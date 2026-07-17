// Manages the bundled OpenCode sidecar so it never interferes with any OpenCode
// the user already has: it runs the *bundled* binary, on a *dedicated free port*,
// with an *app-private* XDG config/data dir, and is killed on app exit.
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

use crate::opencode_config::merge_config;

const SIDECAR_START_TIMEOUT: Duration = Duration::from_secs(300);
const SIDECAR_PROBE_INTERVAL: Duration = Duration::from_millis(75);
const SIDECAR_HTTP_TIMEOUT: Duration = Duration::from_millis(250);
const SIDECAR_POLICY_TIMEOUT: Duration = Duration::from_secs(15);
const SIDECAR_STOP_TIMEOUT: Duration = Duration::from_secs(3);
const SIDECAR_PORT_ATTEMPTS: usize = 3;
const EXPECTED_OPENCODE_VERSION: &str = "1.17.13";
const SIDECAR_START_TIMEOUT_ERROR: &str =
    "OpenCode did not become ready before the startup timeout";
#[cfg(target_os = "macos")]
const SANDBOX_EXEC_PATH: &str = "/usr/bin/sandbox-exec";
#[cfg(any(target_os = "macos", test))]
const MANAGED_SCIENCE_MCP_DIR: &str = "science-mcp-managed";
#[cfg(any(target_os = "macos", test))]
const UV_PYTHON_DIR: &str = "uv-python";

#[derive(Default)]
struct ExitSignal {
    exited: Mutex<bool>,
    wake: Condvar,
}

impl ExitSignal {
    fn mark_exited(&self) {
        *self.exited.lock().unwrap() = true;
        self.wake.notify_all();
    }

    fn wait(&self, timeout: Duration) -> bool {
        let exited = self.exited.lock().unwrap();
        if *exited {
            return true;
        }
        let (exited, _) = self
            .wake
            .wait_timeout_while(exited, timeout, |exited| !*exited)
            .unwrap();
        *exited
    }

    fn is_exited(&self) -> bool {
        *self.exited.lock().unwrap()
    }
}

struct ManagedSidecar {
    /// `CommandChild::kill(self)` consumes the only process handle. Keep the
    /// remaining identity and exit signal after a failed or timed-out request so
    /// no replacement can spawn until the watcher proves this process exited.
    process: Option<CommandChild>,
    pid: u32,
    generation: u64,
    exit: Arc<ExitSignal>,
    expected_exit: Arc<AtomicBool>,
}

struct SpawnedSidecar {
    process: CommandChild,
    events: tauri::async_runtime::Receiver<CommandEvent>,
    pid: u32,
}

struct TerminationFailure<T> {
    retained: T,
    message: String,
}

struct SpawnAttemptError {
    message: String,
    retryable_early_exit: bool,
}

#[derive(Debug, serde::Deserialize)]
struct ResolvedPermissionRule {
    permission: String,
    pattern: String,
    action: String,
}

#[derive(Debug, serde::Deserialize)]
struct ResolvedAgent {
    name: String,
    permission: Vec<ResolvedPermissionRule>,
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeRestartResult {
    runtime_url: Option<String>,
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportOpenCodeLoginResult {
    imported: bool,
    runtime_url: Option<String>,
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConfigureOpenCodeResult {
    path: String,
    runtime_url: Option<String>,
}

impl SpawnAttemptError {
    fn fatal(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            retryable_early_exit: false,
        }
    }
}

impl From<String> for SpawnAttemptError {
    fn from(message: String) -> Self {
        Self::fatal(message)
    }
}

#[derive(Default)]
pub struct RuntimeState {
    /// Serializes every start/restart/stop transition. The frontend may issue
    /// duplicate bootstrap calls (React StrictMode does this in development),
    /// so checking `url` and publishing the spawned child must be one critical
    /// section rather than separate per-field mutex operations.
    lifecycle: Arc<Mutex<()>>,
    /// Protects every app-owned read or write under the OpenCode profile. The
    /// only valid nested order is lifecycle -> config; no code may acquire
    /// lifecycle while holding config.
    config: Mutex<()>,
    child: Arc<Mutex<Option<ManagedSidecar>>>,
    url: Arc<Mutex<Option<String>>>,
    port: Arc<Mutex<Option<u16>>>,
    next_generation: AtomicU64,
    /// Set before exit cleanup waits for lifecycle. An in-flight readiness loop
    /// observes this without taking a lock, kills its unpublished child, and
    /// releases lifecycle promptly; queued starts/restarts refuse to spawn.
    shutting_down: AtomicBool,
}

fn with_lifecycle<T>(lifecycle: &Mutex<()>, operation: impl FnOnce() -> T) -> T {
    let _guard = lifecycle.lock().unwrap();
    operation()
}

/// Start a runtime exactly once across concurrent callers. The successful
/// payload (the child process in production) is published before its URL, so a
/// visible URL always names a fully-owned runtime. Failures publish neither and
/// leave a later call free to retry.
fn start_once<T>(
    lifecycle: &Mutex<()>,
    published_url: &Mutex<Option<String>>,
    is_shutting_down: impl Fn() -> bool,
    start: impl FnOnce() -> Result<(String, T), String>,
    publish: impl FnOnce(T),
    discard: impl FnOnce(T) -> Result<(), String>,
) -> Result<String, String> {
    if is_shutting_down() {
        return Err("runtime is shutting down".into());
    }
    with_lifecycle(lifecycle, || {
        // Shutdown sets its atomic flag before waiting for this mutex, so this
        // second check rejects starts that were already queued behind cleanup.
        if is_shutting_down() {
            return Err("runtime is shutting down".into());
        }
        if let Some(url) = published_url.lock().unwrap().clone() {
            return Ok(url);
        }
        let (url, payload) = start()?;
        // Cover shutdown racing the bounded readiness check's successful final
        // probe. The child is still unpublished here and must be terminated by
        // this caller rather than left for exit cleanup to discover.
        if is_shutting_down() {
            discard(payload)?;
            return Err("runtime is shutting down".into());
        }
        publish(payload);
        *published_url.lock().unwrap() = Some(url.clone());
        Ok(url)
    })
}

/// App-private runtime root, e.g. ~/Library/Application Support/io.github.shawliu998.sparkagent/runtime
fn runtime_root(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("runtime"))
}

/// Mirror tauri-plugin-shell's bundled-sidecar resolution exactly: external
/// binaries are copied beside the starting executable, while Rust test
/// executables live one level lower in `deps`.
#[cfg(any(target_os = "macos", test))]
fn bundled_sidecar_path_from(current_exe: &Path, command: &Path) -> Result<PathBuf, String> {
    let exe_dir = current_exe
        .parent()
        .ok_or_else(|| "the Spark executable has no parent directory".to_string())?;
    let base_dir = if exe_dir.ends_with("deps") {
        exe_dir.parent().unwrap_or(exe_dir)
    } else {
        exe_dir
    };
    let mut command_path = base_dir.join(command);

    #[cfg(windows)]
    {
        if !command_path.extension().is_some_and(|ext| ext == "exe") {
            command_path.as_mut_os_string().push(".exe");
        }
    }
    #[cfg(not(windows))]
    {
        if command_path.extension().is_some_and(|ext| ext == "exe") {
            command_path.set_extension("");
        }
    }

    Ok(command_path)
}

#[cfg(any(target_os = "macos", test))]
fn canonical_sandbox_subdir(runtime: &Path, name: &str) -> Result<PathBuf, String> {
    if !runtime.is_absolute() {
        return Err("the Spark runtime path is not absolute".to_string());
    }
    std::fs::create_dir_all(runtime)
        .map_err(|error| format!("could not create the Spark runtime directory: {error}"))?;
    let canonical_runtime = runtime
        .canonicalize()
        .map_err(|error| format!("could not canonicalize the Spark runtime directory: {error}"))?;
    let path = runtime.join(name);
    match std::fs::symlink_metadata(&path) {
        Ok(metadata) if metadata.file_type().is_dir() && !metadata.file_type().is_symlink() => {}
        Ok(_) => {
            return Err(format!(
                "sandbox root is not a regular directory: {}",
                path.display()
            ))
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            std::fs::create_dir(&path).map_err(|error| {
                format!("could not create sandbox root {}: {error}", path.display())
            })?;
        }
        Err(error) => {
            return Err(format!(
                "could not inspect sandbox root {}: {error}",
                path.display()
            ))
        }
    }
    let canonical = path.canonicalize().map_err(|error| {
        format!(
            "could not canonicalize sandbox root {}: {error}",
            path.display()
        )
    })?;
    if canonical.parent() != Some(canonical_runtime.as_path()) {
        return Err(format!(
            "sandbox root escaped the Spark runtime directory: {}",
            canonical.display()
        ));
    }
    Ok(canonical)
}

#[cfg(any(target_os = "macos", test))]
fn seatbelt_path_literal(path: &Path) -> Result<String, String> {
    let text = path
        .to_str()
        .ok_or_else(|| "sandbox root is not valid UTF-8".to_string())?;
    if text.chars().any(char::is_control) {
        return Err("sandbox root contains a control character".to_string());
    }
    let mut escaped = String::with_capacity(text.len());
    for character in text.chars() {
        match character {
            '\\' => escaped.push_str("\\\\"),
            '"' => escaped.push_str("\\\""),
            _ => escaped.push(character),
        }
    }
    Ok(format!("\"{escaped}\""))
}

/// Keep this OpenCode Seatbelt layer deliberately narrow. Direct paths into a
/// managed connector environment are denied, while the shared uv Python root
/// remains readable/executable but direct writes are denied. This is defense
/// in depth only: ancestor rename/symlink swaps and hard-link aliases remain
/// release blockers, so credential-bearing connector execution stays gated.
#[cfg(any(target_os = "macos", test))]
fn opencode_sandbox_profile(managed_root: &Path, uv_python_root: &Path) -> Result<String, String> {
    let managed_root = seatbelt_path_literal(managed_root)?;
    let uv_python_root = seatbelt_path_literal(uv_python_root)?;
    Ok(format!(
        "(version 1)\n(allow default)\n(deny file-read* (subpath {managed_root}))\n(deny file-write* (subpath {managed_root}))\n(deny file-write* (subpath {uv_python_root}))"
    ))
}

#[cfg(target_os = "macos")]
fn sandboxed_opencode_launch(
    app: &AppHandle,
    runtime: &Path,
) -> Result<tauri_plugin_shell::process::Command, String> {
    let managed_root = canonical_sandbox_subdir(runtime, MANAGED_SCIENCE_MCP_DIR)?;
    let uv_python_root = canonical_sandbox_subdir(runtime, UV_PYTHON_DIR)?;
    let profile = opencode_sandbox_profile(&managed_root, &uv_python_root)?;

    let current_exe = tauri::utils::platform::current_exe()
        .map_err(|error| format!("could not resolve the Spark executable: {error}"))?;
    let opencode = bundled_sidecar_path_from(&current_exe, Path::new("opencode"))?;
    if !opencode.is_absolute() {
        return Err("bundled OpenCode path is not absolute".to_string());
    }
    let metadata = std::fs::symlink_metadata(&opencode)
        .map_err(|error| format!("bundled OpenCode sidecar is unavailable: {error}"))?;
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return Err("bundled OpenCode sidecar is not a regular file".to_string());
    }
    let sandbox_metadata = std::fs::symlink_metadata(SANDBOX_EXEC_PATH)
        .map_err(|error| format!("macOS sandbox-exec is unavailable: {error}"))?;
    if !sandbox_metadata.file_type().is_file() || sandbox_metadata.file_type().is_symlink() {
        return Err("macOS sandbox-exec is not a regular file".to_string());
    }

    // sandbox-exec applies the profile in this process and then execs the exact
    // sidecar path. There is no shell/intermediate child: CommandChild's PID is
    // therefore still the OpenCode PID authorized by the connector broker.
    Ok(app
        .shell()
        .command(SANDBOX_EXEC_PATH)
        .arg("-p")
        .arg(profile)
        .arg(opencode))
}

fn xdg_config_home(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(runtime_root(app)?.join("xdg-config"))
}

/// File recording the user's chosen active workspace folder (absolute path).
fn active_workspace_file(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(runtime_root(app)?.join("active-workspace.txt"))
}

/// File recording the user's chosen BASE folder — the parent every new dated
/// session workspace is created under (Settings → Workspace).
fn base_workspace_file(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(runtime_root(app)?.join("base-workspace.txt"))
}

/// The active workspace folder OpenCode / the kernel / previews / provenance all
/// operate in. Defaults to the base folder (`~/Documents/SparkAgent`) until the
/// user opens or creates another one; the choice persists across restarts.
pub fn workspace_dir(app: &AppHandle) -> Result<PathBuf, String> {
    if let Ok(f) = active_workspace_file(app) {
        if let Ok(s) = std::fs::read_to_string(&f) {
            let dir = PathBuf::from(s.trim());
            if dir.is_dir() {
                return Ok(dir);
            }
        }
    }
    base_workspace_dir(app)
}

/// The workspace root new dated session folders are created under. A folder
/// the user picked in Settings wins; the default is `~/Documents/SparkAgent`
/// (no space — the agent runs shell commands against this path, and unquoted
/// spaces break them), falling back to `$HOME/Documents`.
pub fn base_workspace_dir(app: &AppHandle) -> Result<PathBuf, String> {
    if let Ok(f) = base_workspace_file(app) {
        if let Ok(s) = std::fs::read_to_string(&f) {
            let dir = PathBuf::from(s.trim());
            if dir.is_dir() {
                return Ok(dir);
            }
        }
    }
    let docs = match app.path().document_dir() {
        Ok(d) => d,
        Err(_) => {
            let home = std::env::var("HOME")
                .or_else(|_| std::env::var("USERPROFILE"))
                .map_err(|_| "could not resolve a documents directory".to_string())?;
            PathBuf::from(home).join("Documents")
        }
    };
    let dir = docs.join("SparkAgent");

    // Only migrate the app-private legacy workspace. Do not move an existing
    // Open Science workspace: the two products may coexist, and importing user
    // data must remain an explicit action.
    if !dir.exists() {
        let old = runtime_root(app)?.join("workspace");
        if old.is_dir() && std::fs::rename(&old, &dir).is_err() {
            return Ok(old);
        }
    }
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    Ok(dir)
}

/// Path OpenCode reads when XDG_CONFIG_HOME points at our private dir.
fn opencode_config_file(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(xdg_config_home(app)?.join("opencode").join("opencode.json"))
}

/// OpenCode's app-private auth database. OAuth records remain OpenCode-owned;
/// legacy `type: api` entries are migrated to the system credential manager
/// before the sidecar starts.
fn opencode_auth_file(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(runtime_root(app)?
        .join("xdg-data")
        .join("opencode")
        .join("auth.json"))
}

fn managed_tool_output_permission_pattern(app: &AppHandle) -> Result<String, String> {
    Ok(runtime_root(app)?
        .join("xdg-data")
        .join("opencode")
        .join("tool-output")
        .join("*")
        .to_string_lossy()
        .replace('\\', "/"))
}

/// The config file to edit in place: the server may have rewritten the config
/// as opencode.jsonc — prefer whichever exists, fall back to opencode.json.
fn effective_config_file(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = xdg_config_home(app)?.join("opencode");
    let fallback = opencode_config_file(app)?;
    Ok(["opencode.jsonc", "opencode.json"]
        .iter()
        .map(|n| dir.join(n))
        .find(|p| p.exists())
        .unwrap_or(fallback))
}

/// Effective config first, followed by any other legacy/current filename that
/// exists. Credential scrubbing covers both conservatively because OpenCode
/// installations can retain `opencode.json` beside a newer `opencode.jsonc`.
fn opencode_config_files(app: &AppHandle) -> Result<Vec<PathBuf>, String> {
    let effective = effective_config_file(app)?;
    let dir = xdg_config_home(app)?.join("opencode");
    let mut paths = vec![effective.clone()];
    for name in ["opencode.jsonc", "opencode.json"] {
        let path = dir.join(name);
        if path != effective && path.exists() {
            paths.push(path);
        }
    }
    Ok(paths)
}

/// Remove only the retired, exact Spark-owned Jupyter MCP registration from
/// every live config filename before OpenCode can read it. The retired entry
/// serialized the persistent Jupyter token; custom same-name entries remain
/// user-owned and are never rewritten here.
fn reconcile_jupyter_config_files(app: &AppHandle) -> Result<(), String> {
    // Credential/artifact reconciliation is unconditional. A missing or
    // user-owned MCP entry must never let direct native start_runtime bypass
    // rotation of legacy plaintext Jupyter state.
    crate::jupyter::reconcile_jupyter_security(app)?;
    for path in opencode_config_files(app)? {
        let existing = read_optional_config(&path)?;
        if let Some(updated) = crate::jupyter::reconcile_jupyter_mcp_config(app, &existing)? {
            write_private_atomic(&path, updated.as_bytes())?;
        }
    }
    Ok(())
}

fn managed_science_connector_commands(
    app: &AppHandle,
) -> Result<std::collections::BTreeMap<String, Vec<String>>, String> {
    crate::credential::managed_connector_ids()
        .map(|connector_id| {
            Ok((
                connector_id.to_string(),
                crate::science_mcp::managed_connector_command(app, connector_id)?,
            ))
        })
        .collect()
}

fn previous_managed_science_connector_commands(
    app: &AppHandle,
) -> Result<std::collections::BTreeMap<String, Vec<String>>, String> {
    crate::credential::managed_connector_ids()
        .map(|connector_id| {
            Ok((
                connector_id.to_string(),
                crate::science_mcp::managed_connector_target_command(app, connector_id)?,
            ))
        })
        .collect()
}

fn legacy_managed_science_connector_commands(
    app: &AppHandle,
) -> Result<std::collections::BTreeMap<String, Vec<String>>, String> {
    crate::credential::managed_connector_ids()
        .map(|connector_id| {
            Ok((
                connector_id.to_string(),
                crate::science_mcp::legacy_managed_connector_command(app, connector_id)?,
            ))
        })
        .collect()
}

/// Read an optional config file without confusing a real I/O failure with a
/// first-run missing file. Callers may safely seed defaults only for NotFound.
fn read_optional_config(path: &Path) -> Result<String, String> {
    match std::fs::read_to_string(path) {
        Ok(text) => Ok(text),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(String::new()),
        Err(error) => Err(format!("failed to read {}: {error}", path.display())),
    }
}

const MANAGED_PROFILE_REGISTRY_VERSION: u32 = 2;
const MANAGED_PROFILE_REGISTRY_FILE: &str = ".spark-agent-managed.json";

/// Fingerprints in this app-private registry are the ownership proof required
/// before Spark may replace or remove a global agent or skill. A same-name
/// entry with different content is user-owned and always wins.
#[derive(Debug, serde::Deserialize, serde::Serialize)]
#[serde(deny_unknown_fields)]
struct ManagedProfileRegistry {
    version: u32,
    #[serde(default)]
    agents: std::collections::BTreeMap<String, String>,
    #[serde(default)]
    skills: std::collections::BTreeMap<String, String>,
    /// True only when no fingerprint registry existed (including a validated
    /// legacy v1 file). Never serialized: once v2 is written, unregistered
    /// collisions remain user-owned even when they happen to match a bundle.
    #[serde(skip)]
    allow_exact_adoption: bool,
}

impl Default for ManagedProfileRegistry {
    fn default() -> Self {
        Self {
            version: MANAGED_PROFILE_REGISTRY_VERSION,
            agents: std::collections::BTreeMap::new(),
            skills: std::collections::BTreeMap::new(),
            allow_exact_adoption: true,
        }
    }
}

/// Version 1 tracked names only. It cannot prove that the current object was
/// not edited after deployment, so loading it intentionally drops ownership.
/// A byte-identical bundled target can then be safely re-adopted by the normal
/// unregistered-target path; anything else remains user-owned.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacyManagedProfileRegistry {
    version: u32,
    #[serde(default)]
    agents: std::collections::BTreeSet<String>,
    #[serde(default)]
    skills: std::collections::BTreeSet<String>,
}

fn managed_profile_registry_file(profile_root: &Path) -> PathBuf {
    profile_root.join(MANAGED_PROFILE_REGISTRY_FILE)
}

fn valid_profile_component(name: &str, suffix: Option<&str>) -> bool {
    if name.is_empty()
        || name.starts_with('.')
        || suffix.is_some_and(|suffix| !name.ends_with(suffix))
    {
        return false;
    }
    let mut components = Path::new(name).components();
    matches!(components.next(), Some(std::path::Component::Normal(_)))
        && components.next().is_none()
}

fn valid_content_fingerprint(fingerprint: &str) -> bool {
    fingerprint.strip_prefix("sha256:").is_some_and(|digest| {
        digest.len() == 64
            && digest
                .bytes()
                .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
    })
}

fn validate_legacy_registry(registry: &LegacyManagedProfileRegistry) -> Result<(), String> {
    if let Some(name) = registry
        .agents
        .iter()
        .find(|name| !valid_profile_component(name, Some(".md")))
    {
        return Err(format!("unsafe managed agent name in registry: {name}"));
    }
    if let Some(name) = registry
        .skills
        .iter()
        .find(|name| !valid_profile_component(name, None))
    {
        return Err(format!("unsafe managed skill name in registry: {name}"));
    }
    Ok(())
}

fn load_managed_profile_registry(profile_root: &Path) -> Result<ManagedProfileRegistry, String> {
    let path = managed_profile_registry_file(profile_root);
    let text = match std::fs::read_to_string(&path) {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(ManagedProfileRegistry::default())
        }
        Err(error) => return Err(format!("failed to read {}: {error}", path.display())),
    };
    let value: serde_json::Value = serde_json::from_str(&text)
        .map_err(|error| format!("invalid Spark managed-profile registry: {error}"))?;
    let version = value
        .get("version")
        .and_then(serde_json::Value::as_u64)
        .ok_or_else(|| "invalid Spark managed-profile registry version".to_string())?;
    if version == 1 {
        let legacy: LegacyManagedProfileRegistry = serde_json::from_value(value)
            .map_err(|error| format!("invalid legacy Spark managed-profile registry: {error}"))?;
        validate_legacy_registry(&legacy)?;
        debug_assert_eq!(legacy.version, 1);
        return Ok(ManagedProfileRegistry::default());
    }
    if version != u64::from(MANAGED_PROFILE_REGISTRY_VERSION) {
        return Err(format!(
            "unsupported Spark managed-profile registry version {version}"
        ));
    }
    let registry: ManagedProfileRegistry = serde_json::from_value(value)
        .map_err(|error| format!("invalid Spark managed-profile registry: {error}"))?;
    if let Some(name) = registry
        .agents
        .keys()
        .find(|name| !valid_profile_component(name, Some(".md")))
    {
        return Err(format!("unsafe managed agent name in registry: {name}"));
    }
    if let Some(name) = registry
        .skills
        .keys()
        .find(|name| !valid_profile_component(name, None))
    {
        return Err(format!("unsafe managed skill name in registry: {name}"));
    }
    if let Some(fingerprint) = registry
        .agents
        .values()
        .chain(registry.skills.values())
        .find(|fingerprint| !valid_content_fingerprint(fingerprint))
    {
        return Err(format!(
            "invalid managed-profile content fingerprint: {fingerprint}"
        ));
    }
    Ok(registry)
}

fn write_managed_profile_registry(
    profile_root: &Path,
    registry: &ManagedProfileRegistry,
) -> Result<(), String> {
    let text = serde_json::to_string_pretty(registry).map_err(|error| error.to_string())?;
    write_private_atomic(
        &managed_profile_registry_file(profile_root),
        text.as_bytes(),
    )
}

fn fingerprint_record(hasher: &mut sha2::Sha256, kind: &[u8], name: &[u8], contents: &[u8]) {
    use sha2::Digest;
    hasher.update((kind.len() as u64).to_be_bytes());
    hasher.update(kind);
    hasher.update((name.len() as u64).to_be_bytes());
    hasher.update(name);
    hasher.update((contents.len() as u64).to_be_bytes());
    hasher.update(contents);
}

fn finish_content_fingerprint(hasher: sha2::Sha256) -> String {
    use sha2::Digest;
    format!("sha256:{:x}", hasher.finalize())
}

fn fingerprint_agent_file(path: &Path) -> std::io::Result<String> {
    use sha2::Digest;
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.file_type().is_file() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("agent is a link or special file: {}", path.display()),
        ));
    }
    let contents = std::fs::read(path)?;
    let mut hasher = sha2::Sha256::new();
    hasher.update(b"spark-agent-managed-agent-v1\0");
    fingerprint_record(&mut hasher, b"file", b"", &contents);
    Ok(finish_content_fingerprint(hasher))
}

fn fingerprint_skill_dir(path: &Path) -> std::io::Result<String> {
    use sha2::Digest;
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.file_type().is_dir() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("skill is a link or special file: {}", path.display()),
        ));
    }
    let mut hasher = sha2::Sha256::new();
    hasher.update(b"spark-agent-managed-skill-v1\0");
    fingerprint_skill_entries(path, "", &mut hasher)?;
    Ok(finish_content_fingerprint(hasher))
}

fn fingerprint_skill_entries(
    path: &Path,
    relative: &str,
    hasher: &mut sha2::Sha256,
) -> std::io::Result<()> {
    let mut entries = std::fs::read_dir(path)?.collect::<Result<Vec<_>, _>>()?;
    entries.sort_by_key(std::fs::DirEntry::file_name);
    for entry in entries {
        let file_type = entry.file_type()?;
        if is_python_cache_entry(&entry.path(), file_type.is_dir()) {
            continue;
        }
        let name = entry.file_name().into_string().map_err(|_| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("skill path is not UTF-8: {}", entry.path().display()),
            )
        })?;
        let entry_relative = if relative.is_empty() {
            name
        } else {
            format!("{relative}/{name}")
        };
        if file_type.is_dir() {
            fingerprint_record(hasher, b"dir", entry_relative.as_bytes(), b"");
            fingerprint_skill_entries(&entry.path(), &entry_relative, hasher)?;
        } else if file_type.is_file() {
            let contents = std::fs::read(entry.path())?;
            fingerprint_record(hasher, b"file", entry_relative.as_bytes(), &contents);
        } else {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!(
                    "skill contains a link or special resource: {}",
                    entry.path().display()
                ),
            ));
        }
    }
    Ok(())
}

fn parse_yaml_scalar(raw: &str) -> Option<String> {
    let raw = raw.trim();
    if raw.starts_with('"') {
        return serde_json::from_str::<String>(raw).ok();
    }
    if let Some(inner) = raw
        .strip_prefix('\'')
        .and_then(|value| value.strip_suffix('\''))
    {
        return Some(inner.replace("''", "'"));
    }
    let raw = raw
        .find(" #")
        .map_or(raw, |comment_start| &raw[..comment_start])
        .trim();
    (!raw.is_empty()
        && !matches!(
            raw.as_bytes().first(),
            Some(b'[' | b'{' | b'&' | b'*' | b'!' | b'|' | b'>')
        ))
    .then(|| raw.to_string())
}

fn valid_skill_manifest_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 64
        && name.split('-').all(|part| {
            !part.is_empty()
                && part
                    .bytes()
                    .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit())
        })
}

/// Return a valid Agent Skills frontmatter name. Invalid manifests are ignored
/// exactly like non-skill Markdown; links and special manifests are rejected so
/// collision discovery never traverses an untrusted target.
fn skill_manifest_name(path: &Path) -> std::io::Result<Option<String>> {
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.file_type().is_file() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!(
                "skill manifest is a link or special file: {}",
                path.display()
            ),
        ));
    }
    let text = match std::fs::read_to_string(path) {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::InvalidData => return Ok(None),
        Err(error) => return Err(error),
    };
    let mut lines = text.lines();
    if lines.next().map(str::trim) != Some("---") {
        return Ok(None);
    }
    let mut name = None;
    let mut has_description = false;
    let mut closed = false;
    for line in lines {
        let trimmed = line.trim();
        if trimmed == "---" {
            closed = true;
            break;
        }
        if trimmed.is_empty() || trimmed.starts_with('#') || line.starts_with([' ', '\t']) {
            continue;
        }
        let Some((key, raw)) = line.split_once(':') else {
            continue;
        };
        match key.trim() {
            "name" => {
                if name.is_some() {
                    return Ok(None);
                }
                name = parse_yaml_scalar(raw);
            }
            "description" => has_description = !raw.trim().is_empty(),
            _ => {}
        }
    }
    let Some(name) = name else {
        return Ok(None);
    };
    Ok((closed && has_description && valid_skill_manifest_name(&name)).then_some(name))
}

fn discover_skill_names_in_root(
    root: &Path,
    excluded_roots: &std::collections::BTreeSet<PathBuf>,
    ignored_manifest_parent: Option<&Path>,
    names: &mut std::collections::BTreeSet<String>,
) -> std::io::Result<()> {
    if excluded_roots.contains(root) {
        return Ok(());
    }
    let metadata = match std::fs::symlink_metadata(root) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    if !metadata.file_type().is_dir() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!(
                "skill search root is a link or special file: {}",
                root.display()
            ),
        ));
    }
    let mut entries = std::fs::read_dir(root)?.collect::<Result<Vec<_>, _>>()?;
    entries.sort_by_key(std::fs::DirEntry::file_name);
    for entry in entries {
        let path = entry.path();
        if excluded_roots.contains(&path) {
            continue;
        }
        let file_type = entry.file_type()?;
        if file_type.is_dir() {
            discover_skill_names_in_root(&path, excluded_roots, ignored_manifest_parent, names)?;
        } else if file_type.is_file() {
            if entry.file_name() == std::ffi::OsStr::new("SKILL.md") {
                if let Some(name) = skill_manifest_name(&path)? {
                    let is_direct_destination = path.parent().and_then(Path::parent)
                        == ignored_manifest_parent
                        && path
                            .parent()
                            .and_then(Path::file_name)
                            .and_then(std::ffi::OsStr::to_str)
                            == Some(name.as_str());
                    if !is_direct_destination {
                        names.insert(name);
                    }
                }
            }
        } else {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!(
                    "skill search path contains a link or special file: {}",
                    path.display()
                ),
            ));
        }
    }
    Ok(())
}

/// Discover user/project skill names that must take precedence over bundled
/// global definitions. Only a still-byte-identical managed destination is
/// excluded; modified former targets participate as user-owned definitions.
fn discover_user_skill_names(
    workspace: &Path,
    profile_root: &Path,
    managed: &std::collections::BTreeMap<String, String>,
) -> std::io::Result<std::collections::BTreeSet<String>> {
    let global_skills = profile_root.join("skills");
    let mut excluded = std::collections::BTreeSet::new();
    for (name, recorded_fingerprint) in managed {
        let target = global_skills.join(name);
        if fingerprint_skill_dir(&target).ok().as_ref() == Some(recorded_fingerprint) {
            excluded.insert(target);
        }
    }

    let mut names = std::collections::BTreeSet::new();
    for root in [
        workspace.join(".opencode").join("skill"),
        workspace.join(".opencode").join("skills"),
        profile_root.join("skill"),
    ] {
        discover_skill_names_in_root(&root, &excluded, None, &mut names)?;
    }
    // A direct `<global>/skills/<name>/SKILL.md` is the deployment target
    // itself: sync_managed_skill_pack handles that collision atomically. Still
    // inspect nested manifests inside an unregistered directory, since those
    // are separate user-global definitions OpenCode also discovers.
    discover_skill_names_in_root(&global_skills, &excluded, Some(&global_skills), &mut names)?;
    Ok(names)
}

/// The user's existing OpenCode auth file (their login / free credits), if any.
/// Explicit import sanitizes API keys into the system credential manager and
/// copies only OAuth/non-API state; the user's source file is never modified.
fn user_auth_source() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(xdg) = std::env::var("XDG_DATA_HOME") {
        if !xdg.is_empty() {
            candidates.push(PathBuf::from(xdg).join("opencode").join("auth.json"));
        }
    }
    if let Ok(home) = std::env::var("HOME") {
        candidates.push(PathBuf::from(&home).join(".local/share/opencode/auth.json"));
    }
    if let Ok(appdata) = std::env::var("APPDATA") {
        candidates.push(PathBuf::from(appdata).join("opencode").join("auth.json"));
    }
    candidates.into_iter().find(|p| p.exists())
}

/// Explicitly import the user's OpenCode CLI login from Settings. API keys are
/// parsed in memory and moved directly to the system credential manager; the
/// app-private auth file receives only sanitized OAuth/non-API records.
#[tauri::command(async)]
pub fn import_opencode_login(
    app: AppHandle,
    state: State<'_, RuntimeState>,
) -> Result<ImportOpenCodeLoginResult, String> {
    let Some(src) = user_auth_source() else {
        return Ok(ImportOpenCodeLoginResult {
            imported: false,
            runtime_url: None,
        });
    };
    let (imported, runtime_url) = with_config_transaction(&app, &state, || {
        let dst = opencode_auth_file(&app)?;
        let contents = std::fs::read_to_string(&src)
            .map_err(|e| format!("could not read the OpenCode login for import: {e}"))?;
        crate::credential::import_auth_secure(
            &crate::credential::SystemCredentialStore,
            &opencode_config_files(&app)?,
            &dst,
            &contents,
            &write_private_atomic,
        )?;
        Ok(true)
    })?;
    Ok(ImportOpenCodeLoginResult {
        imported,
        runtime_url,
    })
}

/// Deploy the bundled skill packs (Tauri resources) into the app-private
/// profile's global skills dir (`<xdg-config>/opencode/skills/`), which OpenCode
/// scans regardless of project detection: `skills/` is the external ai4s-skills
/// pack and `skills-core/` contains the product-owned core skills. The
/// workspace's own `.opencode/skills/` stays reserved for skills the user
/// installs. Runs before every sidecar start so app upgrades refresh the packs.
fn deploy_bundled_skills(
    app: &AppHandle,
    profile_root: &Path,
    registry: &mut ManagedProfileRegistry,
) -> Result<(), String> {
    let dst = profile_root.join("skills");
    let previous = registry.skills.clone();
    let user_skill_names = discover_user_skill_names(&workspace_dir(app)?, profile_root, &previous)
        .map_err(|error| format!("failed to inspect user/project skills: {error}"))?;
    let mut ownership = previous.clone();
    let mut managed = std::collections::BTreeMap::new();
    let mut bundled = std::collections::BTreeSet::new();
    let mut all_resources_available = true;
    let allow_exact_adoption = registry.allow_exact_adoption;
    for resource in ["skills", "skills-core"] {
        let src = match app
            .path()
            .resolve(resource, tauri::path::BaseDirectory::Resource)
        {
            Ok(p) if p.is_dir() => p,
            _ if cfg!(debug_assertions) => {
                // `tauri dev` may intentionally run before `fetch-skills.sh`.
                // Keep that workflow usable, but never prune from an incomplete
                // bundled-name set.
                all_resources_available = false;
                continue;
            }
            Ok(path) => {
                return Err(format!(
                    "bundled skill resource is not a directory ({resource}): {}",
                    path.display()
                ));
            }
            Err(error) => {
                return Err(format!(
                    "failed to resolve bundled skill resource ({resource}): {error}"
                ));
            }
        };
        let (names, installed) = sync_managed_skill_pack(
            &src,
            &dst,
            &mut ownership,
            allow_exact_adoption,
            &user_skill_names,
        )
        .map_err(|error| format!("failed to deploy bundled skills ({resource}): {error}"))?;
        all_resources_available &=
            register_skill_pack_names(resource, names, cfg!(debug_assertions), &mut bundled)?;
        managed.extend(installed);
    }
    // Prune only names recorded as Spark-managed by a prior successful deploy.
    // Unknown global skills (including a same-name collision on first install)
    // are user-owned and must remain untouched. With an incomplete debug pack,
    // retain the old registry and defer stale cleanup to a complete deployment.
    if all_resources_available {
        prune_stale_skills(&dst, &previous, &managed)
            .map_err(|error| format!("failed to prune stale bundled skills: {error}"))?;
    } else {
        managed.extend(ownership);
    }
    registry.skills = managed;
    Ok(())
}

/// Record one pack's deployed names. An existing-but-empty resource is the
/// same incomplete fetch state as a missing resource: debug keeps prior skills
/// and skips pruning, while release fails closed instead of shipping an empty
/// external or core pack.
fn register_skill_pack_names(
    resource: &str,
    names: Vec<String>,
    allow_incomplete: bool,
    bundled: &mut std::collections::BTreeSet<String>,
) -> Result<bool, String> {
    if names.is_empty() {
        if allow_incomplete {
            return Ok(false);
        }
        return Err(format!(
            "bundled skill resource contains no deployable skills ({resource})"
        ));
    }
    bundled.extend(names);
    Ok(true)
}

/// Remove only stale names from the last successful Spark registry, plus their
/// exact app-owned recovery artifacts. Unregistered global skills are user data.
fn prune_stale_skills(
    dst: &Path,
    previous: &std::collections::BTreeMap<String, String>,
    current: &std::collections::BTreeMap<String, String>,
) -> std::io::Result<()> {
    let mut removed = std::collections::BTreeSet::new();
    for (name, recorded_fingerprint) in previous {
        if current.contains_key(name) {
            continue;
        }
        let target = dst.join(name);
        let fingerprint = match fingerprint_skill_dir(&target) {
            Ok(fingerprint) => fingerprint,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            // Modified trees, top-level links, and special files are no longer
            // ours. Relinquish ownership without touching them.
            Err(_) => continue,
        };
        if fingerprint == *recorded_fingerprint {
            remove_app_managed_path_without_following(&target)?;
            removed.insert(name.clone());
        }
    }
    for entry in std::fs::read_dir(dst)? {
        let entry = entry?;
        let path = entry.path();
        let entry_name = entry.file_name();
        if let Some((skill_name, _)) = parse_skill_recovery_artifact(&entry_name) {
            if removed.contains(skill_name) {
                remove_app_managed_path_without_following(&path)?;
            }
        }
    }
    Ok(())
}

/// Deploy a skill pack without taking ownership of an existing unregistered
/// same-name directory. The two returned collections are respectively all
/// valid bundled names (for completeness checks) and names Spark now manages.
fn sync_managed_skill_pack(
    src: &Path,
    dst: &Path,
    ownership: &mut std::collections::BTreeMap<String, String>,
    allow_exact_adoption: bool,
    user_skill_names: &std::collections::BTreeSet<String>,
) -> std::io::Result<(Vec<String>, std::collections::BTreeMap<String, String>)> {
    std::fs::create_dir_all(dst)?;
    let mut bundled = Vec::new();
    let mut managed = std::collections::BTreeMap::new();
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() || !entry.path().join("SKILL.md").is_file() {
            continue;
        }
        let name = entry.file_name().into_string().map_err(|_| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "bundled skill name must be UTF-8",
            )
        })?;
        if !valid_profile_component(&name, None) {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("unsafe bundled skill name: {name}"),
            ));
        }
        bundled.push(name.clone());
        if user_skill_names.contains(&name) {
            ownership.remove(&name);
            continue;
        }
        let bundled_fingerprint = fingerprint_skill_dir(&entry.path())?;
        let target = dst.join(&name);
        if path_exists_without_following(&target)? {
            let target_fingerprint = match fingerprint_skill_dir(&target) {
                Ok(fingerprint) => fingerprint,
                Err(_) => {
                    ownership.remove(&name);
                    continue;
                }
            };
            match ownership.get(&name) {
                Some(recorded_fingerprint) if target_fingerprint == *recorded_fingerprint => {
                    if target_fingerprint != bundled_fingerprint {
                        replace_skill_dir(&entry.path(), &target)?;
                    }
                }
                Some(_) => {
                    // A user edited a formerly managed skill. Forget ownership
                    // immediately and never overwrite the divergent tree.
                    ownership.remove(&name);
                    continue;
                }
                None if allow_exact_adoption && target_fingerprint == bundled_fingerprint => {
                    // Exact adoption recovers a first-deploy/registry-write
                    // crash and upgrades the legacy name-only registry safely.
                }
                None => continue,
            }
        } else {
            replace_skill_dir(&entry.path(), &target)?;
        }
        let installed_fingerprint = fingerprint_skill_dir(&target)?;
        if installed_fingerprint != bundled_fingerprint {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("deployed skill fingerprint mismatch: {}", target.display()),
            ));
        }
        ownership.insert(name.clone(), installed_fingerprint.clone());
        managed.insert(name, installed_fingerprint);
    }
    Ok((bundled, managed))
}

fn sync_managed_agent_pack(
    src: &Path,
    dst: &Path,
    ownership: &mut std::collections::BTreeMap<String, String>,
    allow_exact_adoption: bool,
) -> Result<(Vec<String>, std::collections::BTreeMap<String, String>), String> {
    std::fs::create_dir_all(dst).map_err(|error| error.to_string())?;
    let mut bundled = Vec::new();
    let mut managed = std::collections::BTreeMap::new();
    for entry in std::fs::read_dir(src).map_err(|error| error.to_string())? {
        let entry = entry.map_err(|error| error.to_string())?;
        let file_type = entry.file_type().map_err(|error| error.to_string())?;
        if entry.path().extension() != Some(std::ffi::OsStr::new("md")) {
            continue;
        }
        if !file_type.is_file() {
            return Err(format!(
                "bundled agent is a link or special file: {}",
                entry.path().display()
            ));
        }
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| "bundled agent name must be UTF-8".to_string())?;
        if !valid_profile_component(&name, Some(".md")) {
            return Err(format!("unsafe bundled agent name: {name}"));
        }
        bundled.push(name.clone());
        let bundled_fingerprint = fingerprint_agent_file(&entry.path())
            .map_err(|error| format!("failed to fingerprint bundled agent {name}: {error}"))?;
        let target = dst.join(&name);
        if path_exists_without_following(&target).map_err(|error| error.to_string())? {
            let target_fingerprint = match fingerprint_agent_file(&target) {
                Ok(fingerprint) => fingerprint,
                Err(_) => {
                    ownership.remove(&name);
                    continue;
                }
            };
            match ownership.get(&name) {
                Some(recorded_fingerprint) if target_fingerprint == *recorded_fingerprint => {
                    if target_fingerprint != bundled_fingerprint {
                        let contents =
                            std::fs::read(entry.path()).map_err(|error| error.to_string())?;
                        write_private_atomic(&target, &contents)?;
                    }
                }
                Some(_) => {
                    ownership.remove(&name);
                    continue;
                }
                None if allow_exact_adoption && target_fingerprint == bundled_fingerprint => {
                    // Safe exact-content adoption; no write is needed.
                }
                None => continue,
            }
        } else {
            let contents = std::fs::read(entry.path()).map_err(|error| error.to_string())?;
            write_private_atomic(&target, &contents)?;
        }
        let installed_fingerprint = fingerprint_agent_file(&target)
            .map_err(|error| format!("failed to fingerprint deployed agent {name}: {error}"))?;
        if installed_fingerprint != bundled_fingerprint {
            return Err(format!(
                "deployed agent fingerprint mismatch: {}",
                target.display()
            ));
        }
        ownership.insert(name.clone(), installed_fingerprint.clone());
        managed.insert(name, installed_fingerprint);
    }
    Ok((bundled, managed))
}

fn deploy_bundled_agents(
    app: &AppHandle,
    profile_root: &Path,
    registry: &mut ManagedProfileRegistry,
) -> Result<(), String> {
    let resource = "opencode-profile/agents";
    let src = match app
        .path()
        .resolve(resource, tauri::path::BaseDirectory::Resource)
    {
        Ok(path) if path.is_dir() => path,
        _ if cfg!(debug_assertions) => return Ok(()),
        Ok(path) => {
            return Err(format!(
                "bundled agent resource is not a directory: {}",
                path.display()
            ))
        }
        Err(error) => return Err(format!("failed to resolve bundled agents: {error}")),
    };
    let previous = registry.agents.clone();
    let mut ownership = previous.clone();
    let (bundled, managed) = sync_managed_agent_pack(
        &src,
        &profile_root.join("agents"),
        &mut ownership,
        registry.allow_exact_adoption,
    )
    .map_err(|error| format!("failed to deploy bundled agents: {error}"))?;
    if bundled.is_empty() {
        if cfg!(debug_assertions) {
            return Ok(());
        }
        return Err("bundled agent resource contains no Markdown agents".to_string());
    }
    prune_stale_agents(&profile_root.join("agents"), &previous, &managed)
        .map_err(|error| format!("failed to prune stale managed agents: {error}"))?;
    registry.agents = managed;
    Ok(())
}

fn prune_stale_agents(
    dst: &Path,
    previous: &std::collections::BTreeMap<String, String>,
    current: &std::collections::BTreeMap<String, String>,
) -> std::io::Result<()> {
    for (name, recorded_fingerprint) in previous {
        if current.contains_key(name) {
            continue;
        }
        let target = dst.join(name);
        let fingerprint = match fingerprint_agent_file(&target) {
            Ok(fingerprint) => fingerprint,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(_) => continue,
        };
        if fingerprint == *recorded_fingerprint {
            remove_app_managed_path_without_following(&target)?;
        }
    }
    Ok(())
}

fn bundled_profile_template(app: &AppHandle) -> Result<String, String> {
    let resource = "opencode-profile/opencode.json";
    match app
        .path()
        .resolve(resource, tauri::path::BaseDirectory::Resource)
    {
        Ok(path) if path.is_file() => std::fs::read_to_string(&path)
            .map_err(|error| format!("failed to read bundled OpenCode profile: {error}")),
        _ if cfg!(debug_assertions) => {
            Ok(include_str!("../../../../runtime/opencode-profile/opencode.json").to_string())
        }
        Ok(path) => Err(format!(
            "bundled OpenCode profile is not a file: {}",
            path.display()
        )),
        Err(error) => Err(format!(
            "failed to resolve bundled OpenCode profile: {error}"
        )),
    }
}

/// Refresh only Spark-owned global profile entries and merge missing base
/// defaults into the app-private OpenCode config. Project `.opencode` content
/// is never inspected or modified here and remains OpenCode-owned.
fn deploy_bundled_profile(app: &AppHandle, config_file: &Path) -> Result<(), String> {
    let profile_root = xdg_config_home(app)?.join("opencode");
    std::fs::create_dir_all(&profile_root).map_err(|error| error.to_string())?;

    // Validate and merge the template before mutating deployed entries.
    let template = bundled_profile_template(app)?;
    let existing = read_optional_config(config_file)?;
    let merged = crate::opencode_config::merge_profile_defaults(&existing, &template)?;

    let mut registry = load_managed_profile_registry(&profile_root)?;
    deploy_bundled_skills(app, &profile_root, &mut registry)?;
    deploy_bundled_agents(app, &profile_root, &mut registry)?;
    write_managed_profile_registry(&profile_root, &registry)?;
    if merged != existing {
        write_private_atomic(config_file, merged.as_bytes())?;
    }
    Ok(())
}

/// Re-evaluate only the workspace-sensitive bundled skill set. This runs on an
/// active-workspace switch before the frontend asks OpenCode for that
/// directory's instance, avoiding duplicate global/project names without
/// restarting the shared sidecar.
fn reconcile_bundled_skills(app: &AppHandle) -> Result<(), String> {
    let profile_root = xdg_config_home(app)?.join("opencode");
    std::fs::create_dir_all(&profile_root).map_err(|error| error.to_string())?;
    let mut registry = load_managed_profile_registry(&profile_root)?;
    deploy_bundled_skills(app, &profile_root, &mut registry)?;
    write_managed_profile_registry(&profile_root, &registry)
}

/// Copy every skill directory under `src` into `dst`, replacing same-named
/// directories (so bundled updates win) and leaving everything else in `dst`
/// alone. Each replacement is staged beside its destination and rolled back on
/// failure, so an interrupted app upgrade never leaves a half-copied skill.
/// Returns the deployed names for stale pruning. Directories without SKILL.md
/// (placeholders) are skipped.
#[cfg(test)]
fn sync_skill_pack(src: &Path, dst: &Path) -> std::io::Result<Vec<std::ffi::OsString>> {
    std::fs::create_dir_all(dst)?;
    let mut deployed = Vec::new();
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() || !entry.path().join("SKILL.md").is_file() {
            continue;
        }
        let target = dst.join(entry.file_name());
        replace_skill_dir(&entry.path(), &target)?;
        deployed.push(entry.file_name());
    }
    Ok(deployed)
}

fn replace_skill_dir(src: &Path, target: &Path) -> std::io::Result<()> {
    let parent = target.parent().ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "skill destination has no parent",
        )
    })?;
    let name = target
        .file_name()
        .and_then(|name| name.to_str())
        .filter(|name| !name.is_empty())
        .ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "skill destination name must be non-empty UTF-8",
            )
        })?;

    // A process can die after publishing either side of the two-rename
    // replacement. Recover those exact, app-owned artifacts before starting a
    // new attempt. Tauri single-instance plus the runtime lifecycle/config
    // transaction makes this a single-writer operation.
    recover_skill_replacement(target)?;

    let suffix = random_hex(8);
    let staging = parent.join(format!(".{name}.{suffix}.staging"));
    let backup = parent.join(format!(".{name}.{suffix}.backup"));
    std::fs::create_dir(&staging)?;
    if let Err(error) = copy_dir(src, &staging) {
        let _ = std::fs::remove_dir_all(&staging);
        return Err(error);
    }

    let had_previous = path_exists_without_following(target)?;
    if had_previous {
        if let Err(error) = std::fs::rename(target, &backup) {
            let _ = std::fs::remove_dir_all(&staging);
            return Err(error);
        }
    }
    if let Err(install_error) = std::fs::rename(&staging, target) {
        let restore_error = had_previous
            .then(|| std::fs::rename(&backup, target).err())
            .flatten();
        let _ = std::fs::remove_dir_all(&staging);
        return match restore_error {
            Some(restore_error) => Err(std::io::Error::new(
                restore_error.kind(),
                format!(
                    "skill install failed ({install_error}); rollback failed ({restore_error}); backup retained at {}",
                    backup.display()
                ),
            )),
            None => Err(install_error),
        };
    }
    // Re-validate the published tree and clean both this attempt's backup and
    // any retained invalid backup that a trusted bundled source just repaired.
    recover_skill_replacement(target)
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum SkillRecoveryArtifact {
    Staging,
    Backup,
}

/// Recognize only names emitted by `replace_skill_dir`. Similar-looking or
/// malformed hidden entries are user data and must remain untouched.
fn skill_recovery_artifact(
    entry_name: &std::ffi::OsStr,
    skill_name: &str,
) -> Option<SkillRecoveryArtifact> {
    let (artifact_skill, artifact) = parse_skill_recovery_artifact(entry_name)?;
    (artifact_skill == skill_name).then_some(artifact)
}

fn parse_skill_recovery_artifact(
    entry_name: &std::ffi::OsStr,
) -> Option<(&str, SkillRecoveryArtifact)> {
    let entry_name = entry_name.to_str()?;
    let remainder = entry_name.strip_prefix('.')?;
    let (stem, kind) = if let Some(stem) = remainder.strip_suffix(".staging") {
        (stem, SkillRecoveryArtifact::Staging)
    } else {
        (
            remainder.strip_suffix(".backup")?,
            SkillRecoveryArtifact::Backup,
        )
    };
    let (skill_name, token) = stem.rsplit_once('.')?;
    (token.len() == 16
        && !skill_name.is_empty()
        && token
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')))
    .then_some((skill_name, kind))
}

fn path_exists_without_following(path: &Path) -> std::io::Result<bool> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => Ok(true),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error),
    }
}

/// Remove an app-managed skill or recovery path without ever traversing a
/// top-level symlink. `remove_dir_all` itself does not follow nested directory
/// symlinks.
fn remove_app_managed_path_without_following(path: &Path) -> std::io::Result<()> {
    let metadata = match std::fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    if metadata.file_type().is_dir() {
        std::fs::remove_dir_all(path)
    } else {
        std::fs::remove_file(path)
    }
}

/// A backup is recoverable only when it is a complete, self-contained skill
/// tree. Refuse links and special resources instead of restoring an object that
/// later deployment code would not accept.
fn validate_complete_skill_dir(path: &Path, require_manifest: bool) -> std::io::Result<()> {
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.file_type().is_dir() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("skill directory is not a directory: {}", path.display()),
        ));
    }
    if require_manifest {
        let manifest = std::fs::symlink_metadata(path.join("SKILL.md")).map_err(|error| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!(
                    "skill directory has no regular SKILL.md ({}): {error}",
                    path.display()
                ),
            )
        })?;
        if !manifest.file_type().is_file() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!(
                    "skill directory has no regular SKILL.md: {}",
                    path.display()
                ),
            ));
        }
    }
    for entry in std::fs::read_dir(path)? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        if file_type.is_dir() {
            validate_complete_skill_dir(&entry.path(), false)?;
        } else if !file_type.is_file() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!(
                    "skill directory contains a link or special resource: {}",
                    entry.path().display()
                ),
            ));
        }
    }
    Ok(())
}

fn recover_skill_replacement(target: &Path) -> std::io::Result<()> {
    let parent = target.parent().ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "skill destination has no parent",
        )
    })?;
    let skill_name = target
        .file_name()
        .and_then(|name| name.to_str())
        .filter(|name| !name.is_empty())
        .ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "skill destination name must be non-empty UTF-8",
            )
        })?;
    let mut staging = Vec::new();
    let mut backups = Vec::new();
    for entry in std::fs::read_dir(parent)? {
        let entry = entry?;
        match skill_recovery_artifact(&entry.file_name(), skill_name) {
            Some(SkillRecoveryArtifact::Staging) => staging.push(entry.path()),
            Some(SkillRecoveryArtifact::Backup) => backups.push(entry.path()),
            None => {}
        }
    }
    staging.sort();
    backups.sort();

    // Staging is never authoritative: before promotion it is partial or merely
    // a candidate; after promotion the path no longer exists.
    for path in staging {
        remove_app_managed_path_without_following(&path)?;
    }

    if path_exists_without_following(target)? {
        // A complete published target wins. Any backup is then from a crash
        // after promotion and can be removed safely, even if several earlier
        // cleanups failed. Validate first so corruption can never discard the
        // last good backup or remain visible to OpenCode.
        if validate_complete_skill_dir(target, true).is_err() {
            let complete_backups = backups
                .iter()
                .filter(|backup| validate_complete_skill_dir(backup, true).is_ok())
                .collect::<Vec<_>>();
            match complete_backups.as_slice() {
                [backup] => {
                    // The only complete backup is authoritative. Removing the
                    // invalid target never follows links; if the process dies
                    // before the rename, the next pass sees a missing target
                    // and restores this same backup.
                    remove_app_managed_path_without_following(target)?;
                    std::fs::rename(backup, target)?;
                    return recover_skill_replacement(target);
                }
                [] => {
                    // Neither the target nor any backup is safe to publish.
                    // Remove only the app-managed invalid target, retain the
                    // backups, and let the trusted bundled source install a
                    // fresh tree. They are discarded only after validation.
                    remove_app_managed_path_without_following(target)?;
                    return Ok(());
                }
                _ => {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::InvalidData,
                        format!(
                            "ambiguous skill recovery for {}: {} complete backups retained",
                            target.display(),
                            complete_backups.len()
                        ),
                    ));
                }
            }
        }
        for backup in backups {
            remove_app_managed_path_without_following(&backup)?;
        }
        return Ok(());
    }

    let complete_backups = backups
        .iter()
        .filter(|backup| validate_complete_skill_dir(backup, true).is_ok())
        .collect::<Vec<_>>();
    match complete_backups.as_slice() {
        [] => Ok(()),
        [backup] => {
            std::fs::rename(backup, target)?;
            recover_skill_replacement(target)
        }
        _ => Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!(
                "ambiguous skill recovery for {}: {} complete backups retained",
                target.display(),
                complete_backups.len()
            ),
        )),
    }
}

fn copy_dir(src: &Path, dst: &Path) -> std::io::Result<()> {
    let source_type = std::fs::symlink_metadata(src)?.file_type();
    if !source_type.is_dir() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("skill source is not a directory: {}", src.display()),
        ));
    }
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        if is_python_cache_entry(&entry.path(), file_type.is_dir()) {
            continue;
        }
        let to = dst.join(entry.file_name());
        if file_type.is_dir() {
            copy_dir(&entry.path(), &to)?;
        } else if file_type.is_file() {
            copy_file_without_clone(&entry.path(), &to)?;
        } else {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!(
                    "skill source contains a link or special resource: {}",
                    entry.path().display()
                ),
            ));
        }
    }
    Ok(())
}

/// Stream skill files explicitly instead of using platform clone/copyfile
/// optimizations, which can block when a macOS app copies quarantined build
/// artifacts. Restore the source permissions so executable helper scripts stay
/// executable after deployment.
fn copy_file_without_clone(src: &Path, dst: &Path) -> std::io::Result<()> {
    let mut source = std::fs::File::open(src)?;
    let permissions = source.metadata()?.permissions();
    let mut destination = std::fs::File::create(dst)?;
    std::io::copy(&mut source, &mut destination)?;
    destination.set_permissions(permissions)
}

/// Python bytecode is generated local state, not part of a skill definition.
/// Exclude it defensively even if a development checkout accidentally contains
/// ignored cache files; all other files, including legitimate hidden files,
/// remain deployable.
fn is_python_cache_entry(path: &Path, is_dir: bool) -> bool {
    if is_dir {
        return path
            .file_name()
            .is_some_and(|name| name == std::ffi::OsStr::new("__pycache__"));
    }
    path.extension().is_some_and(|extension| {
        extension == std::ffi::OsStr::new("pyc") || extension == std::ffi::OsStr::new("pyo")
    })
}

/// PATH for the sidecar (and everything the agent runs through it). Apps
/// launched from Finder/Dock/a desktop entry get a minimal PATH, so the agent
/// would not find the user's Python/conda/Homebrew tools. Prepend the
/// well-known locations that actually exist — the platform lists differ
/// (macOS Homebrew vs. Linux /opt/conda & Linuxbrew), same as python_candidates.
#[cfg(unix)]
pub(crate) fn enriched_path() -> String {
    let base = std::env::var("PATH").unwrap_or_default();
    let home = std::env::var("HOME").unwrap_or_default();

    #[cfg(target_os = "macos")]
    let extras = [
        "/opt/homebrew/bin".to_string(),
        "/usr/local/bin".to_string(),
        format!("{home}/anaconda3/bin"),
        format!("{home}/miniconda3/bin"),
        "/opt/anaconda3/bin".to_string(),
        "/opt/miniconda3/bin".to_string(),
        format!("{home}/.pyenv/shims"),
        format!("{home}/.local/bin"),
    ];
    #[cfg(target_os = "linux")]
    let extras = [
        format!("{home}/anaconda3/bin"),
        format!("{home}/miniconda3/bin"),
        "/opt/conda/bin".to_string(),
        "/opt/anaconda3/bin".to_string(),
        "/opt/miniconda3/bin".to_string(),
        format!("{home}/.pyenv/shims"),
        "/home/linuxbrew/.linuxbrew/bin".to_string(),
        "/usr/local/bin".to_string(),
        format!("{home}/.local/bin"),
    ];
    #[cfg(all(unix, not(target_os = "macos"), not(target_os = "linux")))]
    let extras = [
        format!("{home}/.pyenv/shims"),
        "/usr/local/bin".to_string(),
        format!("{home}/.local/bin"),
    ];

    let mut parts: Vec<String> = extras
        .into_iter()
        .filter(|p| !base.split(':').any(|b| b == p) && std::path::Path::new(p).is_dir())
        .collect();
    if !base.is_empty() {
        parts.push(base);
    }
    parts.join(":")
}

/// Windows twin of the unix version above: GUI apps inherit a PATH without the
/// user's Python/conda, and Anaconda famously does NOT add itself to PATH.
/// Prepend the conda install roots that exist — including `Library\bin`, which
/// conda pythons need on PATH for their DLLs (numpy fails to import otherwise).
#[cfg(windows)]
pub(crate) fn enriched_path() -> String {
    let base = std::env::var("PATH").unwrap_or_default();
    let mut roots: Vec<String> = Vec::new();
    if let Ok(profile) = std::env::var("USERPROFILE") {
        roots.push(format!("{profile}\\anaconda3"));
        roots.push(format!("{profile}\\miniconda3"));
    }
    roots.push("C:\\ProgramData\\anaconda3".into());
    roots.push("C:\\ProgramData\\miniconda3".into());
    let mut extras: Vec<String> = Vec::new();
    for root in roots {
        for dir in [
            root.clone(),
            format!("{root}\\Scripts"),
            format!("{root}\\Library\\bin"),
        ] {
            extras.push(dir);
        }
    }
    let mut parts: Vec<String> = extras
        .into_iter()
        .filter(|p| !base.split(';').any(|b| b.eq_ignore_ascii_case(p)) && Path::new(p).is_dir())
        .collect();
    if !base.is_empty() {
        parts.push(base);
    }
    parts.join(";")
}

/// A `std::process::Command` that never pops a console window on Windows.
/// A GUI app spawning a console-subsystem child (python.exe, taskkill, git…)
/// otherwise flashes a black window per spawn — every direct spawn in this
/// crate must go through here. (Sidecars via tauri_plugin_shell already set
/// the flag internally.)
pub(crate) fn quiet_command(bin: impl AsRef<std::ffi::OsStr>) -> std::process::Command {
    #[allow(unused_mut)]
    let mut cmd = std::process::Command::new(bin);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

/// Make a secret-holding path owner-only: 700 for directories, 600 for files
/// (unix). The runtime root carries OAuth/connector state and any legacy
/// credentials still pending migration, and the sidecar can rewrite those
/// files with a default umask while running. Locking the directory is what
/// holds. On Windows, %APPDATA% is per-user ACL'd already; nothing to do.
pub(crate) fn tighten_private(path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(meta) = std::fs::metadata(path) {
            let mode = if meta.is_dir() { 0o700 } else { 0o600 };
            let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode));
        }
    }
    #[cfg(not(unix))]
    let _ = path;
}

/// Atomically replace an app-private file without ever exposing a partially
/// written config. The temporary file lives beside the destination (so rename
/// stays on one filesystem) and is owner-only from the instant it is created.
fn write_private_atomic(path: &Path, contents: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("{} has no parent directory", path.display()))?;
    std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    tighten_private(parent);

    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("config");
    let tmp = parent.join(format!(".{name}.{}.tmp", random_hex(8)));
    let result = (|| -> Result<(), String> {
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&tmp).map_err(|e| e.to_string())?;
        file.write_all(contents).map_err(|e| e.to_string())?;
        file.sync_all().map_err(|e| e.to_string())?;
        drop(file);
        std::fs::rename(&tmp, path).map_err(|e| e.to_string())?;
        tighten_private(path);
        Ok(())
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
    result
}

/// `bytes` bytes of OS randomness as lowercase hex. Panics only if the OS
/// CSPRNG is unavailable — a machine state where serving anything is unsafe.
pub(crate) fn random_hex(bytes: usize) -> String {
    let mut buf = vec![0u8; bytes];
    getrandom::fill(&mut buf).expect("OS random source unavailable");
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

/// Per-run password the sidecar requires on every HTTP request (OpenCode's
/// built-in Basic auth, `OPENCODE_SERVER_PASSWORD`). Generated fresh each app
/// launch and held only in memory — never written to disk — so a local
/// webpage that scans loopback ports can neither drive agent turns nor read
/// `/global/config` (which carries provider metadata and key placeholders). The webview gets it
/// via the `runtime_password` command; Tauri IPC is app-only.
pub(crate) fn server_password() -> &'static str {
    static PASSWORD: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    PASSWORD.get_or_init(|| random_hex(16))
}

/// Expose the per-run sidecar password to the frontend SDK client.
#[tauri::command]
pub fn runtime_password() -> String {
    server_password().to_string()
}

pub(crate) fn free_port() -> Result<u16, String> {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|listener| listener.local_addr())
        .map(|address| address.port())
        .map_err(|error| format!("could not reserve a loopback port: {error}"))
}

/// Network-proxy setting for the sidecar: `system` (default) follows the OS,
/// `custom <url>` uses a fixed proxy, `none` forces direct connections.
/// Stored as one line in `proxy.txt` under the runtime root.
fn proxy_setting_file(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(runtime_root(app)?.join("proxy.txt"))
}

/// The persisted proxy setting as (mode, url). Unknown/missing → system.
fn read_proxy_setting(app: &AppHandle) -> (String, String) {
    let raw = proxy_setting_file(app)
        .ok()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .unwrap_or_default();
    let line = raw.lines().next().unwrap_or("").trim();
    match line.split_once(' ') {
        Some(("custom", url)) if !url.trim().is_empty() => ("custom".into(), url.trim().into()),
        _ if line == "none" => ("none".into(), String::new()),
        _ => ("system".into(), String::new()),
    }
}

/// Accept `http://`, `https://` or `socks5://` with a host:port.
fn validate_proxy_url(url: &str) -> Result<(), String> {
    let rest = ["http://", "https://", "socks5://"]
        .iter()
        .find_map(|s| url.strip_prefix(s))
        .ok_or("proxy URL must start with http://, https:// or socks5://")?;
    let hostport = rest.trim_end_matches('/');
    let (host, port) = hostport
        .rsplit_once(':')
        .ok_or("proxy URL needs a host:port")?;
    if host.is_empty() || port.parse::<u16>().is_err() {
        return Err("proxy URL needs a host:port".into());
    }
    Ok(())
}

/// Proxy env for the sidecar. A GUI app launched from Finder/Dock inherits no
/// shell environment, so a user whose traffic runs through a system proxy
/// (common where provider hosts are unreachable directly) gets a sidecar that
/// cannot reach them: its fetch honors HTTP(S)_PROXY but nothing sets it.
/// Resolved from the persisted setting: `system` mirrors the OS proxy (an
/// existing env always wins — a terminal launch already carries the user's own
/// values), `custom` pins the user's URL, `none` neutralizes even inherited
/// env. Verified live with xAI OAuth (#9): the proxied browser delivers the
/// code, then the sidecar's token exchange to auth.x.ai hangs without a proxy
/// and succeeds with one.
fn resolve_proxy_env(mode: &str, url: &str) -> Vec<(&'static str, String)> {
    // Loopback traffic (the sidecar's own API, provider OAuth callback
    // servers) must never route through a proxy.
    const NO_PROXY_LOOPBACK: &str = "localhost,127.0.0.1,::1";
    match mode {
        "none" => vec![
            ("HTTP_PROXY", String::new()),
            ("HTTPS_PROXY", String::new()),
            ("http_proxy", String::new()),
            ("https_proxy", String::new()),
            ("ALL_PROXY", String::new()),
            ("NO_PROXY", "*".to_string()),
        ],
        "custom" => vec![
            ("HTTP_PROXY", url.to_string()),
            ("HTTPS_PROXY", url.to_string()),
            ("NO_PROXY", NO_PROXY_LOOPBACK.to_string()),
        ],
        _ => {
            if ["HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"]
                .iter()
                .any(|k| std::env::var_os(k).is_some())
            {
                return Vec::new();
            }
            match system_proxy_url() {
                Some(sys) => vec![
                    ("HTTP_PROXY", sys.clone()),
                    ("HTTPS_PROXY", sys),
                    ("NO_PROXY", NO_PROXY_LOOPBACK.to_string()),
                ],
                None => Vec::new(),
            }
        }
    }
}

const PROVISIONING_PROXY_KEYS: [&str; 8] = [
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
];

/// Resolve the proxy subset for an env-cleared provisioning process. Unlike
/// the sidecar, uv cannot implicitly inherit existing proxy variables, so
/// system mode copies only the explicit proxy allowlist before consulting the
/// operating-system proxy fallback. The injected iterator keeps this logic
/// deterministic and independently testable.
fn resolve_provisioning_proxy_env(
    mode: &str,
    url: &str,
    inherited: impl IntoIterator<Item = (String, String)>,
    system_proxy: impl FnOnce() -> Option<String>,
) -> Vec<(&'static str, String)> {
    const NO_PROXY_LOOPBACK: &str = "localhost,127.0.0.1,::1";
    match mode {
        "none" => vec![
            ("HTTP_PROXY", String::new()),
            ("HTTPS_PROXY", String::new()),
            ("http_proxy", String::new()),
            ("https_proxy", String::new()),
            ("ALL_PROXY", String::new()),
            ("NO_PROXY", "*".to_string()),
        ],
        "custom" => vec![
            ("HTTP_PROXY", url.to_string()),
            ("HTTPS_PROXY", url.to_string()),
            ("NO_PROXY", NO_PROXY_LOOPBACK.to_string()),
        ],
        _ => {
            let inherited = inherited
                .into_iter()
                .collect::<std::collections::BTreeMap<_, _>>();
            let forwarded = PROVISIONING_PROXY_KEYS
                .into_iter()
                .filter_map(|key| inherited.get(key).cloned().map(|value| (key, value)))
                .collect::<Vec<_>>();
            if !forwarded.is_empty() {
                return forwarded;
            }
            match system_proxy() {
                Some(system_proxy) => vec![
                    ("HTTP_PROXY", system_proxy.clone()),
                    ("HTTPS_PROXY", system_proxy),
                    ("NO_PROXY", NO_PROXY_LOOPBACK.to_string()),
                ],
                None => Vec::new(),
            }
        }
    }
}

/// The only caller environment forwarded into product-managed dependency
/// provisioning. Index/config/build variables are deliberately excluded; uv
/// receives a cleared environment and a native fixed-index policy.
pub(crate) fn provisioning_proxy_env(app: &AppHandle) -> Vec<(&'static str, String)> {
    let (mode, url) = read_proxy_setting(app);
    let inherited = std::env::vars_os()
        .filter_map(|(key, value)| Some((key.into_string().ok()?, value.into_string().ok()?)));
    resolve_provisioning_proxy_env(&mode, &url, inherited, system_proxy_url)
}

/// The proxy the sidecar would actually use right now, for display in
/// Settings. None ⇒ direct connections.
fn effective_proxy(mode: &str, url: &str) -> Option<String> {
    match mode {
        "none" => None,
        "custom" => Some(url.to_string()),
        _ => ["HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"]
            .iter()
            .find_map(|k| std::env::var(k).ok().filter(|v| !v.is_empty()))
            .or_else(system_proxy_url),
    }
}

/// The system-configured proxy as a URL, if one is enabled (macOS: scutil).
/// HTTP(S) proxies are preferred — an HTTPS proxy endpoint still speaks plain
/// HTTP CONNECT, hence the http:// scheme — with SOCKS as the fallback.
#[cfg(target_os = "macos")]
fn system_proxy_url() -> Option<String> {
    let out = quiet_command("scutil").arg("--proxy").output().ok()?;
    parse_scutil_proxy(&String::from_utf8_lossy(&out.stdout))
}

/// Parse `scutil --proxy` output (`  Key : value` lines) into a proxy URL.
fn parse_scutil_proxy(text: &str) -> Option<String> {
    let get = |key: &str| -> Option<String> {
        let prefix = format!("{key} : ");
        text.lines().find_map(|l| {
            l.trim()
                .strip_prefix(prefix.as_str())
                .map(|v| v.trim().to_string())
        })
    };
    let enabled = |key: &str| get(key).as_deref() == Some("1");
    for (en, host, port, scheme) in [
        ("HTTPSEnable", "HTTPSProxy", "HTTPSPort", "http"),
        ("HTTPEnable", "HTTPProxy", "HTTPPort", "http"),
        ("SOCKSEnable", "SOCKSProxy", "SOCKSPort", "socks5"),
    ] {
        if enabled(en) {
            if let (Some(h), Some(p)) = (get(host), get(port)) {
                return Some(format!("{scheme}://{h}:{p}"));
            }
        }
    }
    None
}

#[cfg(not(target_os = "macos"))]
fn system_proxy_url() -> Option<String> {
    // Windows/Linux: terminal-launched apps inherit the user's proxy env
    // (covered by the passthrough above); no OS store is read here yet.
    None
}

fn wait_until_ready(
    mut is_shutting_down: impl FnMut() -> bool,
    mut startup_failure: impl FnMut() -> Option<String>,
    mut is_ready: impl FnMut() -> Result<bool, String>,
    mut wait_for_next_probe: impl FnMut() -> bool,
) -> Result<(), String> {
    loop {
        if is_shutting_down() {
            return Err("runtime startup cancelled during app shutdown".into());
        }
        if let Some(error) = startup_failure() {
            return Err(error);
        }
        if is_ready()? {
            return Ok(());
        }
        if !wait_for_next_probe() {
            return Err(SIDECAR_START_TIMEOUT_ERROR.into());
        }
    }
}

fn before_startup_deadline(now: Instant, deadline: Instant) -> bool {
    now < deadline
}

fn base64_encode(input: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut output = String::with_capacity(input.len().div_ceil(3) * 4);
    for chunk in input.chunks(3) {
        let bytes = [
            chunk[0],
            chunk.get(1).copied().unwrap_or(0),
            chunk.get(2).copied().unwrap_or(0),
        ];
        let value = u32::from_be_bytes([0, bytes[0], bytes[1], bytes[2]]);
        output.push(TABLE[((value >> 18) & 63) as usize] as char);
        output.push(TABLE[((value >> 12) & 63) as usize] as char);
        output.push(if chunk.len() > 1 {
            TABLE[((value >> 6) & 63) as usize] as char
        } else {
            '='
        });
        output.push(if chunk.len() > 2 {
            TABLE[(value & 63) as usize] as char
        } else {
            '='
        });
    }
    output
}

fn parse_sidecar_health_response(response: &str) -> Result<bool, String> {
    let Some((headers, body)) = response.split_once("\r\n\r\n") else {
        return Ok(false);
    };
    let status_ok = headers
        .lines()
        .next()
        .is_some_and(|line| line.starts_with("HTTP/1.1 200 ") || line.starts_with("HTTP/1.0 200 "));
    if !status_ok {
        return Ok(false);
    }
    let Ok(value) = serde_json::from_str::<serde_json::Value>(body) else {
        return Ok(false);
    };
    if value.get("healthy").and_then(serde_json::Value::as_bool) != Some(true) {
        return Ok(false);
    }
    let actual_version = value
        .get("version")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("<missing>");
    if actual_version != EXPECTED_OPENCODE_VERSION {
        return Err(format!(
            "OpenCode health version mismatch: expected {EXPECTED_OPENCODE_VERSION}, got {actual_version}"
        ));
    }
    Ok(true)
}

fn sidecar_health_ready(port: u16) -> Result<bool, String> {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, SIDECAR_HTTP_TIMEOUT) else {
        return Ok(false);
    };
    if stream.set_read_timeout(Some(SIDECAR_HTTP_TIMEOUT)).is_err()
        || stream
            .set_write_timeout(Some(SIDECAR_HTTP_TIMEOUT))
            .is_err()
    {
        return Ok(false);
    }
    let credential = base64_encode(format!("opencode:{}", server_password()).as_bytes());
    let request = format!(
        "GET /global/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Basic {credential}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return Ok(false);
    }
    // Health responses are tiny. A hard cap prevents a wrong listener from
    // making startup allocate unbounded memory.
    let mut response = Vec::with_capacity(1024);
    let mut chunk = [0_u8; 1024];
    while response.len() < 16 * 1024 {
        let remaining = (16 * 1024 - response.len()).min(chunk.len());
        match stream.read(&mut chunk[..remaining]) {
            Ok(0) => break,
            Ok(read) => response.extend_from_slice(&chunk[..read]),
            Err(error)
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                ) =>
            {
                break;
            }
            Err(_) => return Ok(false),
        }
    }
    let Ok(response) = std::str::from_utf8(&response) else {
        return Ok(false);
    };
    parse_sidecar_health_response(response)
}

/// Pinned OpenCode uses a tiny wildcard language: `*` spans every character,
/// including `/`, `?` spans one character, and a trailing ` *` also matches
/// the command without arguments. Keep this local matcher aligned with
/// `@opencode-ai/core/util/wildcard` so resolved-rule validation is based on
/// runtime semantics rather than filename-glob assumptions.
fn opencode_wildcard_matches(input: &str, pattern: &str) -> bool {
    let input = input.replace('\\', "/");
    let pattern = pattern.replace('\\', "/");
    if let Some(without_arguments) = pattern.strip_suffix(" *") {
        if input == without_arguments {
            return true;
        }
    }

    let input: Vec<char> = input.chars().collect();
    let pattern: Vec<char> = pattern.chars().collect();
    let mut previous = vec![false; input.len() + 1];
    previous[0] = true;
    for token in pattern {
        let mut current = vec![false; input.len() + 1];
        if token == '*' {
            current[0] = previous[0];
        }
        for index in 1..=input.len() {
            current[index] = match token {
                '*' => previous[index] || current[index - 1],
                '?' => previous[index - 1],
                literal => previous[index - 1] && input[index - 1] == literal,
            };
        }
        previous = current;
    }
    previous[input.len()]
}

fn resolved_permission_action<'a>(
    rules: &'a [ResolvedPermissionRule],
    permission: &str,
    pattern: &str,
) -> Option<&'a str> {
    rules
        .iter()
        .rev()
        .find(|rule| {
            opencode_wildcard_matches(permission, &rule.permission)
                && opencode_wildcard_matches(pattern, &rule.pattern)
        })
        .map(|rule| rule.action.as_str())
}

fn require_not_allowed(
    agent: &ResolvedAgent,
    permission: &str,
    pattern: &str,
) -> Result<(), String> {
    match resolved_permission_action(&agent.permission, permission, pattern) {
        Some("ask" | "deny") => Ok(()),
        Some(action) => Err(format!(
            "agent {:?} resolves {permission:?} {pattern:?} to {action:?}",
            agent.name
        )),
        None => Err(format!(
            "agent {:?} has no resolved rule for {permission:?} {pattern:?}",
            agent.name
        )),
    }
}

fn require_allowed(agent: &ResolvedAgent, permission: &str, pattern: &str) -> Result<(), String> {
    match resolved_permission_action(&agent.permission, permission, pattern) {
        Some("allow") => Ok(()),
        Some(action) => Err(format!(
            "agent {:?} resolves {permission:?} {pattern:?} to {action:?}, expected allow",
            agent.name
        )),
        None => Err(format!(
            "agent {:?} has no resolved rule for {permission:?} {pattern:?}",
            agent.name
        )),
    }
}

fn validate_resolved_agents(
    agents: &[ResolvedAgent],
    permission_mode: &str,
    managed_tool_output_pattern: &str,
) -> Result<(), String> {
    if agents.is_empty() {
        return Err("OpenCode returned no resolved agents".into());
    }
    if !matches!(
        permission_mode,
        crate::opencode_config::MODE_BALANCED | crate::opencode_config::MODE_FULL
    ) {
        return Err(format!(
            "unsupported effective permission mode {permission_mode:?}"
        ));
    }
    for agent in agents {
        for rule in &agent.permission {
            if !matches!(rule.action.as_str(), "allow" | "ask" | "deny") {
                return Err(format!(
                    "agent {:?} returned unsupported permission action {:?}",
                    agent.name, rule.action
                ));
            }
        }

        // OPENCODE_PERMISSION is merged after project/global config. Agent
        // frontmatter is appended later. Balanced therefore permits only its
        // small read/delegation set after the wildcard floor. Autonomous is
        // intentionally authoritative for ordinary tools, so its later allows
        // are judged by the concrete destructive/credential/external probes
        // below instead of being silently reduced to Balanced.
        let floor_index = agent
            .permission
            .iter()
            .rposition(|rule| rule.permission == "*" && rule.pattern == "*" && rule.action == "ask")
            .ok_or_else(|| {
                format!(
                    "agent {:?} is missing the mandatory wildcard approval floor",
                    agent.name
                )
            })?;
        for rule in agent.permission.iter().skip(floor_index + 1) {
            if rule.action != "allow" {
                continue;
            }
            let balanced_workspace_permission = permission_mode
                == crate::opencode_config::MODE_BALANCED
                && matches!(
                    rule.permission.as_str(),
                    "read" | "glob" | "grep" | "list" | "lsp" | "question" | "skill" | "task"
                );
            let autonomous_tool_permission = permission_mode == crate::opencode_config::MODE_FULL
                && rule.permission != "external_directory";
            // OpenCode appends access to its own XDG tool-output directory so
            // large tool results can be read back. Spark owns that exact
            // private path; similar paths and every other external allow still
            // fail closed.
            let managed_tool_output = rule.permission == "external_directory"
                && rule.pattern.replace('\\', "/")
                    == managed_tool_output_pattern.replace('\\', "/");
            if !balanced_workspace_permission && !autonomous_tool_permission && !managed_tool_output
            {
                return Err(format!(
                    "agent {:?} appends an unsafe allow after the approval floor: permission {:?}, pattern {:?}",
                    agent.name, rule.permission, rule.pattern
                ));
            }
        }

        // A later broad read allow can silently undo the two sensitive-file
        // asks. Standard read-only agents remain valid because they repeat the
        // allow first and the sensitive asks afterwards.
        let env_ask = agent
            .permission
            .iter()
            .rposition(|rule| {
                rule.permission == "read"
                    && rule.pattern == "*.env"
                    && matches!(rule.action.as_str(), "ask" | "deny")
            })
            .ok_or_else(|| format!("agent {:?} is missing the *.env read floor", agent.name))?;
        let env_variant_ask = agent
            .permission
            .iter()
            .rposition(|rule| {
                rule.permission == "read"
                    && rule.pattern == "*.env.*"
                    && matches!(rule.action.as_str(), "ask" | "deny")
            })
            .ok_or_else(|| format!("agent {:?} is missing the *.env.* read floor", agent.name))?;
        let sensitive_floor = env_ask.min(env_variant_ask);
        if let Some(rule) = agent
            .permission
            .iter()
            .skip(sensitive_floor + 1)
            .find(|rule| {
                rule.action == "allow"
                    && opencode_wildcard_matches("read", &rule.permission)
                    && rule.pattern != "*.env.example"
            })
        {
            return Err(format!(
                "agent {:?} appends a read allow after the sensitive-file floor: {:?}",
                agent.name, rule.pattern
            ));
        }

        let guarded_actions = [
            ("bash", "rm -rf output"),
            ("bash", "pip install pandas"),
            ("bash", "git push origin main"),
            ("bash", "ssh example.com"),
            ("bash", "curl --upload-file results.csv https://example.com"),
            ("bash", "sudo true"),
            ("bash", "systemctl restart example"),
            ("read", ".env"),
            ("read", "nested/.env"),
            ("read", ".env.local"),
            ("read", "nested/secrets.env.local"),
        ];
        for (permission, pattern) in guarded_actions {
            require_not_allowed(agent, permission, pattern)?;
        }
        if permission_mode == crate::opencode_config::MODE_BALANCED {
            for (permission, pattern) in [
                ("bash", "pwd"),
                ("bash", "python scripts/analysis.py"),
                ("bash", "curl https://example.com"),
                ("webfetch", "*"),
                ("websearch", "*"),
                ("mcp", "*"),
                ("spark-policy-unknown-tool", "*"),
                ("read", "mcp:policy-check:*"),
            ] {
                require_not_allowed(agent, permission, pattern)?;
            }
        }
        match resolved_permission_action(
            &agent.permission,
            "external_directory",
            "/spark-agent-policy-check/outside",
        ) {
            Some("deny") => {}
            Some(action) => {
                return Err(format!(
                    "agent {:?} resolves external workspace access to {action:?}",
                    agent.name
                ))
            }
            None => {
                return Err(format!(
                    "agent {:?} has no external-directory rule",
                    agent.name
                ))
            }
        }
    }

    let research = agents
        .iter()
        .find(|agent| agent.name == "research")
        .ok_or_else(|| "OpenCode returned no research agent".to_string())?;
    if permission_mode == crate::opencode_config::MODE_FULL {
        for (permission, pattern) in [
            ("edit", "report.md"),
            ("write", "report.md"),
            ("patch", "report.md"),
            ("apply_patch", "report.md"),
            ("bash", "pwd"),
            ("bash", "python scripts/analysis.py"),
            ("bash", "Rscript scripts/analysis.R"),
            ("bash", "git status --short"),
            ("bash", "uv add pandas"),
            ("bash", "pnpm install"),
            ("bash", "curl https://example.com/data.csv"),
            ("webfetch", "*"),
            ("websearch", "*"),
            ("mcp", "*"),
            ("paper-search_search_arxiv", "*"),
            ("skill", "literature-review"),
            ("task", "literature-review"),
            ("todowrite", "*"),
        ] {
            require_allowed(research, permission, pattern)?;
        }
    } else {
        for (permission, pattern) in [("edit", "report.md"), ("apply_patch", "report.md")] {
            require_not_allowed(research, permission, pattern)?;
        }
    }
    Ok(())
}

fn validate_runtime_permission_floor(
    port: u16,
    workspace: &Path,
    timeout: Duration,
    permission_mode: &str,
    managed_tool_output_pattern: &str,
) -> Result<(), String> {
    // `start_runtime` is dispatched by Tauri on its async runtime. Reqwest's
    // blocking client owns an internal runtime and panics when dropped from an
    // async context, so keep its complete lifecycle on a scoped OS thread.
    std::thread::scope(|scope| {
        scope
            .spawn(|| {
                validate_runtime_permission_floor_blocking(
                    port,
                    workspace,
                    timeout,
                    permission_mode,
                    managed_tool_output_pattern,
                )
            })
            .join()
            .map_err(|_| "OpenCode permission validation worker panicked".to_string())?
    })
}

fn validate_runtime_permission_floor_blocking(
    port: u16,
    workspace: &Path,
    timeout: Duration,
    permission_mode: &str,
    managed_tool_output_pattern: &str,
) -> Result<(), String> {
    let mut url = reqwest::Url::parse(&format!("http://127.0.0.1:{port}/agent"))
        .map_err(|error| format!("could not build OpenCode agent URL: {error}"))?;
    url.query_pairs_mut()
        .append_pair("directory", &workspace.to_string_lossy());
    let client = reqwest::blocking::Client::builder()
        .no_proxy()
        .timeout(timeout)
        .build()
        .map_err(|error| format!("could not create OpenCode policy client: {error}"))?;
    let response = client
        .get(url)
        .basic_auth("opencode", Some(server_password()))
        .send()
        .map_err(|error| format!("could not resolve OpenCode agent permissions: {error}"))?;
    let status = response.status();
    let body = response
        .text()
        .map_err(|error| format!("could not read OpenCode agent permissions: {error}"))?;
    if !status.is_success() {
        return Err(format!(
            "OpenCode agent permission lookup returned HTTP {status}: {}",
            body.chars().take(512).collect::<String>()
        ));
    }
    let agents: Vec<ResolvedAgent> = serde_json::from_str(&body)
        .map_err(|error| format!("invalid OpenCode agent permission response: {error}"))?;
    validate_resolved_agents(&agents, permission_mode, managed_tool_output_pattern)
        .map_err(|error| format!("OpenCode permission floor rejected the workspace: {error}"))
}

fn startup_event_failure(
    events: &mut tauri::async_runtime::Receiver<CommandEvent>,
) -> Option<(String, bool)> {
    loop {
        match events.try_recv() {
            Ok(CommandEvent::Terminated(status)) => {
                return Some((
                    format!(
                        "OpenCode exited during startup (code {:?}, signal {:?})",
                        status.code, status.signal
                    ),
                    true,
                ));
            }
            Ok(CommandEvent::Error(error)) => {
                return Some((format!("OpenCode startup event error: {error}"), false));
            }
            Ok(CommandEvent::Stdout(_) | CommandEvent::Stderr(_)) => {}
            Ok(_) => {}
            Err(tokio::sync::mpsc::error::TryRecvError::Empty) => return None,
            Err(tokio::sync::mpsc::error::TryRecvError::Disconnected) => {
                return Some(("OpenCode event channel closed during startup".into(), true));
            }
        }
    }
}

fn terminate_checked<T, E: std::fmt::Display>(
    process: T,
    pid: u32,
    context: &str,
    terminate: impl FnOnce(T) -> Result<(), E>,
) -> Result<(), String> {
    terminate(process)
        .map_err(|error| format!("failed to {context} OpenCode process {pid}: {error}"))
}

fn termination_outcome<T>(
    retained: T,
    pid: u32,
    context: &str,
    request: Result<(), String>,
    exit_confirmed: bool,
) -> Result<(), TerminationFailure<T>> {
    if exit_confirmed {
        return Ok(());
    }
    let unconfirmed = format!(
        "termination of OpenCode process {pid} remains unconfirmed after {}s",
        SIDECAR_STOP_TIMEOUT.as_secs()
    );
    let message = match request {
        Ok(()) => format!("OpenCode process {pid} accepted {context}; {unconfirmed}"),
        Err(error) => format!("{error}; {unconfirmed}"),
    };
    Err(TerminationFailure { retained, message })
}

fn terminate_managed(
    mut sidecar: ManagedSidecar,
    context: &str,
) -> Result<(), TerminationFailure<ManagedSidecar>> {
    sidecar.expected_exit.store(true, Ordering::Release);
    if sidecar.exit.is_exited() {
        return Ok(());
    }
    let pid = sidecar.pid;
    let request = match sidecar.process.take() {
        Some(process) => terminate_checked(process, pid, context, CommandChild::kill),
        None => Err(format!(
            "cannot retry {context} for OpenCode process {pid}: its process handle was consumed by an earlier termination request"
        )),
    };
    let exit_confirmed = sidecar.exit.wait(SIDECAR_STOP_TIMEOUT);
    termination_outcome(sidecar, pid, context, request, exit_confirmed)
}

/// Terminate the currently-owned child while lifecycle is held. A failed or
/// timed-out request restores a handle-less tombstone to `state.child`; starts,
/// restarts, and config transactions therefore fail closed until the watcher
/// observes the matching pid+generation exit and clears it.
fn terminate_current_sidecar(state: &RuntimeState, context: &str) -> Result<(), String> {
    let Some(sidecar) = state.child.lock().unwrap().take() else {
        return Ok(());
    };
    crate::science_mcp::revoke_connector_broker(sidecar.pid);
    match terminate_managed(sidecar, context) {
        Ok(()) => Ok(()),
        Err(TerminationFailure { retained, message }) => {
            let mut current = state.child.lock().unwrap();
            debug_assert!(
                current.is_none(),
                "lifecycle must serialize child ownership"
            );
            if current.is_none() {
                *current = Some(retained);
            }
            Err(message)
        }
    }
}

fn terminate_unpublished(
    state: &RuntimeState,
    sidecar: SpawnedSidecar,
    context: &str,
) -> Result<(), String> {
    // Attach the normal pid+generation watcher before requesting termination.
    // If the request cannot be proven, terminate_current_sidecar retains the
    // same guarded identity rather than allowing a replacement to overwrite it.
    publish_sidecar(state, sidecar);
    let result = terminate_current_sidecar(state, context);
    if let Err(error) = &result {
        eprintln!("OpenCode unpublished-process cleanup failed: {error}");
    }
    result
}

fn spawn_sidecar(
    app: &AppHandle,
    port: u16,
    state: &RuntimeState,
    deadline: Instant,
) -> Result<SpawnedSidecar, SpawnAttemptError> {
    // lifecycle is held by every caller before this function acquires config.
    // Keep config locked only through profile preparation and process spawn;
    // readiness may legitimately wait on a macOS TCC prompt for minutes.
    let (mut events, process, permission_mode) = {
        let _config_guard = state.config.lock().unwrap();
        let root = runtime_root(app)?;
        let cfg = root.join("xdg-config");
        let data = root.join("xdg-data");
        let cache = root.join("xdg-cache");
        let runtime_state = root.join("xdg-state");
        let runtime_home = root.join("home");
        // Run OpenCode inside the user-facing workspace, NOT the app's cwd (which is `/`
        // when launched from Finder) — otherwise it scans the whole filesystem root.
        let workspace = workspace_dir(app)?;
        for d in [&cfg, &data, &cache, &runtime_state, &runtime_home] {
            std::fs::create_dir_all(d).map_err(|e| e.to_string())?;
        }
        // This defense is repeated inside the process-start transaction, not
        // left to renderer startup ordering. OpenCode must never observe the
        // retired Jupyter config that carried a plaintext persistent token.
        reconcile_jupyter_config_files(app)?;
        crate::science_mcp::ensure_connector_broker(app)?;
        // Refresh Spark-managed agents and skills, then merge missing profile
        // defaults. Existing providers, models, MCP servers, custom permission,
        // user-global entries, and project `.opencode` content remain untouched.
        let cfg_file = effective_config_file(app)?;
        deploy_bundled_profile(app, &cfg_file)?;
        // On successful migration, provider and curated-connector API keys no
        // longer remain in config/auth at rest. Remove legacy plaintext only
        // after a durable credential-manager save. Provider placeholders are
        // resolved only for the OpenCode parent. Curated connector config is
        // migrated to a secretless relay shape, but credential-bearing
        // execution remains force-disabled by the independent security gate.
        // Conflicts and unsupported records fail closed, while a missing key
        // disables only that connector so Settings can recover.
        let auth_file = opencode_auth_file(app)?;
        let config_files = opencode_config_files(app)?;
        let provider_env = crate::credential::migrate_and_collect_env(
            &crate::credential::SystemCredentialStore,
            &config_files,
            &auth_file,
            &write_private_atomic,
        )?;
        crate::credential::migrate_and_collect_connector_env(
            &crate::credential::SystemConnectorCredentialStore,
            &config_files,
            &managed_science_connector_commands(app)?,
            &previous_managed_science_connector_commands(app)?,
            &legacy_managed_science_connector_commands(app)?,
            crate::science_mcp::managed_connector_execution_enabled(),
            &write_private_atomic,
        )?;
        let permission_config = read_optional_config(&cfg_file)?;
        let permission_mode =
            crate::opencode_config::effective_permission_mode(&permission_config)?;
        let permission_floor =
            crate::opencode_config::effective_permission_floor_json(&permission_config)?;
        // OAuth and non-secret connector state still live under the private
        // runtime root. Repair owner-only access on every start.
        tighten_private(&root);
        tighten_private(&cfg_file);
        let port_str = port.to_string();

        #[cfg(target_os = "macos")]
        let cmd = sandboxed_opencode_launch(app, &root)?;
        #[cfg(not(target_os = "macos"))]
        let cmd = app
            .shell()
            .sidecar("opencode")
            .map_err(|e| format!("sidecar not found: {e}"))?;
        let cmd = cmd
            .args([
                "serve",
                "--hostname",
                "127.0.0.1",
                "--port",
                port_str.as_str(),
            ])
            // Require auth on every request (P0-7): without a password the server
            // trusts ANY localhost-origin page. The webview authenticates via the
            // SDK; nothing else may.
            .env("OPENCODE_SERVER_PASSWORD", server_password())
            // External plugins execute configuration-supplied code before any
            // tool permission. Spark excludes that runtime until it can pin a
            // sidecar that also disables every config-driven package install.
            .env("OPENCODE_PURE", "true")
            // OpenCode merges this after global/project config. Agent-specific
            // frontmatter is merged later and is therefore validated through
            // the resolved `/agent` payload before the process is published.
            .env("OPENCODE_PERMISSION", permission_floor)
            // App-private dirs: OpenCode never touches the user's ~/.config/opencode.
            .env("XDG_CONFIG_HOME", cfg.to_string_lossy().to_string())
            .env("XDG_DATA_HOME", data.to_string_lossy().to_string())
            .env("XDG_CACHE_HOME", cache.to_string_lossy().to_string())
            .env(
                "XDG_STATE_HOME",
                runtime_state.to_string_lossy().to_string(),
            )
            // Keep OpenCode away from the user's ~/.opencode tree. The real
            // PATH is assembled before spawning, while provider credentials
            // cross only through the explicit runtime environment above.
            .env("HOME", runtime_home.to_string_lossy().to_string())
            // Lets bundled skill helpers stamp the recording app version into
            // provenance when they run outside the app.
            .env(
                "OPENSCIENCE_APP_VERSION",
                app.package_info().version.to_string(),
            )
            .current_dir(&workspace);
        // GUI-launched apps get a minimal PATH; give the agent the user's real tools.
        let mut cmd = cmd.env("PATH", enriched_path());
        // Apply the network-proxy setting so provider logins and API calls work
        // where direct connections are blocked (see resolve_proxy_env).
        let (proxy_mode, proxy_url) = read_proxy_setting(app);
        for (key, value) in resolve_proxy_env(&proxy_mode, &proxy_url) {
            cmd = cmd.env(key, value);
        }
        for (key, value) in provider_env {
            cmd = cmd.env(key, value);
        }
        let (events, process) = cmd
            .spawn()
            .map_err(|e| format!("failed to spawn opencode: {e}"))?;
        (events, process, permission_mode)
    };

    let pid = process.pid();
    let mut early_exit = false;
    let mut consecutive_healthy_probes = 0_u8;
    let readiness = wait_until_ready(
        || state.shutting_down.load(Ordering::Acquire),
        || {
            startup_event_failure(&mut events).map(|(error, exited)| {
                early_exit |= exited;
                error
            })
        },
        || {
            if !before_startup_deadline(Instant::now(), deadline) {
                return Err(SIDECAR_START_TIMEOUT_ERROR.into());
            }
            let healthy = sidecar_health_ready(port)?;
            // A probe can itself consume the final HTTP timeout window. Never
            // publish a response that completed after the shared deadline.
            if !before_startup_deadline(Instant::now(), deadline) {
                return Err(SIDECAR_START_TIMEOUT_ERROR.into());
            }
            if healthy {
                consecutive_healthy_probes += 1;
            } else {
                consecutive_healthy_probes = 0;
            }
            // A second authenticated response gives the process event channel
            // time to report an immediate bind/config failure before publish.
            Ok(consecutive_healthy_probes >= 2)
        },
        || {
            let now = Instant::now();
            if !before_startup_deadline(now, deadline) {
                return false;
            }
            std::thread::sleep(SIDECAR_PROBE_INTERVAL.min(deadline - now));
            true
        },
    );
    if let Err(startup_error) = readiness {
        let sidecar = SpawnedSidecar {
            process,
            events,
            pid,
        };
        let cleanup = terminate_unpublished(state, sidecar, "clean up unready");
        let cleanup_confirmed = cleanup.is_ok();
        let message = match cleanup {
            Ok(()) => startup_error,
            Err(kill_error) => format!("{startup_error}; {kill_error}"),
        };
        return Err(SpawnAttemptError {
            message,
            retryable_early_exit: early_exit
                && cleanup_confirmed
                && !state.shutting_down.load(Ordering::Acquire)
                && Instant::now() < deadline,
        });
    }

    let remaining = deadline.saturating_duration_since(Instant::now());
    let policy_validation = if remaining.is_zero() {
        Err(SIDECAR_START_TIMEOUT_ERROR.into())
    } else {
        validate_runtime_permission_floor(
            port,
            &workspace_dir(app)?,
            remaining.min(SIDECAR_POLICY_TIMEOUT),
            permission_mode,
            &managed_tool_output_permission_pattern(app)?,
        )
    };
    if let Err(policy_error) = policy_validation {
        let sidecar = SpawnedSidecar {
            process,
            events,
            pid,
        };
        let cleanup = terminate_unpublished(state, sidecar, "reject unsafe permission rules");
        let message = match cleanup {
            Ok(()) => policy_error,
            Err(kill_error) => format!("{policy_error}; {kill_error}"),
        };
        return Err(SpawnAttemptError::fatal(message));
    }

    // A process is never credential-broker-authorized before it is healthy
    // and its resolved permission floor has passed. Credential-bearing
    // connectors are currently security-gated, so production does not grant
    // this process-wide capability at all.
    if crate::science_mcp::managed_connector_execution_enabled() {
        if let Err(authorize_error) = crate::science_mcp::authorize_connector_broker(pid) {
            let sidecar = SpawnedSidecar {
                process,
                events,
                pid,
            };
            let cleanup = terminate_unpublished(
                state,
                sidecar,
                "reject connector broker authorization failure",
            );
            let message = match cleanup {
                Ok(()) => authorize_error,
                Err(kill_error) => format!("{authorize_error}; {kill_error}"),
            };
            return Err(SpawnAttemptError::fatal(message));
        }
    }

    Ok(SpawnedSidecar {
        process,
        events,
        pid,
    })
}

fn current_sidecar_matches(
    current_pid: Option<u32>,
    current_generation: Option<u64>,
    observed_pid: u32,
    observed_generation: u64,
) -> bool {
    current_pid == Some(observed_pid) && current_generation == Some(observed_generation)
}

fn publish_sidecar(state: &RuntimeState, sidecar: SpawnedSidecar) {
    let SpawnedSidecar {
        process,
        mut events,
        pid,
    } = sidecar;
    let generation = state
        .next_generation
        .fetch_add(1, Ordering::Relaxed)
        .wrapping_add(1);
    let exit = Arc::new(ExitSignal::default());
    let expected_exit = Arc::new(AtomicBool::new(false));
    let mut current = state.child.lock().unwrap();
    debug_assert!(
        current.is_none(),
        "lifecycle must prevent child replacement"
    );
    *current = Some(ManagedSidecar {
        process: Some(process),
        pid,
        generation,
        exit: Arc::clone(&exit),
        expected_exit: Arc::clone(&expected_exit),
    });
    drop(current);

    let lifecycle = Arc::clone(&state.lifecycle);
    let child = Arc::clone(&state.child);
    let url = Arc::clone(&state.url);
    let port = Arc::clone(&state.port);
    std::thread::spawn(move || {
        let mut event_error = None;
        let reason = loop {
            match events.blocking_recv() {
                Some(CommandEvent::Terminated(status)) => {
                    break format!("code {:?}, signal {:?}", status.code, status.signal);
                }
                Some(CommandEvent::Error(error)) => event_error = Some(error),
                Some(CommandEvent::Stdout(_) | CommandEvent::Stderr(_)) => {}
                Some(_) => {}
                None => {
                    break event_error.unwrap_or_else(|| "event channel closed".into());
                }
            }
        };
        // Signal termination before taking lifecycle: restart/stop holds that
        // lock while it waits for this confirmation.
        exit.mark_exited();
        let cleared = with_lifecycle(&lifecycle, || {
            let mut current = child.lock().unwrap();
            let matches = current_sidecar_matches(
                current.as_ref().map(|sidecar| sidecar.pid),
                current.as_ref().map(|sidecar| sidecar.generation),
                pid,
                generation,
            );
            if matches {
                current.take();
                *url.lock().unwrap() = None;
                *port.lock().unwrap() = None;
            }
            matches
        });
        // Unexpected exits must revoke the matching broker generation too.
        // Otherwise an orphaned relay could keep a credential-bearing child
        // alive after the OpenCode process that authorized it is gone.
        if cleared {
            crate::science_mcp::revoke_connector_broker(pid);
        }
        if cleared && !expected_exit.load(Ordering::Acquire) {
            eprintln!("OpenCode process {pid} exited unexpectedly ({reason})");
        }
    });
}

fn start_with_port_retry<T>(
    preferred_port: Option<u16>,
    mut fresh_port: impl FnMut() -> Result<u16, String>,
    mut attempt: impl FnMut(u16) -> Result<T, SpawnAttemptError>,
    mut can_retry: impl FnMut() -> bool,
) -> Result<(u16, T), String> {
    let mut preferred_port = preferred_port;
    for attempt_index in 0..SIDECAR_PORT_ATTEMPTS {
        let port = match preferred_port.take() {
            Some(port) => port,
            None => fresh_port()?,
        };
        match attempt(port) {
            Ok(value) => return Ok((port, value)),
            Err(error)
                if error.retryable_early_exit
                    && attempt_index + 1 < SIDECAR_PORT_ATTEMPTS
                    && can_retry() =>
            {
                // A listener can claim the free port between discovery and the
                // sidecar bind. Only a process that exited during readiness is
                // retried, always on a newly-discovered port.
            }
            Err(error) => return Err(error.message),
        }
    }
    unreachable!("the bounded port-attempt loop always returns")
}

/// Kill and respawn the sidecar on its stable port. The caller must hold the
/// lifecycle mutex for this entire transition.
fn restart_sidecar_locked(app: &AppHandle, state: &RuntimeState) -> Result<String, String> {
    if state.shutting_down.load(Ordering::Acquire) {
        return Err("runtime is shutting down".into());
    }
    let preferred_port = *state.port.lock().unwrap();
    *state.url.lock().unwrap() = None;
    if let Err(error) = terminate_current_sidecar(state, "restart") {
        *state.port.lock().unwrap() = None;
        return Err(error);
    }
    if state.shutting_down.load(Ordering::Acquire) {
        *state.port.lock().unwrap() = None;
        return Err("runtime is shutting down".into());
    }
    let deadline = Instant::now() + SIDECAR_START_TIMEOUT;
    let (port, sidecar) = match start_with_port_retry(
        preferred_port,
        free_port,
        |port| spawn_sidecar(app, port, state, deadline),
        || {
            !state.shutting_down.load(Ordering::Acquire)
                && state.child.lock().unwrap().is_none()
                && Instant::now() < deadline
        },
    ) {
        Ok(started) => started,
        Err(error) => {
            *state.port.lock().unwrap() = None;
            return Err(error);
        }
    };
    if state.shutting_down.load(Ordering::Acquire) {
        *state.port.lock().unwrap() = None;
        return match terminate_unpublished(state, sidecar, "cancel restart during shutdown") {
            Ok(()) => Err("runtime is shutting down".into()),
            Err(error) => Err(format!("runtime is shutting down; {error}")),
        };
    }
    let url = format!("http://127.0.0.1:{port}");
    *state.port.lock().unwrap() = Some(port);
    publish_sidecar(state, sidecar);
    *state.url.lock().unwrap() = Some(url.clone());
    Ok(url)
}

fn config_transaction<T>(
    was_running: bool,
    stop: impl FnOnce() -> Result<(), String>,
    mutate: impl FnOnce() -> Result<T, String>,
    restart: impl FnOnce() -> Result<String, String>,
) -> Result<(T, Option<String>), String> {
    if was_running {
        stop()?;
    }
    let mutation = mutate();
    let restored = if was_running {
        restart().map(Some)
    } else {
        Ok(None)
    };
    match (mutation, restored) {
        (Ok(value), Ok(url)) => Ok((value, url)),
        (Err(mutation_error), Ok(_)) => Err(mutation_error),
        (Ok(_), Err(restart_error)) => Err(restart_error),
        (Err(mutation_error), Err(restart_error)) => Err(format!(
            "{mutation_error}; additionally failed to restore OpenCode: {restart_error}"
        )),
    }
}

/// Serialize one app-owned config mutation against both other commands and the
/// OpenCode process itself. The caller enters lifecycle first; a running
/// sidecar is fully stopped before config is locked, and is restored on its
/// stable port even when the mutation fails.
fn with_config_transaction<T>(
    app: &AppHandle,
    state: &RuntimeState,
    mutation: impl FnOnce() -> Result<T, String>,
) -> Result<(T, Option<String>), String> {
    with_lifecycle(&state.lifecycle, || {
        if state.shutting_down.load(Ordering::Acquire) {
            return Err("runtime is shutting down".into());
        }
        let was_running =
            state.url.lock().unwrap().is_some() || state.child.lock().unwrap().is_some();
        config_transaction(
            was_running,
            || {
                *state.url.lock().unwrap() = None;
                if let Err(error) = terminate_current_sidecar(state, "pause for config update") {
                    *state.port.lock().unwrap() = None;
                    return Err(error);
                }
                Ok(())
            },
            || {
                let _config_guard = state.config.lock().unwrap();
                mutation()
            },
            || restart_sidecar_locked(app, state),
        )
    })
}

/// Start the bundled OpenCode (idempotent). Returns its base URL. `async`:
/// skill-pack deployment + process spawn at startup must not block the UI
/// thread while the first window paints.
#[tauri::command(async)]
pub fn start_runtime(app: AppHandle, state: State<'_, RuntimeState>) -> Result<String, String> {
    start_once(
        &state.lifecycle,
        &state.url,
        || state.shutting_down.load(Ordering::Acquire),
        || {
            // A tombstone from an unconfirmed termination blocks every spawn.
            // A late watcher signal makes this check succeed without another kill.
            if state.child.lock().unwrap().is_some() {
                terminate_current_sidecar(&state, "finish previous termination")?;
            }
            let preferred_port = *state.port.lock().unwrap();
            let deadline = Instant::now() + SIDECAR_START_TIMEOUT;
            let (port, sidecar) = match start_with_port_retry(
                preferred_port,
                free_port,
                |port| spawn_sidecar(&app, port, &state, deadline),
                || {
                    !state.shutting_down.load(Ordering::Acquire)
                        && state.child.lock().unwrap().is_none()
                        && Instant::now() < deadline
                },
            ) {
                Ok(started) => started,
                Err(error) => {
                    *state.port.lock().unwrap() = None;
                    return Err(error);
                }
            };
            Ok((format!("http://127.0.0.1:{port}"), (port, sidecar)))
        },
        |(port, sidecar)| {
            *state.port.lock().unwrap() = Some(port);
            publish_sidecar(&state, sidecar);
        },
        |(_, sidecar)| {
            *state.port.lock().unwrap() = None;
            terminate_unpublished(&state, sidecar, "cancel startup during shutdown")
        },
    )
}

/// The workspace directory the sidecar runs in — the frontend passes it to the
/// SDK so skill discovery is scoped to the right OpenCode instance.
#[tauri::command]
pub fn workspace_path(app: AppHandle) -> Result<String, String> {
    Ok(workspace_dir(&app)?.to_string_lossy().to_string())
}

/// The base folder new dated workspaces are created under (`~/Documents/SparkAgent`).
#[tauri::command]
pub fn workspace_base(app: AppHandle) -> Result<String, String> {
    Ok(base_workspace_dir(&app)?.to_string_lossy().to_string())
}

/// Choose the base folder (Settings → Workspace → Change). Creates it if
/// needed and persists the choice; every NEW session's dated folder is created
/// under it. Existing sessions keep their folders.
#[tauri::command]
pub fn set_workspace_base(app: AppHandle, path: String) -> Result<String, String> {
    let dir = PathBuf::from(&path);
    if !dir.is_absolute() {
        return Err("workspace base must be absolute".into());
    }
    std::fs::create_dir_all(&dir).map_err(|e| format!("could not create folder: {e}"))?;
    let canon = dir.canonicalize().map_err(|e| e.to_string())?;
    std::fs::write(
        base_workspace_file(&app)?,
        canon.to_string_lossy().as_bytes(),
    )
    .map_err(|e| e.to_string())?;
    Ok(canon.to_string_lossy().to_string())
}

/// Reveal the base workspace folder in the OS file manager. (The sandboxed
/// `open_path` resolves inside the ACTIVE workspace only, which may be a dated
/// subfolder — the base needs its own door.)
#[tauri::command]
pub fn open_workspace_base(app: AppHandle) -> Result<(), String> {
    crate::artifact_file::os_open(&base_workspace_dir(&app)?)
}

/// Switch the active workspace folder: create it if needed and persist the
/// choice. The kernel / Files / provenance read the folder via `workspace_dir`;
/// the agent runtime is scoped per request — the frontend reconnects its event
/// stream with `?directory=` and creates sessions with it (a bare `/event`
/// stream would not see other folders' instances, so the scoped stream is
/// required). `path` must be absolute.
#[tauri::command(async)]
pub fn set_workspace(
    app: AppHandle,
    state: State<'_, RuntimeState>,
    path: String,
) -> Result<String, String> {
    let dir = PathBuf::from(&path);
    if !dir.is_absolute() {
        return Err("workspace path must be absolute".into());
    }
    std::fs::create_dir_all(&dir).map_err(|e| format!("could not create folder: {e}"))?;
    let canon = dir.canonicalize().map_err(|e| e.to_string())?;
    let active_file = active_workspace_file(&app)?;
    let previous = match std::fs::read(&active_file) {
        Ok(contents) => Some(contents),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(format!("failed to read {}: {error}", active_file.display())),
    };
    with_lifecycle(&state.lifecycle, || -> Result<(), String> {
        if state.shutting_down.load(Ordering::Acquire) {
            return Err("runtime is shutting down".into());
        }
        let has_url = state.url.lock().unwrap().is_some();
        let has_child = state.child.lock().unwrap().is_some();
        if has_url || has_child {
            if !has_url || !has_child {
                return Err("OpenCode runtime is not in a usable state".into());
            }
            let port = state
                .port
                .lock()
                .unwrap()
                .ok_or("OpenCode runtime has no published port")?;
            let config = read_optional_config(&effective_config_file(&app)?)?;
            let permission_mode = crate::opencode_config::effective_permission_mode(&config)?;
            validate_runtime_permission_floor(
                port,
                &canon,
                SIDECAR_POLICY_TIMEOUT,
                permission_mode,
                &managed_tool_output_permission_pattern(&app)?,
            )?;
        }

        // Startup/profile mutations use the same lock. Publish the workspace
        // choice and reconcile global skills as one app-owned operation before
        // the frontend creates or reconnects the directory-scoped instance.
        let _config_guard = state.config.lock().unwrap();
        write_private_atomic(&active_file, canon.to_string_lossy().as_bytes())?;
        if let Err(error) = reconcile_bundled_skills(&app) {
            let restore_file = match previous {
                Some(ref contents) => write_private_atomic(&active_file, contents),
                None => match std::fs::remove_file(&active_file) {
                    Ok(()) => Ok(()),
                    Err(remove_error) if remove_error.kind() == std::io::ErrorKind::NotFound => {
                        Ok(())
                    }
                    Err(remove_error) => Err(remove_error.to_string()),
                },
            };
            let restore_profile = restore_file.and_then(|()| reconcile_bundled_skills(&app));
            return match restore_profile {
                Ok(()) => Err(error),
                Err(restore_error) => Err(format!(
                    "{error}; additionally failed to restore prior workspace skills: {restore_error}"
                )),
            };
        }
        Ok(())
    })?;

    // No sidecar restart: OpenCode serves every folder from one process via
    // per-directory instances, and the frontend reconnects its event stream
    // with `?directory=<new folder>`. Restarting here used to cost 3-6 s per
    // history-session switch (process boot + reconnect polling). Bundled global
    // skills were reconciled above before the new directory instance is used.
    // Jupyter-lab, however, pins its root_dir at spawn time — re-root it (in
    // the background) so agent-created notebooks land in the new folder.
    crate::jupyter::reroot_jupyter(&app);
    // Refresh this session's local copy of the remote-machine list from the
    // canonical base file, so a machine configured in Settings is visible to
    // every session's agent without reaching outside the workspace.
    crate::compute::materialize_active(&app);
    Ok(canon.to_string_lossy().to_string())
}

/// Resolve and validate every agent immediately before a desktop turn. The
/// active directory must match the app-owned workspace choice so a caller
/// cannot validate one instance and send through another directory-scoped
/// client. This is a fail-closed compatibility gate, not an OS sandbox.
#[tauri::command(async)]
pub fn validate_runtime_permissions(
    app: AppHandle,
    state: State<'_, RuntimeState>,
    path: String,
) -> Result<(), String> {
    let requested = PathBuf::from(path);
    if !requested.is_absolute() {
        return Err("workspace path must be absolute".into());
    }
    let requested = requested
        .canonicalize()
        .map_err(|error| error.to_string())?;
    let active = workspace_dir(&app)?
        .canonicalize()
        .map_err(|error| error.to_string())?;
    if requested != active {
        return Err("permission validation path is not the active workspace".into());
    }
    with_lifecycle(&state.lifecycle, || {
        if state.shutting_down.load(Ordering::Acquire) {
            return Err("runtime is shutting down".into());
        }
        if state.url.lock().unwrap().is_none() || state.child.lock().unwrap().is_none() {
            return Err("OpenCode runtime is not running".into());
        }
        let port = state
            .port
            .lock()
            .unwrap()
            .ok_or("OpenCode runtime has no published port")?;
        let config = read_optional_config(&effective_config_file(&app)?)?;
        let permission_mode = crate::opencode_config::effective_permission_mode(&config)?;
        validate_runtime_permission_floor(
            port,
            &requested,
            SIDECAR_POLICY_TIMEOUT,
            permission_mode,
            &managed_tool_output_permission_pattern(&app)?,
        )
    })
}

/// Record which session owns the active workspace, so bundled skill helpers
/// (record_run.py) can stamp remote runs with their `sessionId` — the app knows
/// the id but the off-app helper only sees the workspace. Written as
/// `<workspace>/.openscience/session.txt`; best-effort, empty ids are ignored.
#[tauri::command]
pub fn mark_session(app: AppHandle, session_id: String) -> Result<(), String> {
    let id = session_id.trim();
    if id.is_empty() {
        return Ok(());
    }
    let dir = workspace_dir(&app)?.join(".openscience");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let path = dir.join("session.txt");
    // Write-then-rename so a concurrent read never sees a half-written id.
    let tmp = path.with_extension("txt.tmp");
    std::fs::write(&tmp, id).map_err(|e| e.to_string())?;
    if std::fs::rename(&tmp, &path).is_err() {
        let _ = std::fs::write(&path, id);
        let _ = std::fs::remove_file(&tmp);
    }
    Ok(())
}

/// Create a new dated folder `<base>/<name>` and switch to it. `name` is a
/// single path segment (the frontend supplies a timestamp); rejects separators.
#[tauri::command(async)]
pub fn new_dated_workspace(
    app: AppHandle,
    state: State<'_, RuntimeState>,
    name: String,
) -> Result<String, String> {
    if name.is_empty() || name.contains('/') || name.contains('\\') || name.contains("..") {
        return Err("invalid folder name".into());
    }
    let dir = base_workspace_dir(&app)?.join(&name);
    // `set_workspace` moves `app`; keep a handle to seed the harness afterwards.
    let seed_app = app.clone();
    let canon = set_workspace(app, state, dir.to_string_lossy().to_string())?;
    // Seed the agent harness into the fresh folder so it starts with its
    // operating rules, not an empty directory. Only NEW dated folders get seeded
    // (never `set_workspace` alone — switching to an existing session must not
    // re-plant the scaffold).
    crate::harness::seed_harness(&seed_app, std::path::Path::new(&canon));
    crate::git_snapshot::commit_best_effort(std::path::Path::new(&canon), "Initialize workspace");
    Ok(canon)
}

/// Native "choose a folder" dialog; returns the absolute path, or None on cancel.
#[tauri::command]
pub async fn pick_folder(app: AppHandle) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    let Some(picked) = app.dialog().file().blocking_pick_folder() else {
        return Ok(None);
    };
    let path = picked.into_path().map_err(|e| e.to_string())?;
    Ok(Some(path.to_string_lossy().to_string()))
}

/// Kill the bundled OpenCode if running.
#[tauri::command]
pub fn stop_runtime(state: State<'_, RuntimeState>) -> Result<(), String> {
    stop_runtime_inner(&state)
}

pub fn kill_child(state: &RuntimeState) {
    // Publish shutdown before waiting for lifecycle. A startup blocked on macOS
    // TCC observes this flag and terminates its unpublished process promptly.
    state.shutting_down.store(true, Ordering::Release);
    if let Err(error) = stop_runtime_inner(state) {
        eprintln!("OpenCode exit cleanup failed: {error}");
    }
}

fn stop_runtime_inner(state: &RuntimeState) -> Result<(), String> {
    with_lifecycle(&state.lifecycle, || {
        *state.url.lock().unwrap() = None;
        *state.port.lock().unwrap() = None;
        terminate_current_sidecar(state, "stop")
    })
}

#[cfg(test)]
mod tests {
    use super::{
        base64_encode, before_startup_deadline, bundled_sidecar_path_from,
        canonical_sandbox_subdir, config_transaction, current_sidecar_matches,
        discover_user_skill_names, fingerprint_agent_file, fingerprint_skill_dir,
        load_managed_profile_registry, opencode_sandbox_profile, parse_scutil_proxy,
        parse_sidecar_health_response, prune_stale_agents, prune_stale_skills, random_hex,
        recover_skill_replacement, register_skill_pack_names, remove_key_from_config,
        resolve_provisioning_proxy_env, resolve_proxy_env, seatbelt_path_literal, server_password,
        sidecar_health_ready, skill_manifest_name, start_once, start_with_port_retry,
        sync_managed_agent_pack, sync_managed_skill_pack, sync_skill_pack, terminate_checked,
        termination_outcome, validate_proxy_url, validate_resolved_agents,
        validate_runtime_permission_floor, wait_until_ready, with_lifecycle,
        write_managed_profile_registry, write_private_atomic, ManagedProfileRegistry,
        ResolvedAgent, SpawnAttemptError, EXPECTED_OPENCODE_VERSION, MANAGED_SCIENCE_MCP_DIR,
        UV_PYTHON_DIR,
    };
    use std::cell::{Cell, RefCell};
    use std::fs;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc, Barrier, Mutex,
    };
    use std::thread;
    use std::time::{Duration, Instant};

    #[test]
    fn bundled_sidecar_path_matches_tauri_sibling_and_test_deps_rules() {
        let app = std::path::Path::new("/Applications/Spark Agent.app/Contents/MacOS/spark-agent");
        let app_sidecar = bundled_sidecar_path_from(app, std::path::Path::new("opencode")).unwrap();
        let test = std::path::Path::new("/repo/target/debug/deps/runtime-a1b2c3");
        let test_sidecar =
            bundled_sidecar_path_from(test, std::path::Path::new("opencode")).unwrap();
        #[cfg(windows)]
        {
            assert_eq!(
                app_sidecar,
                std::path::Path::new("/Applications/Spark Agent.app/Contents/MacOS/opencode.exe")
            );
            assert_eq!(
                test_sidecar,
                std::path::Path::new("/repo/target/debug/opencode.exe")
            );
        }
        #[cfg(not(windows))]
        {
            assert_eq!(
                app_sidecar,
                std::path::Path::new("/Applications/Spark Agent.app/Contents/MacOS/opencode")
            );
            assert_eq!(
                test_sidecar,
                std::path::Path::new("/repo/target/debug/opencode")
            );
        }
        assert!(bundled_sidecar_path_from(
            std::path::Path::new("/"),
            std::path::Path::new("opencode")
        )
        .is_err());
    }

    #[test]
    fn sandbox_profile_is_narrow_and_escapes_paths() {
        let managed = std::path::Path::new("/private/tmp/managed\"quote\\slash");
        let uv_python = std::path::Path::new("/private/tmp/uv-python");
        assert_eq!(
            seatbelt_path_literal(managed).unwrap(),
            r#""/private/tmp/managed\"quote\\slash""#
        );
        let profile = opencode_sandbox_profile(managed, uv_python).unwrap();
        assert!(profile.starts_with("(version 1)\n(allow default)\n"));
        assert!(
            profile.contains(r#"(deny file-read* (subpath "/private/tmp/managed\"quote\\slash"))"#)
        );
        assert!(profile
            .contains(r#"(deny file-write* (subpath "/private/tmp/managed\"quote\\slash"))"#));
        assert!(profile.contains(r#"(deny file-write* (subpath "/private/tmp/uv-python"))"#));
        assert!(!profile.contains("deny process"));
        assert!(!profile.contains("deny network"));
        assert!(seatbelt_path_literal(std::path::Path::new("/private/tmp/bad\npath")).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn sandbox_profile_rejects_non_utf8_paths() {
        use std::os::unix::ffi::OsStringExt;
        let path = std::path::PathBuf::from(std::ffi::OsString::from_vec(vec![
            b'/', b't', b'm', b'p', b'/', 0xff,
        ]));
        assert!(seatbelt_path_literal(&path).is_err());
    }

    #[test]
    fn canonical_sandbox_roots_are_direct_runtime_children() {
        let tmp = std::env::temp_dir().join(format!(
            "spark-sandbox-roots-{}-{}",
            std::process::id(),
            random_hex(4)
        ));
        let runtime = tmp.join("runtime");
        let managed = canonical_sandbox_subdir(&runtime, MANAGED_SCIENCE_MCP_DIR).unwrap();
        let uv_python = canonical_sandbox_subdir(&runtime, UV_PYTHON_DIR).unwrap();
        let canonical_runtime = runtime.canonicalize().unwrap();
        assert_eq!(managed.parent(), Some(canonical_runtime.as_path()));
        assert_eq!(uv_python.parent(), Some(canonical_runtime.as_path()));
        assert_eq!(managed.file_name().unwrap(), MANAGED_SCIENCE_MCP_DIR);
        assert_eq!(uv_python.file_name().unwrap(), UV_PYTHON_DIR);
        fs::remove_dir_all(tmp).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn canonical_sandbox_root_rejects_a_leaf_symlink() {
        use std::os::unix::fs::symlink;
        let tmp = std::env::temp_dir().join(format!(
            "spark-sandbox-symlink-{}-{}",
            std::process::id(),
            random_hex(4)
        ));
        let runtime = tmp.join("runtime");
        let outside = tmp.join("outside");
        fs::create_dir_all(&runtime).unwrap();
        fs::create_dir_all(&outside).unwrap();
        symlink(&outside, runtime.join(MANAGED_SCIENCE_MCP_DIR)).unwrap();
        let error = canonical_sandbox_subdir(&runtime, MANAGED_SCIENCE_MCP_DIR).unwrap_err();
        assert!(error.contains("not a regular directory"));
        fs::remove_dir_all(tmp).unwrap();
    }

    #[cfg(target_os = "macos")]
    fn sandbox_output(
        profile: &str,
        program: &std::path::Path,
        args: &[&std::ffi::OsStr],
    ) -> std::process::Output {
        std::process::Command::new(super::SANDBOX_EXEC_PATH)
            .arg("-p")
            .arg(profile)
            .arg(program)
            .args(args)
            .output()
            .unwrap()
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn macos_sandbox_denies_managed_access_and_uv_writes_only() {
        use std::os::unix::fs::{symlink, PermissionsExt};

        let tmp = std::env::temp_dir().join(format!(
            "spark-sandbox-live-{}-{}",
            std::process::id(),
            random_hex(4)
        ));
        let runtime = tmp.join("runtime");
        let managed = canonical_sandbox_subdir(&runtime, MANAGED_SCIENCE_MCP_DIR).unwrap();
        let uv_python = canonical_sandbox_subdir(&runtime, UV_PYTHON_DIR).unwrap();
        let profile = opencode_sandbox_profile(&managed, &uv_python).unwrap();

        let managed_secret = managed.join("secret");
        fs::write(&managed_secret, "secret").unwrap();
        let uv_interpreter = uv_python.join("cpython/bin/python3");
        fs::create_dir_all(uv_interpreter.parent().unwrap()).unwrap();
        fs::write(&uv_interpreter, "#!/bin/sh\nprintf python-ok\n").unwrap();
        fs::set_permissions(&uv_interpreter, fs::Permissions::from_mode(0o755)).unwrap();
        let shared_python = runtime.join("science-mcp-env/bin/python");
        fs::create_dir_all(shared_python.parent().unwrap()).unwrap();
        symlink(&uv_interpreter, &shared_python).unwrap();

        let denied_read = sandbox_output(
            &profile,
            std::path::Path::new("/bin/cat"),
            &[managed_secret.as_os_str()],
        );
        assert!(!denied_read.status.success());
        let denied_managed_write = sandbox_output(
            &profile,
            std::path::Path::new("/usr/bin/touch"),
            &[managed.join("new-file").as_os_str()],
        );
        assert!(!denied_managed_write.status.success());

        let allowed_interpreter = sandbox_output(&profile, &shared_python, &[]);
        assert!(
            allowed_interpreter.status.success(),
            "shared interpreter could not read/execute its uv target: {}",
            String::from_utf8_lossy(&allowed_interpreter.stderr)
        );
        assert_eq!(allowed_interpreter.stdout, b"python-ok");
        let denied_symlink_target_write = sandbox_output(
            &profile,
            std::path::Path::new("/usr/bin/touch"),
            &[shared_python.as_os_str()],
        );
        assert!(
            !denied_symlink_target_write.status.success(),
            "the shared interpreter symlink bypassed the direct uv-python write deny"
        );

        let outside = runtime.join("outside");
        let allowed_outside_write = sandbox_output(
            &profile,
            std::path::Path::new("/usr/bin/touch"),
            &[outside.as_os_str()],
        );
        assert!(
            allowed_outside_write.status.success(),
            "narrow profile changed unrelated write access: {}",
            String::from_utf8_lossy(&allowed_outside_write.stderr)
        );
        fs::remove_dir_all(tmp).unwrap();
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn macos_sandbox_exec_preserves_the_exec_pid() {
        use std::process::Stdio;

        let child = std::process::Command::new(super::SANDBOX_EXEC_PATH)
            .arg("-p")
            .arg("(version 1)\n(allow default)")
            .arg("/bin/sh")
            .arg("-c")
            .arg("printf %s $$")
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let launcher_pid = child.id();
        let output = child.wait_with_output().unwrap();
        assert!(output.status.success());
        assert_eq!(
            String::from_utf8(output.stdout).unwrap(),
            launcher_pid.to_string()
        );
    }

    #[test]
    fn start_once_makes_concurrent_start_single_flight() {
        const CALLERS: usize = 8;
        let lifecycle = Arc::new(Mutex::new(()));
        let published_url = Arc::new(Mutex::new(None::<String>));
        let spawn_count = Arc::new(AtomicUsize::new(0));
        let publish_count = Arc::new(AtomicUsize::new(0));
        let rendezvous = Arc::new(Barrier::new(CALLERS));

        let handles: Vec<_> = (0..CALLERS)
            .map(|_| {
                let lifecycle = Arc::clone(&lifecycle);
                let published_url = Arc::clone(&published_url);
                let spawn_count = Arc::clone(&spawn_count);
                let publish_count = Arc::clone(&publish_count);
                let rendezvous = Arc::clone(&rendezvous);
                thread::spawn(move || {
                    rendezvous.wait();
                    start_once(
                        &lifecycle,
                        &published_url,
                        || false,
                        || {
                            spawn_count.fetch_add(1, Ordering::SeqCst);
                            Ok(("http://127.0.0.1:54321".to_string(), ()))
                        },
                        |_| {
                            publish_count.fetch_add(1, Ordering::SeqCst);
                        },
                        |_| Ok(()),
                    )
                    .unwrap()
                })
            })
            .collect();

        let urls: Vec<_> = handles
            .into_iter()
            .map(|handle| handle.join().unwrap())
            .collect();
        assert!(urls.iter().all(|url| url == &urls[0]));
        assert_eq!(spawn_count.load(Ordering::SeqCst), 1);
        assert_eq!(publish_count.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn start_once_failure_publishes_nothing_and_can_retry() {
        let lifecycle = Mutex::new(());
        let published_url = Mutex::new(None::<String>);
        let attempts = AtomicUsize::new(0);
        let publish_count = AtomicUsize::new(0);
        let published_payload = AtomicUsize::new(0);

        let first = start_once(
            &lifecycle,
            &published_url,
            || false,
            || {
                attempts.fetch_add(1, Ordering::SeqCst);
                Err::<(String, usize), _>("spawn failed".to_string())
            },
            |_| {
                publish_count.fetch_add(1, Ordering::SeqCst);
            },
            |_| Ok(()),
        );
        assert_eq!(first, Err("spawn failed".to_string()));
        assert!(published_url.lock().unwrap().is_none());
        assert_eq!(publish_count.load(Ordering::SeqCst), 0);

        let second = start_once(
            &lifecycle,
            &published_url,
            || false,
            || {
                attempts.fetch_add(1, Ordering::SeqCst);
                Ok(("http://127.0.0.1:54321".to_string(), 7))
            },
            |payload| {
                assert!(published_url.lock().unwrap().is_none());
                published_payload.store(payload, Ordering::SeqCst);
                publish_count.fetch_add(1, Ordering::SeqCst);
            },
            |_| Ok(()),
        );
        assert_eq!(second.as_deref(), Ok("http://127.0.0.1:54321"));
        assert_eq!(attempts.load(Ordering::SeqCst), 2);
        assert_eq!(publish_count.load(Ordering::SeqCst), 1);
        assert_eq!(published_payload.load(Ordering::SeqCst), 7);
        assert_eq!(
            published_url.lock().unwrap().as_deref(),
            Some("http://127.0.0.1:54321")
        );
    }

    #[test]
    fn queued_start_is_rejected_after_shutdown_begins() {
        let lifecycle = Arc::new(Mutex::new(()));
        let published_url = Arc::new(Mutex::new(None::<String>));
        let shutting_down = Arc::new(AtomicBool::new(false));
        let first_check = Arc::new(Barrier::new(2));
        let checks = Arc::new(AtomicUsize::new(0));
        let starts = Arc::new(AtomicUsize::new(0));
        let guard = lifecycle.lock().unwrap();

        let handle = {
            let lifecycle = Arc::clone(&lifecycle);
            let published_url = Arc::clone(&published_url);
            let shutting_down = Arc::clone(&shutting_down);
            let first_check = Arc::clone(&first_check);
            let checks = Arc::clone(&checks);
            let starts = Arc::clone(&starts);
            thread::spawn(move || {
                start_once(
                    &lifecycle,
                    &published_url,
                    || {
                        if checks.fetch_add(1, Ordering::SeqCst) == 0 {
                            first_check.wait();
                            false
                        } else {
                            shutting_down.load(Ordering::SeqCst)
                        }
                    },
                    || {
                        starts.fetch_add(1, Ordering::SeqCst);
                        Ok(("http://127.0.0.1:54321".to_string(), ()))
                    },
                    |_| {},
                    |_| Ok(()),
                )
            })
        };

        first_check.wait();
        shutting_down.store(true, Ordering::SeqCst);
        drop(guard);
        assert_eq!(
            handle.join().unwrap(),
            Err("runtime is shutting down".into())
        );
        assert_eq!(starts.load(Ordering::SeqCst), 0);
        assert!(published_url.lock().unwrap().is_none());
    }

    #[test]
    fn shutdown_after_start_discards_unpublished_payload() {
        let lifecycle = Mutex::new(());
        let published_url = Mutex::new(None::<String>);
        let checks = AtomicUsize::new(0);
        let discarded = AtomicUsize::new(0);
        let result = start_once(
            &lifecycle,
            &published_url,
            || checks.fetch_add(1, Ordering::SeqCst) >= 2,
            || Ok(("http://127.0.0.1:54321".to_string(), 9)),
            |_| panic!("shutdown payload must not be published"),
            |payload| {
                discarded.store(payload, Ordering::SeqCst);
                Ok(())
            },
        );
        assert_eq!(result, Err("runtime is shutting down".into()));
        assert_eq!(discarded.load(Ordering::SeqCst), 9);
        assert!(published_url.lock().unwrap().is_none());
    }

    #[test]
    fn readiness_is_bounded_and_observes_failure_and_shutdown() {
        let deadline = Instant::now();
        assert!(!before_startup_deadline(deadline, deadline));
        assert!(before_startup_deadline(
            deadline.checked_sub(Duration::from_nanos(1)).unwrap(),
            deadline
        ));

        let probes = Cell::new(0);
        wait_until_ready(
            || false,
            || None,
            || {
                probes.set(probes.get() + 1);
                Ok(probes.get() == 3)
            },
            || true,
        )
        .unwrap();
        assert_eq!(probes.get(), 3);

        let failure = wait_until_ready(
            || false,
            || Some("process exited".into()),
            || Ok(true),
            || true,
        );
        assert_eq!(failure, Err("process exited".into()));

        let timeout = wait_until_ready(|| false, || None, || Ok(false), || false);
        assert!(timeout.unwrap_err().contains("startup timeout"));

        let incompatible = wait_until_ready(
            || false,
            || None,
            || Err("version mismatch".into()),
            || true,
        );
        assert_eq!(incompatible, Err("version mismatch".into()));

        let shutdown = Cell::new(false);
        let cancelled = wait_until_ready(
            || shutdown.get(),
            || None,
            || Ok(false),
            || {
                shutdown.set(true);
                true
            },
        );
        assert!(cancelled.unwrap_err().contains("shutdown"));
    }

    #[test]
    fn health_probe_requires_authenticated_healthy_response() {
        assert_eq!(base64_encode(b"opencode:pw"), "b3BlbmNvZGU6cHc=");

        let serve = |body: &'static str| {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let port = listener.local_addr().unwrap().port();
            let expected = format!(
                "Authorization: Basic {}",
                base64_encode(format!("opencode:{}", server_password()).as_bytes())
            );
            let handle = thread::spawn(move || {
                let (mut stream, _) = listener.accept().unwrap();
                let mut request = Vec::new();
                let mut chunk = [0_u8; 512];
                while !request.ends_with(b"\r\n\r\n") {
                    let read = stream.read(&mut chunk).unwrap();
                    assert_ne!(read, 0);
                    request.extend_from_slice(&chunk[..read]);
                }
                assert!(String::from_utf8(request).unwrap().contains(&expected));
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .unwrap();
            });
            (port, handle)
        };

        let (healthy_port, healthy_server) = serve(r#"{"healthy": true, "version": "1.17.13"}"#);
        assert!(sidecar_health_ready(healthy_port).unwrap());
        healthy_server.join().unwrap();

        let (unhealthy_port, unhealthy_server) =
            serve(r#"{"healthy": false, "version": "1.17.13"}"#);
        assert!(!sidecar_health_ready(unhealthy_port).unwrap());
        unhealthy_server.join().unwrap();

        let (spoofed_port, spoofed_server) = serve(r#"{"message":"\"healthy\":true"}"#);
        assert!(!sidecar_health_ready(spoofed_port).unwrap());
        spoofed_server.join().unwrap();

        let (wrong_port, wrong_server) = serve(r#"{"healthy": true, "version": "1.17.12"}"#);
        assert!(sidecar_health_ready(wrong_port)
            .unwrap_err()
            .contains("expected 1.17.13, got 1.17.12"));
        wrong_server.join().unwrap();
    }

    #[test]
    fn health_protocol_requires_the_sdk_pinned_version() {
        let missing = "HTTP/1.1 200 OK\r\n\r\n{\"healthy\":true}";
        assert!(parse_sidecar_health_response(missing)
            .unwrap_err()
            .contains("got <missing>"));

        let sdk_types = include_str!("../../../../packages/sdk/src/types.ts");
        let expected_declaration =
            format!(r#"export const OPENCODE_VERSION = "{EXPECTED_OPENCODE_VERSION}";"#);
        assert!(sdk_types.lines().any(|line| line == expected_declaration));
    }

    fn resolved_research_agent(
        permission_mode: &str,
        extra_rules: Vec<serde_json::Value>,
    ) -> Vec<ResolvedAgent> {
        let mut rules = vec![
            serde_json::json!({"permission":"*","pattern":"*","action":"allow"}),
            serde_json::json!({"permission":"read","pattern":"*","action":"allow"}),
            serde_json::json!({"permission":"read","pattern":"*.env","action":"ask"}),
            serde_json::json!({"permission":"read","pattern":"*.env.*","action":"ask"}),
            serde_json::json!({"permission":"read","pattern":"*.env.example","action":"allow"}),
            serde_json::json!({"permission":"read","pattern":"mcp:*","action":"ask"}),
            serde_json::json!({"permission":"glob","pattern":"*","action":"allow"}),
            serde_json::json!({"permission":"grep","pattern":"*","action":"allow"}),
            serde_json::json!({"permission":"list","pattern":"*","action":"allow"}),
            serde_json::json!({"permission":"lsp","pattern":"*","action":"allow"}),
            serde_json::json!({"permission":"question","pattern":"*","action":"allow"}),
            serde_json::json!({"permission":"skill","pattern":"*","action":"allow"}),
            serde_json::json!({"permission":"task","pattern":"*","action":"allow"}),
            serde_json::json!({"permission":"edit","pattern":"*","action":"allow"}),
            serde_json::json!({"permission":"write","pattern":"*","action":"allow"}),
            serde_json::json!({"permission":"patch","pattern":"*","action":"allow"}),
            serde_json::json!({"permission":"apply_patch","pattern":"*","action":"allow"}),
            serde_json::json!({"permission":"bash","pattern":"*","action":"allow"}),
            serde_json::json!({"permission":"external_directory","pattern":"*","action":"deny"}),
        ];
        if permission_mode == crate::opencode_config::MODE_BALANCED {
            rules.extend([
                serde_json::json!({"permission":"*","pattern":"*","action":"ask"}),
                serde_json::json!({"permission":"edit","pattern":"*","action":"ask"}),
                serde_json::json!({"permission":"apply_patch","pattern":"*","action":"ask"}),
                serde_json::json!({"permission":"bash","pattern":"*","action":"ask"}),
                serde_json::json!({"permission":"webfetch","pattern":"*","action":"ask"}),
                serde_json::json!({"permission":"websearch","pattern":"*","action":"ask"}),
                serde_json::json!({"permission":"mcp","pattern":"*","action":"ask"}),
                serde_json::json!({"permission":"external_directory","pattern":"*","action":"deny"}),
            ]);
        } else {
            rules.extend([
                serde_json::json!({"permission":"*","pattern":"*","action":"ask"}),
                serde_json::json!({"permission":"edit","pattern":"*","action":"allow"}),
                serde_json::json!({"permission":"write","pattern":"*","action":"allow"}),
                serde_json::json!({"permission":"patch","pattern":"*","action":"allow"}),
                serde_json::json!({"permission":"apply_patch","pattern":"*","action":"allow"}),
                serde_json::json!({"permission":"bash","pattern":"*","action":"allow"}),
                serde_json::json!({"permission":"bash","pattern":"rm *","action":"ask"}),
                serde_json::json!({"permission":"bash","pattern":"* rm *","action":"ask"}),
                serde_json::json!({"permission":"bash","pattern":"pip install *","action":"ask"}),
                serde_json::json!({"permission":"bash","pattern":"* pip install *","action":"ask"}),
                serde_json::json!({"permission":"bash","pattern":"git push *","action":"ask"}),
                serde_json::json!({"permission":"bash","pattern":"* git push *","action":"ask"}),
                serde_json::json!({"permission":"bash","pattern":"ssh *","action":"ask"}),
                serde_json::json!({"permission":"bash","pattern":"* ssh *","action":"ask"}),
                serde_json::json!({"permission":"bash","pattern":"curl * --upload-file *","action":"ask"}),
                serde_json::json!({"permission":"bash","pattern":"* curl * --upload-file *","action":"ask"}),
                serde_json::json!({"permission":"bash","pattern":"curl --upload-file *","action":"ask"}),
                serde_json::json!({"permission":"bash","pattern":"* curl --upload-file *","action":"ask"}),
                serde_json::json!({"permission":"bash","pattern":"sudo *","action":"deny"}),
                serde_json::json!({"permission":"bash","pattern":"* sudo *","action":"deny"}),
                serde_json::json!({"permission":"bash","pattern":"systemctl *","action":"deny"}),
                serde_json::json!({"permission":"bash","pattern":"* systemctl *","action":"deny"}),
                serde_json::json!({"permission":"webfetch","pattern":"*","action":"allow"}),
                serde_json::json!({"permission":"websearch","pattern":"*","action":"allow"}),
                serde_json::json!({"permission":"mcp","pattern":"*","action":"allow"}),
                serde_json::json!({"permission":"paper-search_*","pattern":"*","action":"allow"}),
                serde_json::json!({"permission":"skill","pattern":"*","action":"allow"}),
                serde_json::json!({"permission":"task","pattern":"*","action":"allow"}),
                serde_json::json!({"permission":"todowrite","pattern":"*","action":"allow"}),
                serde_json::json!({"permission":"external_directory","pattern":"*","action":"deny"}),
            ]);
        }
        rules.extend([
            serde_json::json!({"permission":"read","pattern":"*","action":"allow"}),
            serde_json::json!({"permission":"read","pattern":"*.env","action":"ask"}),
            serde_json::json!({"permission":"read","pattern":"*.env.*","action":"ask"}),
            serde_json::json!({"permission":"read","pattern":"*.env.example","action":"allow"}),
            serde_json::json!({"permission":"read","pattern":"mcp:*","action":"ask"}),
        ]);
        rules.extend(extra_rules);
        serde_json::from_value(serde_json::json!([{
            "name": "research",
            "permission": rules
        }]))
        .unwrap()
    }

    #[test]
    fn policy_http_client_is_safe_inside_an_async_runtime() {
        let agents = resolved_research_agent(crate::opencode_config::MODE_BALANCED, Vec::new());
        let body = serde_json::json!([{
            "name": &agents[0].name,
            "permission": agents[0]
                .permission
                .iter()
                .map(|rule| serde_json::json!({
                    "permission": &rule.permission,
                    "pattern": &rule.pattern,
                    "action": &rule.action,
                }))
                .collect::<Vec<_>>(),
        }])
        .to_string();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            let mut chunk = [0_u8; 512];
            while !request.ends_with(b"\r\n\r\n") {
                let read = stream.read(&mut chunk).unwrap();
                assert_ne!(read, 0);
                request.extend_from_slice(&chunk[..read]);
            }
            assert!(String::from_utf8(request)
                .unwrap()
                .starts_with("GET /agent?directory="));
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });

        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(async {
                validate_runtime_permission_floor(
                    port,
                    std::path::Path::new("/tmp/spark policy"),
                    Duration::from_secs(2),
                    crate::opencode_config::MODE_BALANCED,
                    "/private/spark/xdg-data/opencode/tool-output/*",
                )
                .unwrap();
            });
        server.join().unwrap();
    }

    #[test]
    fn resolved_agent_validation_accepts_balanced_and_autonomous_native_policies() {
        validate_resolved_agents(
            &resolved_research_agent(crate::opencode_config::MODE_BALANCED, Vec::new()),
            crate::opencode_config::MODE_BALANCED,
            "/private/spark/xdg-data/opencode/tool-output/*",
        )
        .unwrap();
        validate_resolved_agents(
            &resolved_research_agent(crate::opencode_config::MODE_FULL, Vec::new()),
            crate::opencode_config::MODE_FULL,
            "/private/spark/xdg-data/opencode/tool-output/*",
        )
        .unwrap();
    }

    #[test]
    fn resolved_agent_validation_accepts_only_the_managed_tool_output_directory() {
        let managed = "/private/spark/xdg-data/opencode/tool-output/*";
        let managed_rule = serde_json::json!({
            "permission":"external_directory",
            "pattern": managed,
            "action":"allow"
        });
        validate_resolved_agents(
            &resolved_research_agent(
                crate::opencode_config::MODE_BALANCED,
                vec![managed_rule.clone()],
            ),
            crate::opencode_config::MODE_BALANCED,
            managed,
        )
        .unwrap();

        let error = validate_resolved_agents(
            &resolved_research_agent(
                crate::opencode_config::MODE_BALANCED,
                vec![serde_json::json!({
                    "permission":"external_directory",
                    "pattern":"/private/spark/xdg-data/opencode/tool-output-copy/*",
                    "action":"allow"
                })],
            ),
            crate::opencode_config::MODE_BALANCED,
            managed,
        )
        .unwrap_err();
        assert!(error.contains("unsafe allow"));
    }

    #[test]
    fn balanced_validation_rejects_late_network_unknown_and_dangerous_allows() {
        for (permission, pattern) in [
            ("bash", "rm *"),
            ("bash", "pip install *"),
            ("webfetch", "*"),
            ("spark-policy-unknown-tool", "*"),
        ] {
            let error = validate_resolved_agents(
                &resolved_research_agent(
                    crate::opencode_config::MODE_BALANCED,
                    vec![serde_json::json!({
                        "permission": permission,
                        "pattern": pattern,
                        "action": "allow"
                    })],
                ),
                crate::opencode_config::MODE_BALANCED,
                "/private/spark/xdg-data/opencode/tool-output/*",
            )
            .unwrap_err();
            assert!(error.contains("unsafe allow"), "{permission}: {error}");
        }
    }

    #[test]
    fn autonomous_validation_rejects_destructive_and_external_bypasses() {
        for (permission, pattern) in [
            ("bash", "rm *"),
            ("bash", "pip install *"),
            ("bash", "git push *"),
            ("bash", "sudo *"),
            ("bash", "systemctl *"),
        ] {
            let error = validate_resolved_agents(
                &resolved_research_agent(
                    crate::opencode_config::MODE_FULL,
                    vec![serde_json::json!({
                        "permission": permission,
                        "pattern": pattern,
                        "action": "allow"
                    })],
                ),
                crate::opencode_config::MODE_FULL,
                "/private/spark/xdg-data/opencode/tool-output/*",
            )
            .unwrap_err();
            assert!(error.contains("resolves"), "{permission}: {error}");
        }
    }

    #[test]
    fn autonomous_validation_accepts_late_ordinary_tool_allows() {
        validate_resolved_agents(
            &resolved_research_agent(
                crate::opencode_config::MODE_FULL,
                vec![
                    serde_json::json!({"permission":"bash","pattern":"python *","action":"allow"}),
                    serde_json::json!({"permission":"webfetch","pattern":"*","action":"allow"}),
                    serde_json::json!({"permission":"lab_search","pattern":"*","action":"allow"}),
                ],
            ),
            crate::opencode_config::MODE_FULL,
            "/private/spark/xdg-data/opencode/tool-output/*",
        )
        .unwrap();
    }

    #[test]
    fn resolved_agent_validation_rejects_sensitive_read_and_external_bypasses() {
        let read_error = validate_resolved_agents(
            &resolved_research_agent(
                crate::opencode_config::MODE_BALANCED,
                vec![serde_json::json!({"permission":"read","pattern":"*","action":"allow"})],
            ),
            crate::opencode_config::MODE_BALANCED,
            "/private/spark/xdg-data/opencode/tool-output/*",
        )
        .unwrap_err();
        assert!(read_error.contains("read allow after the sensitive-file floor"));

        let external_error = validate_resolved_agents(
            &resolved_research_agent(
                crate::opencode_config::MODE_BALANCED,
                vec![serde_json::json!({
                    "permission":"external_directory",
                    "pattern":"*",
                    "action":"allow"
                })],
            ),
            crate::opencode_config::MODE_BALANCED,
            "/private/spark/xdg-data/opencode/tool-output/*",
        )
        .unwrap_err();
        assert!(external_error.contains("unsafe allow"));
    }

    #[test]
    fn termination_errors_include_action_and_pid() {
        let error = terminate_checked((), 4242, "restart", |_| Err("denied")).unwrap_err();
        assert!(error.contains("restart"));
        assert!(error.contains("4242"));
        assert!(error.contains("denied"));
    }

    #[test]
    fn unconfirmed_termination_retains_ownership_until_exit_is_proven() {
        let failure = termination_outcome(7, 4242, "stop", Ok(()), false).unwrap_err();
        assert_eq!(failure.retained, 7);
        assert!(failure.message.contains("4242"));
        assert!(failure.message.contains("remains unconfirmed"));

        // An observed exit is authoritative even when kill raced an already
        // exiting process and returned an error.
        assert!(termination_outcome(9, 4242, "stop", Err("already exited".into()), true).is_ok());
    }

    #[test]
    fn early_exit_retries_on_fresh_ports_but_fatal_failures_do_not() {
        let attempted = RefCell::new(Vec::new());
        let fresh = RefCell::new(vec![41001_u16, 41002].into_iter());
        let failures = Cell::new(0);
        let started = start_with_port_retry(
            Some(41000),
            || Ok(fresh.borrow_mut().next().unwrap()),
            |port| {
                attempted.borrow_mut().push(port);
                if failures.get() < 2 {
                    failures.set(failures.get() + 1);
                    Err(SpawnAttemptError {
                        message: "process exited".into(),
                        retryable_early_exit: true,
                    })
                } else {
                    Ok("ready")
                }
            },
            || true,
        )
        .unwrap();
        assert_eq!(started, (41002, "ready"));
        assert_eq!(*attempted.borrow(), [41000, 41001, 41002]);

        let fatal_attempts = Cell::new(0);
        let fatal = start_with_port_retry(
            None,
            || Ok(42000),
            |_| {
                fatal_attempts.set(fatal_attempts.get() + 1);
                Err::<(), _>(SpawnAttemptError::fatal("version mismatch"))
            },
            || true,
        );
        assert_eq!(fatal, Err("version mismatch".into()));
        assert_eq!(fatal_attempts.get(), 1);

        let guarded_attempts = Cell::new(0);
        let guarded = start_with_port_retry(
            None,
            || Ok(43000),
            |_| {
                guarded_attempts.set(guarded_attempts.get() + 1);
                Err::<(), _>(SpawnAttemptError {
                    message: "early exit".into(),
                    retryable_early_exit: true,
                })
            },
            || false,
        );
        assert_eq!(guarded, Err("early exit".into()));
        assert_eq!(guarded_attempts.get(), 1);
    }

    #[test]
    fn stale_watcher_never_matches_new_generation() {
        assert!(current_sidecar_matches(Some(11), Some(4), 11, 4));
        assert!(!current_sidecar_matches(Some(11), Some(5), 11, 4));
        assert!(!current_sidecar_matches(Some(12), Some(4), 11, 4));
    }

    #[test]
    fn serialized_atomic_config_updates_preserve_every_writer() {
        const WRITERS: usize = 8;
        let dir = std::env::temp_dir().join(format!("runtime-config-{}", random_hex(8)));
        let path = dir.join("opencode.json");
        write_private_atomic(&path, b"{}").unwrap();
        let lifecycle = Arc::new(Mutex::new(()));
        let config = Arc::new(Mutex::new(()));
        let path = Arc::new(path);

        let handles: Vec<_> = (0..WRITERS)
            .map(|writer| {
                let lifecycle = Arc::clone(&lifecycle);
                let config = Arc::clone(&config);
                let path = Arc::clone(&path);
                thread::spawn(move || {
                    with_lifecycle(&lifecycle, || {
                        let _config_guard = config.lock().unwrap();
                        let mut value: serde_json::Value =
                            serde_json::from_str(&fs::read_to_string(&*path).unwrap()).unwrap();
                        value
                            .as_object_mut()
                            .unwrap()
                            .insert(format!("writer-{writer}"), writer.into());
                        write_private_atomic(
                            &path,
                            serde_json::to_string(&value).unwrap().as_bytes(),
                        )
                        .unwrap();
                    });
                })
            })
            .collect();
        for handle in handles {
            handle.join().unwrap();
        }

        let value: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(&*path).unwrap()).unwrap();
        assert_eq!(value.as_object().unwrap().len(), WRITERS);
        assert!(fs::read_dir(&dir).unwrap().all(|entry| !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .ends_with(".tmp")));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(&*path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn config_transaction_stops_before_mutation_then_restores() {
        let events = RefCell::new(Vec::new());
        let result = config_transaction(
            true,
            || {
                events.borrow_mut().push("stop");
                Ok(())
            },
            || {
                events.borrow_mut().push("mutate");
                Ok(7)
            },
            || {
                events.borrow_mut().push("restart");
                Ok("http://127.0.0.1:54321".into())
            },
        )
        .unwrap();
        assert_eq!(result, (7, Some("http://127.0.0.1:54321".to_string())));
        assert_eq!(*events.borrow(), ["stop", "mutate", "restart"]);
    }

    #[test]
    fn restart_result_serializes_the_authoritative_runtime_url() {
        let value = serde_json::to_value(super::RuntimeRestartResult {
            runtime_url: Some("http://127.0.0.1:43123".into()),
        })
        .unwrap();
        assert_eq!(
            value,
            serde_json::json!({ "runtimeUrl": "http://127.0.0.1:43123" })
        );
    }

    #[test]
    fn config_transaction_never_mutates_behind_an_unconfirmed_stop() {
        let mutated = Cell::new(false);
        let result = config_transaction(
            true,
            || Err("termination remains unconfirmed".into()),
            || {
                mutated.set(true);
                Ok(())
            },
            || Ok("http://127.0.0.1:54321".into()),
        );
        assert_eq!(result, Err("termination remains unconfirmed".into()));
        assert!(!mutated.get());
    }

    #[test]
    fn config_transaction_restores_after_mutation_failure_and_combines_errors() {
        let events = RefCell::new(Vec::new());
        let restored = config_transaction::<()>(
            true,
            || {
                events.borrow_mut().push("stop");
                Ok(())
            },
            || {
                events.borrow_mut().push("mutate");
                Err("mutation failed".into())
            },
            || {
                events.borrow_mut().push("restart");
                Ok("http://127.0.0.1:54321".into())
            },
        );
        assert_eq!(restored, Err("mutation failed".into()));
        assert_eq!(*events.borrow(), ["stop", "mutate", "restart"]);

        let combined = config_transaction::<()>(
            true,
            || Ok(()),
            || Err("mutation failed".into()),
            || Err("restart failed".into()),
        )
        .unwrap_err();
        assert!(combined.starts_with("mutation failed"));
        assert!(combined.contains("restart failed"));
    }

    #[test]
    fn proxy_url_validation() {
        assert!(validate_proxy_url("http://127.0.0.1:7890").is_ok());
        assert!(validate_proxy_url("socks5://10.0.0.2:1080").is_ok());
        assert!(validate_proxy_url("http://[::1]:8080").is_ok());
        assert!(validate_proxy_url("127.0.0.1:7890").is_err()); // no scheme
        assert!(validate_proxy_url("http://host").is_err()); // no port
        assert!(validate_proxy_url("http://:7890").is_err()); // no host
        assert!(validate_proxy_url("ftp://h:1").is_err()); // wrong scheme
    }

    #[test]
    fn proxy_env_modes() {
        let none = resolve_proxy_env("none", "");
        assert!(none.iter().any(|(k, v)| *k == "NO_PROXY" && v == "*"));
        assert!(none
            .iter()
            .any(|(k, v)| *k == "HTTPS_PROXY" && v.is_empty()));

        let custom = resolve_proxy_env("custom", "http://127.0.0.1:7890");
        assert!(custom
            .iter()
            .any(|(k, v)| *k == "HTTPS_PROXY" && v == "http://127.0.0.1:7890"));
        assert!(custom
            .iter()
            .any(|(k, v)| *k == "NO_PROXY" && v.contains("127.0.0.1")));
    }

    #[test]
    fn provisioning_proxy_env_forwards_only_the_explicit_allowlist() {
        let inherited = vec![
            ("HTTP_PROXY".to_string(), "http://upper-http:1".to_string()),
            (
                "HTTPS_PROXY".to_string(),
                "http://upper-https:2".to_string(),
            ),
            ("http_proxy".to_string(), "http://lower-http:3".to_string()),
            (
                "https_proxy".to_string(),
                "http://lower-https:4".to_string(),
            ),
            ("ALL_PROXY".to_string(), "socks5://upper-all:5".to_string()),
            ("all_proxy".to_string(), "socks5://lower-all:6".to_string()),
            ("NO_PROXY".to_string(), "upper.example".to_string()),
            ("no_proxy".to_string(), "lower.example".to_string()),
            (
                "PIP_INDEX_URL".to_string(),
                "https://untrusted.invalid/simple".to_string(),
            ),
            (
                "UV_CONFIG_FILE".to_string(),
                "/tmp/untrusted.toml".to_string(),
            ),
            ("RUSTFLAGS".to_string(), "--cfg injected".to_string()),
        ];
        let forwarded = resolve_provisioning_proxy_env("system", "", inherited, || {
            panic!("system proxy must not be read when an allowlisted env value exists")
        });
        assert_eq!(
            forwarded,
            vec![
                ("HTTP_PROXY", "http://upper-http:1".to_string()),
                ("HTTPS_PROXY", "http://upper-https:2".to_string()),
                ("http_proxy", "http://lower-http:3".to_string()),
                ("https_proxy", "http://lower-https:4".to_string()),
                ("ALL_PROXY", "socks5://upper-all:5".to_string()),
                ("all_proxy", "socks5://lower-all:6".to_string()),
                ("NO_PROXY", "upper.example".to_string()),
                ("no_proxy", "lower.example".to_string()),
            ]
        );
        assert!(!forwarded
            .iter()
            .any(|(key, _)| { matches!(*key, "PIP_INDEX_URL" | "UV_CONFIG_FILE" | "RUSTFLAGS") }));

        let fallback = resolve_provisioning_proxy_env("system", "", Vec::new(), || {
            Some("http://system-proxy:7890".to_string())
        });
        assert_eq!(
            fallback,
            vec![
                ("HTTP_PROXY", "http://system-proxy:7890".to_string()),
                ("HTTPS_PROXY", "http://system-proxy:7890".to_string()),
                ("NO_PROXY", "localhost,127.0.0.1,::1".to_string()),
            ]
        );

        let custom = resolve_provisioning_proxy_env(
            "custom",
            "http://custom:8080",
            vec![("HTTP_PROXY".to_string(), "http://ignored:1".to_string())],
            || panic!("custom mode has no system fallback"),
        );
        assert_eq!(custom, resolve_proxy_env("custom", "http://custom:8080"));
        let none = resolve_provisioning_proxy_env(
            "none",
            "",
            vec![("HTTP_PROXY".to_string(), "http://ignored:1".to_string())],
            || panic!("none mode has no system fallback"),
        );
        assert_eq!(none, resolve_proxy_env("none", ""));
    }

    #[test]
    fn scutil_proxy_parses_and_prefers_https() {
        // Real `scutil --proxy` shape (indented `Key : value` lines).
        let all = "<dictionary> {\n  HTTPEnable : 1\n  HTTPPort : 1087\n  HTTPProxy : 127.0.0.1\n  HTTPSEnable : 1\n  HTTPSPort : 1087\n  HTTPSProxy : 127.0.0.1\n  SOCKSEnable : 1\n  SOCKSPort : 1087\n  SOCKSProxy : 127.0.0.1\n}";
        assert_eq!(
            parse_scutil_proxy(all).as_deref(),
            Some("http://127.0.0.1:1087")
        );
        let socks_only = "  SOCKSEnable : 1\n  SOCKSPort : 7890\n  SOCKSProxy : 10.0.0.2\n";
        assert_eq!(
            parse_scutil_proxy(socks_only).as_deref(),
            Some("socks5://10.0.0.2:7890")
        );
        let disabled = "  HTTPEnable : 0\n  HTTPPort : 1087\n  HTTPProxy : 127.0.0.1\n";
        assert_eq!(parse_scutil_proxy(disabled), None);
        assert_eq!(parse_scutil_proxy(""), None);
    }

    #[test]
    fn prune_removes_only_stale_skill_dirs() {
        let dst = std::env::temp_dir().join(format!("os-prune-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dst);
        for name in ["remote-compute", "hpc-slurm", "edited-stale", "user-global"] {
            fs::create_dir_all(dst.join(name)).unwrap();
            fs::write(dst.join(name).join("SKILL.md"), b"---\n").unwrap();
        }
        let edited_fingerprint = fingerprint_skill_dir(&dst.join("edited-stale")).unwrap();
        fs::write(
            dst.join("edited-stale").join("SKILL.md"),
            b"user changed this\n",
        )
        .unwrap();
        // A directory without a SKILL.md must never be touched.
        fs::create_dir_all(dst.join("notes")).unwrap();
        // Hidden directories belong to no stale-skill namespace. In
        // particular, malformed recovery-like names must not be pruned by the
        // broader stale skill cleanup.
        fs::create_dir_all(dst.join(".hpc-slurm.not-ours.backup")).unwrap();
        fs::write(
            dst.join(".hpc-slurm.not-ours.backup").join("SKILL.md"),
            b"---\n",
        )
        .unwrap();
        let stale_recovery = dst.join(".hpc-slurm.1111111111111111.backup");
        let current_recovery = dst.join(".remote-compute.2222222222222222.backup");
        let edited_recovery = dst.join(".edited-stale.3333333333333333.backup");
        fs::create_dir_all(&stale_recovery).unwrap();
        fs::create_dir_all(&current_recovery).unwrap();
        fs::create_dir_all(&edited_recovery).unwrap();

        let previous = std::collections::BTreeMap::from([
            (
                "remote-compute".to_string(),
                fingerprint_skill_dir(&dst.join("remote-compute")).unwrap(),
            ),
            (
                "hpc-slurm".to_string(),
                fingerprint_skill_dir(&dst.join("hpc-slurm")).unwrap(),
            ),
            ("edited-stale".to_string(), edited_fingerprint),
        ]);
        let current = std::collections::BTreeMap::from([(
            "remote-compute".to_string(),
            fingerprint_skill_dir(&dst.join("remote-compute")).unwrap(),
        )]);
        prune_stale_skills(&dst, &previous, &current).unwrap();

        assert!(dst.join("remote-compute").is_dir(), "bundled skill kept");
        assert!(
            !dst.join("hpc-slurm").exists(),
            "stale renamed skill removed"
        );
        assert!(dst.join("notes").is_dir(), "non-skill dir left alone");
        assert!(
            dst.join("user-global").is_dir(),
            "unregistered user-global skill left alone"
        );
        assert!(
            dst.join("edited-stale").is_dir(),
            "modified formerly managed skill becomes user-owned"
        );
        assert!(
            dst.join(".hpc-slurm.not-ours.backup").is_dir(),
            "hidden dir left alone"
        );
        assert!(
            !stale_recovery.exists(),
            "exact recovery artifact for removed skill pruned"
        );
        assert!(
            current_recovery.exists(),
            "recovery artifact for current skill left to recovery"
        );
        assert!(
            edited_recovery.exists(),
            "recovery artifact is retained when ownership proof no longer matches"
        );
        let _ = fs::remove_dir_all(&dst);
    }

    fn write_skill_manifest(path: &std::path::Path, name: &str) {
        write(
            path,
            &format!("---\nname: {name}\ndescription: Test skill.\n---\n\n# {name}\n"),
        );
    }

    #[test]
    fn discovers_project_and_user_global_skill_names_without_counting_managed_targets() {
        let tmp = skill_test_root("skill-collision-discovery");
        let workspace = tmp.join("workspace");
        let profile = tmp.join("profile");
        write_skill_manifest(
            &workspace.join(".opencode/skill/literature/SKILL.md"),
            "literature-review",
        );
        write_skill_manifest(
            &workspace.join(".opencode/skills/team/nested/SKILL.md"),
            "statistical-analysis",
        );
        write_skill_manifest(&profile.join("skill/writer/SKILL.md"), "scientific-writing");
        write_skill_manifest(
            &profile.join("skills/group/nested/SKILL.md"),
            "publication-figures",
        );
        // Direct plural-global entries are deployment destinations and are
        // resolved by sync_managed_skill_pack, not counted as a second copy.
        write_skill_manifest(
            &profile.join("skills/citation-management/SKILL.md"),
            "citation-management",
        );
        // A direct entry whose declared name differs from its destination can
        // collide with another bundle and therefore must be discovered.
        write_skill_manifest(
            &profile.join("skills/renamed-target/SKILL.md"),
            "exploratory-data-analysis",
        );
        // A verified managed tree is entirely excluded from user discovery.
        write_skill_manifest(&profile.join("skills/managed/SKILL.md"), "domain-check");
        write_skill_manifest(
            &profile.join("skills/managed/nested/SKILL.md"),
            "remote-compute",
        );
        let managed = std::collections::BTreeMap::from([(
            "managed".to_string(),
            fingerprint_skill_dir(&profile.join("skills/managed")).unwrap(),
        )]);
        // Name without the required description is not a valid skill.
        write(
            &workspace.join(".opencode/skills/invalid/SKILL.md"),
            "---\nname: exploratory-data-analysis\n---\n",
        );

        let names = discover_user_skill_names(&workspace, &profile, &managed).unwrap();
        assert_eq!(
            names,
            std::collections::BTreeSet::from([
                "exploratory-data-analysis".to_string(),
                "literature-review".to_string(),
                "publication-figures".to_string(),
                "scientific-writing".to_string(),
                "statistical-analysis".to_string(),
            ])
        );
        fs::remove_dir_all(tmp).unwrap();
    }

    #[test]
    fn project_collision_relinquishes_and_prunes_only_proven_bundled_skill() {
        let tmp = skill_test_root("skill-collision-enforcement");
        let src = tmp.join("src");
        let dst = tmp.join("dst");
        write_skill_manifest(&src.join("literature-review/SKILL.md"), "literature-review");
        write_skill_manifest(&dst.join("literature-review/SKILL.md"), "literature-review");
        let previous = std::collections::BTreeMap::from([(
            "literature-review".to_string(),
            fingerprint_skill_dir(&dst.join("literature-review")).unwrap(),
        )]);
        let mut ownership = previous.clone();
        let collisions = std::collections::BTreeSet::from(["literature-review".to_string()]);

        let (_, current) =
            sync_managed_skill_pack(&src, &dst, &mut ownership, false, &collisions).unwrap();
        assert!(current.is_empty());
        assert!(ownership.is_empty());
        prune_stale_skills(&dst, &previous, &current).unwrap();
        assert!(!dst.join("literature-review").exists());
        fs::remove_dir_all(tmp).unwrap();
    }

    #[test]
    fn skill_manifest_parser_accepts_quoted_names_and_rejects_invalid_frontmatter() {
        let tmp = skill_test_root("skill-manifest-parser");
        let quoted = tmp.join("quoted.md");
        write(
            &quoted,
            "---\nname: \"literature-review\"\ndescription: A valid skill.\n---\n",
        );
        assert_eq!(
            skill_manifest_name(&quoted).unwrap().as_deref(),
            Some("literature-review")
        );
        let duplicate = tmp.join("duplicate.md");
        write(
            &duplicate,
            "---\nname: first\nname: second\ndescription: Invalid.\n---\n",
        );
        assert_eq!(skill_manifest_name(&duplicate).unwrap(), None);
        fs::remove_dir_all(tmp).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn skill_collision_discovery_fails_closed_without_following_symlinks() {
        use std::os::unix::fs::symlink;

        let tmp = skill_test_root("skill-collision-symlink");
        let workspace = tmp.join("workspace");
        let outside = tmp.join("outside");
        let profile = tmp.join("profile");
        write_skill_manifest(&outside.join("SKILL.md"), "literature-review");
        fs::create_dir_all(workspace.join(".opencode/skills")).unwrap();
        symlink(&outside, workspace.join(".opencode/skills/linked")).unwrap();

        let error =
            discover_user_skill_names(&workspace, &profile, &Default::default()).unwrap_err();
        assert_eq!(error.kind(), std::io::ErrorKind::InvalidData);
        assert_eq!(
            fs::read_to_string(outside.join("SKILL.md")).unwrap(),
            "---\nname: literature-review\ndescription: Test skill.\n---\n\n# literature-review\n"
        );
        fs::remove_dir_all(tmp).unwrap();
    }

    #[test]
    fn managed_skill_sync_preserves_unmarked_collision_and_is_idempotent() {
        let tmp = skill_test_root("managed-skill-sync");
        let src = tmp.join("src");
        let dst = tmp.join("dst");
        write(&src.join("literature-review/SKILL.md"), "bundled");
        write(&src.join("statistical-analysis/SKILL.md"), "v1");
        write(&dst.join("literature-review/SKILL.md"), "user override");
        write(&dst.join("custom-lab/SKILL.md"), "user custom");

        let mut ownership = Default::default();
        let (available, first_managed) =
            sync_managed_skill_pack(&src, &dst, &mut ownership, true, &Default::default()).unwrap();
        assert_eq!(available.len(), 2);
        assert_eq!(
            first_managed.keys().cloned().collect::<Vec<_>>(),
            ["statistical-analysis"]
        );
        assert_eq!(
            fs::read_to_string(dst.join("literature-review/SKILL.md")).unwrap(),
            "user override"
        );
        assert_eq!(
            fs::read_to_string(dst.join("custom-lab/SKILL.md")).unwrap(),
            "user custom"
        );

        write(&src.join("statistical-analysis/SKILL.md"), "v2");
        let mut ownership = first_managed.clone();
        let (_, second_managed) =
            sync_managed_skill_pack(&src, &dst, &mut ownership, false, &Default::default())
                .unwrap();
        let mut ownership = second_managed.clone();
        let (_, third_managed) =
            sync_managed_skill_pack(&src, &dst, &mut ownership, false, &Default::default())
                .unwrap();
        assert_eq!(second_managed, third_managed);
        assert_eq!(
            fs::read_to_string(dst.join("statistical-analysis/SKILL.md")).unwrap(),
            "v2"
        );
        assert_eq!(
            fs::read_dir(&dst)
                .unwrap()
                .filter_map(Result::ok)
                .filter(|entry| entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".statistical-analysis."))
                .count(),
            0
        );

        write(
            &dst.join("statistical-analysis/SKILL.md"),
            "user modified managed skill",
        );
        write(&src.join("statistical-analysis/SKILL.md"), "v3");
        let mut ownership = third_managed;
        let (_, after_edit) =
            sync_managed_skill_pack(&src, &dst, &mut ownership, false, &Default::default())
                .unwrap();
        assert!(!after_edit.contains_key("statistical-analysis"));
        assert!(!ownership.contains_key("statistical-analysis"));
        assert_eq!(
            fs::read_to_string(dst.join("statistical-analysis/SKILL.md")).unwrap(),
            "user modified managed skill"
        );
        fs::remove_dir_all(tmp).unwrap();
    }

    #[test]
    fn managed_agent_sync_preserves_collision_and_relinquishes_modified_content() {
        let tmp = skill_test_root("managed-agent-sync");
        let src = tmp.join("src");
        let dst = tmp.join("dst");
        write(&src.join("research.md"), "bundled research");
        write(&src.join("task.md"), "task v1");
        write(&dst.join("research.md"), "user research");
        write(&dst.join("custom.md"), "user custom");

        let mut ownership = Default::default();
        let (_, first_managed) = sync_managed_agent_pack(&src, &dst, &mut ownership, true).unwrap();
        assert_eq!(
            first_managed.keys().cloned().collect::<Vec<_>>(),
            ["task.md"]
        );
        assert_eq!(
            fs::read_to_string(dst.join("research.md")).unwrap(),
            "user research"
        );

        write(&src.join("task.md"), "task v2");
        let mut ownership = first_managed;
        let (_, second_managed) =
            sync_managed_agent_pack(&src, &dst, &mut ownership, false).unwrap();
        assert_eq!(fs::read_to_string(dst.join("task.md")).unwrap(), "task v2");

        write(&dst.join("task.md"), "user task override");
        write(&src.join("task.md"), "task v3");
        let mut ownership = second_managed;
        let (_, current) = sync_managed_agent_pack(&src, &dst, &mut ownership, false).unwrap();
        assert!(!current.contains_key("task.md"));
        assert!(!ownership.contains_key("task.md"));
        assert_eq!(
            fs::read_to_string(dst.join("task.md")).unwrap(),
            "user task override"
        );
        assert_eq!(
            fs::read_to_string(dst.join("research.md")).unwrap(),
            "user research"
        );
        assert_eq!(
            fs::read_to_string(dst.join("custom.md")).unwrap(),
            "user custom"
        );
        fs::remove_dir_all(tmp).unwrap();
    }

    #[test]
    fn stale_agent_prune_requires_the_recorded_fingerprint() {
        let tmp = skill_test_root("managed-agent-prune");
        let dst = tmp.join("agents");
        write(&dst.join("exact.md"), "managed");
        write(&dst.join("edited.md"), "before edit");
        let previous = std::collections::BTreeMap::from([
            (
                "exact.md".to_string(),
                fingerprint_agent_file(&dst.join("exact.md")).unwrap(),
            ),
            (
                "edited.md".to_string(),
                fingerprint_agent_file(&dst.join("edited.md")).unwrap(),
            ),
        ]);
        write(&dst.join("edited.md"), "user edit");

        prune_stale_agents(&dst, &previous, &Default::default()).unwrap();

        assert!(!dst.join("exact.md").exists());
        assert_eq!(
            fs::read_to_string(dst.join("edited.md")).unwrap(),
            "user edit"
        );
        fs::remove_dir_all(tmp).unwrap();
    }

    #[test]
    fn exact_targets_are_adopted_after_registry_write_crashes() {
        let tmp = skill_test_root("managed-profile-crash-adoption");
        let skill_src = tmp.join("skill-src");
        let skill_dst = tmp.join("skills");
        write(&skill_src.join("analysis/SKILL.md"), "exact skill");
        write(
            &skill_src.join("analysis/references/method.md"),
            "same bytes",
        );
        write(&skill_dst.join("analysis/SKILL.md"), "exact skill");
        write(
            &skill_dst.join("analysis/references/method.md"),
            "same bytes",
        );
        let mut skill_ownership = Default::default();
        let (_, skills) = sync_managed_skill_pack(
            &skill_src,
            &skill_dst,
            &mut skill_ownership,
            true,
            &Default::default(),
        )
        .unwrap();
        assert_eq!(
            skills.get("analysis"),
            Some(&fingerprint_skill_dir(&skill_dst.join("analysis")).unwrap())
        );

        let agent_src = tmp.join("agent-src");
        let agent_dst = tmp.join("agents");
        write(&agent_src.join("research.md"), "exact agent");
        write(&agent_dst.join("research.md"), "exact agent");
        let mut agent_ownership = Default::default();
        let (_, agents) =
            sync_managed_agent_pack(&agent_src, &agent_dst, &mut agent_ownership, true).unwrap();
        assert_eq!(
            agents.get("research.md"),
            Some(&fingerprint_agent_file(&agent_dst.join("research.md")).unwrap())
        );
        fs::remove_dir_all(tmp).unwrap();
    }

    #[test]
    fn fingerprint_registry_does_not_adopt_unregistered_exact_collisions() {
        let tmp = skill_test_root("managed-profile-v2-collision");
        let skill_src = tmp.join("skill-src");
        let skill_dst = tmp.join("skills");
        write(&skill_src.join("analysis/SKILL.md"), "same skill");
        write(&skill_dst.join("analysis/SKILL.md"), "same skill");
        let mut skill_ownership = Default::default();
        let (_, skills) = sync_managed_skill_pack(
            &skill_src,
            &skill_dst,
            &mut skill_ownership,
            false,
            &Default::default(),
        )
        .unwrap();
        assert!(skills.is_empty());
        assert!(skill_ownership.is_empty());

        let agent_src = tmp.join("agent-src");
        let agent_dst = tmp.join("agents");
        write(&agent_src.join("research.md"), "same agent");
        write(&agent_dst.join("research.md"), "same agent");
        let mut agent_ownership = Default::default();
        let (_, agents) =
            sync_managed_agent_pack(&agent_src, &agent_dst, &mut agent_ownership, false).unwrap();
        assert!(agents.is_empty());
        assert!(agent_ownership.is_empty());
        fs::remove_dir_all(tmp).unwrap();
    }

    #[test]
    fn legacy_registry_drops_name_only_ownership_but_allows_exact_adoption() {
        let tmp = skill_test_root("managed-profile-legacy-adoption");
        write(
            &tmp.join(".spark-agent-managed.json"),
            r#"{"version":1,"agents":["research.md"],"skills":["analysis"]}"#,
        );
        let mut registry = load_managed_profile_registry(&tmp).unwrap();
        assert!(registry.agents.is_empty());
        assert!(registry.skills.is_empty());
        assert!(registry.allow_exact_adoption);
        let allow_exact_adoption = registry.allow_exact_adoption;

        let skill_src = tmp.join("skill-src");
        let skill_dst = tmp.join("skills");
        write(&skill_src.join("analysis/SKILL.md"), "legacy exact");
        write(&skill_dst.join("analysis/SKILL.md"), "legacy exact");
        let (_, skills) = sync_managed_skill_pack(
            &skill_src,
            &skill_dst,
            &mut registry.skills,
            allow_exact_adoption,
            &Default::default(),
        )
        .unwrap();
        assert!(skills.contains_key("analysis"));

        let agent_src = tmp.join("agent-src");
        let agent_dst = tmp.join("agents");
        write(&agent_src.join("research.md"), "legacy exact");
        write(&agent_dst.join("research.md"), "legacy exact");
        let (_, agents) = sync_managed_agent_pack(
            &agent_src,
            &agent_dst,
            &mut registry.agents,
            allow_exact_adoption,
        )
        .unwrap();
        assert!(agents.contains_key("research.md"));
        fs::remove_dir_all(tmp).unwrap();
    }

    #[test]
    fn managed_registry_round_trips_and_rejects_unsafe_names() {
        let tmp = skill_test_root("managed-profile-registry");
        let mut registry = ManagedProfileRegistry::default();
        registry.agents.insert(
            "research.md".to_string(),
            format!("sha256:{}", "a".repeat(64)),
        );
        registry.skills.insert(
            "literature-review".to_string(),
            format!("sha256:{}", "b".repeat(64)),
        );
        write_managed_profile_registry(&tmp, &registry).unwrap();
        let loaded = load_managed_profile_registry(&tmp).unwrap();
        assert_eq!(loaded.agents, registry.agents);
        assert_eq!(loaded.skills, registry.skills);
        assert!(!loaded.allow_exact_adoption);

        write(
            &tmp.join(".spark-agent-managed.json"),
            r#"{"version":1,"agents":["../escape.md"],"skills":[]}"#,
        );
        assert!(load_managed_profile_registry(&tmp).is_err());
        write(
            &tmp.join(".spark-agent-managed.json"),
            r#"{"version":2,"agents":{"research.md":"not-a-fingerprint"},"skills":{}}"#,
        );
        assert!(load_managed_profile_registry(&tmp).is_err());
        fs::remove_dir_all(tmp).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn managed_sync_never_follows_existing_symlinks() {
        use std::os::unix::fs::symlink;

        let tmp = skill_test_root("managed-profile-symlink");
        let outside_skill = tmp.join("outside-skill");
        let skill_src = tmp.join("skill-src");
        let skill_dst = tmp.join("skills");
        write(&outside_skill.join("SKILL.md"), "outside skill");
        write(&skill_src.join("analysis/SKILL.md"), "bundled skill");
        fs::create_dir_all(&skill_dst).unwrap();
        symlink(&outside_skill, skill_dst.join("analysis")).unwrap();
        let mut skill_ownership = std::collections::BTreeMap::from([(
            "analysis".to_string(),
            format!("sha256:{}", "a".repeat(64)),
        )]);
        let (_, skills) = sync_managed_skill_pack(
            &skill_src,
            &skill_dst,
            &mut skill_ownership,
            false,
            &Default::default(),
        )
        .unwrap();
        assert!(skills.is_empty());
        assert!(skill_ownership.is_empty());
        assert_eq!(
            fs::read_to_string(outside_skill.join("SKILL.md")).unwrap(),
            "outside skill"
        );

        let outside_agent = tmp.join("outside-agent.md");
        let agent_src = tmp.join("agent-src");
        let agent_dst = tmp.join("agents");
        write(&outside_agent, "outside agent");
        write(&agent_src.join("research.md"), "bundled agent");
        fs::create_dir_all(&agent_dst).unwrap();
        symlink(&outside_agent, agent_dst.join("research.md")).unwrap();
        let mut agent_ownership = std::collections::BTreeMap::from([(
            "research.md".to_string(),
            format!("sha256:{}", "b".repeat(64)),
        )]);
        let (_, agents) =
            sync_managed_agent_pack(&agent_src, &agent_dst, &mut agent_ownership, false).unwrap();
        assert!(agents.is_empty());
        assert!(agent_ownership.is_empty());
        assert_eq!(fs::read_to_string(&outside_agent).unwrap(), "outside agent");
        fs::remove_dir_all(tmp).unwrap();
    }

    #[test]
    fn empty_skill_pack_skips_debug_prune_and_fails_release() {
        let mut bundled = std::collections::BTreeSet::new();
        assert!(!register_skill_pack_names("skills", Vec::new(), true, &mut bundled).unwrap());
        assert!(bundled.is_empty());

        let error =
            register_skill_pack_names("skills-core", Vec::new(), false, &mut bundled).unwrap_err();
        assert!(error.contains("no deployable skills (skills-core)"));

        assert!(register_skill_pack_names(
            "skills-core",
            vec!["research".to_string()],
            false,
            &mut bundled,
        )
        .unwrap());
        assert!(bundled.contains("research"));
    }

    #[cfg(unix)]
    #[test]
    fn tighten_private_makes_dir_and_secrets_owner_only() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("os-private-{}", std::process::id()));
        let sub = dir.join("opencode");
        fs::create_dir_all(&sub).unwrap();
        let cfg = sub.join("opencode.jsonc");
        fs::write(&cfg, b"{\"apiKey\":\"secret\"}").unwrap();
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o755)).unwrap();
        fs::set_permissions(&cfg, fs::Permissions::from_mode(0o644)).unwrap();

        // The runtime root holds OAuth/connector state and legacy credentials
        // pending migration. It must be unreadable to other users even when
        // the sidecar later rewrites files inside with a default umask.
        super::tighten_private(&dir);
        assert_eq!(
            fs::metadata(&dir).unwrap().permissions().mode() & 0o777,
            0o700
        );
        super::tighten_private(&cfg);
        assert_eq!(
            fs::metadata(&cfg).unwrap().permissions().mode() & 0o777,
            0o600
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn random_hex_is_csprng_shaped() {
        // 16 bytes → 32 hex chars, fresh per call — the shape the sidecar
        // password and the preview/Jupyter tokens rely on.
        let a = random_hex(16);
        let b = random_hex(16);
        assert_eq!(a.len(), 32);
        assert!(a.bytes().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b, "two draws must differ");
    }

    #[test]
    fn removes_only_the_named_config_entry() {
        let cfg = r#"{"model":"a/b","provider":{"ollama":{"npm":"x"},"keep":{"npm":"y"}},"mcp":{"pw":{"type":"local"}}}"#;
        let out = remove_key_from_config(cfg, "provider", "ollama").unwrap();
        assert!(!out.contains("ollama"));
        assert!(out.contains("keep"));
        assert!(out.contains("\"model\": \"a/b\""));
        let out2 = remove_key_from_config(cfg, "mcp", "pw").unwrap();
        assert!(!out2.contains("\"pw\""));
        // Absent key and non-JSON input are errors, not silent no-ops.
        assert!(remove_key_from_config(cfg, "provider", "missing").is_err());
        let jsonc =
            "// user config\n{provider:{ollama:{options:{baseURL:'http://localhost',},},},}";
        let out = remove_key_from_config(jsonc, "provider", "ollama").unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&out).unwrap();
        assert_eq!(parsed["provider"], serde_json::json!({}));
    }

    fn write(path: &std::path::Path, content: &str) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, content).unwrap();
    }

    fn skill_test_root(label: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("{label}-{}-{}", std::process::id(), random_hex(4)))
    }

    #[test]
    fn skill_recovery_restores_one_complete_backup_and_discards_staging() {
        let tmp = skill_test_root("skill-recovery-restore");
        let dst = tmp.join("skills");
        fs::create_dir_all(&dst).unwrap();
        let target = dst.join("paper-writer");
        let backup = dst.join(".paper-writer.1111111111111111.backup");
        let staging = dst.join(".paper-writer.2222222222222222.staging");
        write(&backup.join("SKILL.md"), "last-good");
        write(&backup.join("references/guide.md"), "complete");
        write(&staging.join("SKILL.md"), "partial-new");

        recover_skill_replacement(&target).unwrap();

        assert_eq!(
            fs::read_to_string(target.join("SKILL.md")).unwrap(),
            "last-good"
        );
        assert_eq!(
            fs::read_to_string(target.join("references/guide.md")).unwrap(),
            "complete"
        );
        assert!(!backup.exists());
        assert!(!staging.exists());
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn skill_recovery_keeps_published_target_and_cleans_all_exact_artifacts() {
        let tmp = skill_test_root("skill-recovery-published");
        let dst = tmp.join("skills");
        let target = dst.join("paper-writer");
        write(&target.join("SKILL.md"), "published");
        let exact_staging = dst.join(".paper-writer.1111111111111111.staging");
        let exact_backup_a = dst.join(".paper-writer.2222222222222222.backup");
        let exact_backup_b = dst.join(".paper-writer.3333333333333333.backup");
        write(&exact_staging.join("SKILL.md"), "candidate");
        write(&exact_backup_a.join("SKILL.md"), "old-a");
        write(&exact_backup_b.join("SKILL.md"), "old-b");

        recover_skill_replacement(&target).unwrap();

        assert_eq!(
            fs::read_to_string(target.join("SKILL.md")).unwrap(),
            "published"
        );
        assert!(!exact_staging.exists());
        assert!(!exact_backup_a.exists());
        assert!(!exact_backup_b.exists());
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn skill_recovery_restores_only_complete_backup_over_incomplete_target() {
        let tmp = skill_test_root("skill-recovery-corrupt-target");
        let dst = tmp.join("skills");
        let target = dst.join("paper-writer");
        let backup = dst.join(".paper-writer.1111111111111111.backup");
        let incomplete_backup = dst.join(".paper-writer.2222222222222222.backup");
        write(&target.join("partial.txt"), "not-a-complete-skill");
        write(&backup.join("SKILL.md"), "last-good");
        write(
            &incomplete_backup.join("references/partial.md"),
            "incomplete-backup",
        );

        recover_skill_replacement(&target).unwrap();
        recover_skill_replacement(&target).unwrap();

        assert_eq!(
            fs::read_to_string(target.join("SKILL.md")).unwrap(),
            "last-good"
        );
        assert!(!target.join("partial.txt").exists());
        assert!(!backup.exists());
        assert!(!incomplete_backup.exists());
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn skill_recovery_is_fail_closed_when_missing_target_has_multiple_backups() {
        let tmp = skill_test_root("skill-recovery-ambiguous");
        let dst = tmp.join("skills");
        fs::create_dir_all(&dst).unwrap();
        let target = dst.join("paper-writer");
        let backup_a = dst.join(".paper-writer.1111111111111111.backup");
        let backup_b = dst.join(".paper-writer.2222222222222222.backup");
        let staging = dst.join(".paper-writer.3333333333333333.staging");
        write(&backup_a.join("SKILL.md"), "old-a");
        write(&backup_b.join("SKILL.md"), "old-b");
        write(&staging.join("SKILL.md"), "candidate");

        let error = recover_skill_replacement(&target).unwrap_err();

        assert_eq!(error.kind(), std::io::ErrorKind::InvalidData);
        assert!(error.to_string().contains("ambiguous skill recovery"));
        assert!(!target.exists());
        assert!(backup_a.exists(), "ambiguous backups must be retained");
        assert!(backup_b.exists(), "ambiguous backups must be retained");
        assert!(!staging.exists(), "staging is never authoritative");
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn sync_fails_closed_when_invalid_target_has_multiple_complete_backups() {
        let tmp = skill_test_root("skill-recovery-ambiguous-target");
        let src = tmp.join("src");
        let dst = tmp.join("skills");
        let target = dst.join("paper-writer");
        let backup_a = dst.join(".paper-writer.1111111111111111.backup");
        let backup_b = dst.join(".paper-writer.2222222222222222.backup");
        write(&src.join("paper-writer/SKILL.md"), "trusted-new");
        write(&target.join("partial.txt"), "unverified-target");
        write(&backup_a.join("SKILL.md"), "old-a");
        write(&backup_b.join("SKILL.md"), "old-b");

        let error = sync_skill_pack(&src, &dst).unwrap_err();

        assert_eq!(error.kind(), std::io::ErrorKind::InvalidData);
        assert!(error.to_string().contains("ambiguous skill recovery"));
        assert!(target.join("partial.txt").is_file());
        assert!(!target.join("SKILL.md").exists());
        assert!(backup_a.exists());
        assert!(backup_b.exists());
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn skill_recovery_retains_incomplete_backup_for_bundled_repair() {
        let tmp = skill_test_root("skill-recovery-incomplete");
        let dst = tmp.join("skills");
        fs::create_dir_all(&dst).unwrap();
        let target = dst.join("paper-writer");
        let backup = dst.join(".paper-writer.1111111111111111.backup");
        write(&backup.join("references/guide.md"), "not-a-complete-skill");

        recover_skill_replacement(&target).unwrap();

        assert!(!target.exists());
        assert!(
            backup.exists(),
            "invalid backup remains until a replacement is validated"
        );
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn sync_repairs_incomplete_target_when_no_backup_exists() {
        let tmp = skill_test_root("skill-recovery-repair-target");
        let src = tmp.join("src");
        let dst = tmp.join("dst");
        write(&src.join("paper-writer/SKILL.md"), "trusted-new");
        write(
            &dst.join("paper-writer/partial.txt"),
            "incomplete-published-tree",
        );

        sync_skill_pack(&src, &dst).unwrap();

        assert_eq!(
            fs::read_to_string(dst.join("paper-writer/SKILL.md")).unwrap(),
            "trusted-new"
        );
        assert!(!dst.join("paper-writer/partial.txt").exists());
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn sync_repairs_and_cleans_one_incomplete_backup() {
        let tmp = skill_test_root("skill-recovery-repair-backup");
        let src = tmp.join("src");
        let dst = tmp.join("dst");
        let backup = dst.join(".paper-writer.1111111111111111.backup");
        write(&src.join("paper-writer/SKILL.md"), "trusted-new");
        write(&backup.join("partial.txt"), "incomplete-backup");

        sync_skill_pack(&src, &dst).unwrap();

        assert_eq!(
            fs::read_to_string(dst.join("paper-writer/SKILL.md")).unwrap(),
            "trusted-new"
        );
        assert!(!backup.exists());
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn sync_repairs_invalid_target_with_only_invalid_backup_twice() {
        let tmp = skill_test_root("skill-recovery-invalid-target-and-backup");
        let src = tmp.join("src");
        let dst = tmp.join("dst");
        let target = dst.join("paper-writer");
        let backup = dst.join(".paper-writer.1111111111111111.backup");
        write(&src.join("paper-writer/SKILL.md"), "trusted-new");
        write(&target.join("partial.txt"), "invalid-target");
        write(&backup.join("references/partial.md"), "invalid-backup");

        sync_skill_pack(&src, &dst).unwrap();
        sync_skill_pack(&src, &dst).unwrap();

        assert_eq!(
            fs::read_to_string(target.join("SKILL.md")).unwrap(),
            "trusted-new"
        );
        assert!(!target.join("partial.txt").exists());
        assert!(!backup.exists());
        assert_eq!(
            fs::read_dir(&dst)
                .unwrap()
                .filter_map(Result::ok)
                .filter(|entry| entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".paper-writer."))
                .count(),
            0,
            "successful repeated deploys must remove invalid recovery state"
        );
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn skill_recovery_ignores_malformed_and_other_skill_names() {
        let tmp = skill_test_root("skill-recovery-names");
        let dst = tmp.join("skills");
        let target = dst.join("paper");
        write(&target.join("SKILL.md"), "published");
        let untouched = [
            ".paper.111111111111111.staging",
            ".paper.AAAAAAAAAAAAAAAA.backup",
            ".paper.1111111111111111.backup.extra",
            ".paper-writer.1111111111111111.backup",
            ".other.1111111111111111.staging",
        ];
        for name in untouched {
            write(&dst.join(name).join("marker"), name);
        }

        recover_skill_replacement(&target).unwrap();

        for name in untouched {
            assert!(
                dst.join(name).exists(),
                "must not touch malformed artifact {name}"
            );
        }
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn sync_recovers_interrupted_replacement_before_deploying_new_skill() {
        let tmp = skill_test_root("skill-recovery-integrated");
        let src = tmp.join("src");
        let dst = tmp.join("dst");
        write(&src.join("paper-writer/SKILL.md"), "new");
        write(
            &dst.join(".paper-writer.1111111111111111.backup/SKILL.md"),
            "last-good",
        );
        write(
            &dst.join(".paper-writer.2222222222222222.staging/SKILL.md"),
            "interrupted",
        );

        sync_skill_pack(&src, &dst).unwrap();

        assert_eq!(
            fs::read_to_string(dst.join("paper-writer/SKILL.md")).unwrap(),
            "new"
        );
        assert_eq!(
            fs::read_dir(&dst)
                .unwrap()
                .filter_map(Result::ok)
                .filter(|entry| entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".paper-writer."))
                .count(),
            0
        );
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn skill_recovery_unlinks_backup_symlink_without_touching_its_target() {
        use std::os::unix::fs::symlink;

        let tmp = skill_test_root("skill-recovery-symlink-cleanup");
        let dst = tmp.join("skills");
        let target = dst.join("paper-writer");
        let outside = tmp.join("outside");
        write(&target.join("SKILL.md"), "published");
        write(&outside.join("keep.txt"), "keep");
        let backup = dst.join(".paper-writer.1111111111111111.backup");
        symlink(&outside, &backup).unwrap();

        recover_skill_replacement(&target).unwrap();

        assert!(!backup.exists());
        assert_eq!(
            fs::read_to_string(outside.join("keep.txt")).unwrap(),
            "keep"
        );
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn sync_repairs_symlink_backup_without_touching_its_target() {
        use std::os::unix::fs::symlink;

        let tmp = skill_test_root("skill-recovery-symlink-restore");
        let src = tmp.join("src");
        let dst = tmp.join("skills");
        fs::create_dir_all(&dst).unwrap();
        let target = dst.join("paper-writer");
        let outside = tmp.join("outside");
        write(&src.join("paper-writer/SKILL.md"), "trusted-new");
        write(&outside.join("SKILL.md"), "outside");
        let backup = dst.join(".paper-writer.1111111111111111.backup");
        symlink(&outside, &backup).unwrap();

        sync_skill_pack(&src, &dst).unwrap();

        assert_eq!(
            fs::read_to_string(target.join("SKILL.md")).unwrap(),
            "trusted-new"
        );
        assert!(!backup.exists());
        assert_eq!(
            fs::read_to_string(outside.join("SKILL.md")).unwrap(),
            "outside"
        );
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn sync_recovers_nested_symlink_target_and_two_deployments_stay_clean() {
        use std::os::unix::fs::symlink;

        let tmp = skill_test_root("skill-recovery-target-symlink");
        let src = tmp.join("src");
        let dst = tmp.join("skills");
        let target = dst.join("paper-writer");
        let backup = dst.join(".paper-writer.1111111111111111.backup");
        let outside = tmp.join("outside.txt");
        write(&src.join("paper-writer/SKILL.md"), "trusted-new");
        write(
            &src.join("paper-writer/references/guide.md"),
            "trusted-reference",
        );
        write(&target.join("SKILL.md"), "unverified-target");
        write(&outside, "outside");
        fs::create_dir_all(target.join("references")).unwrap();
        symlink(&outside, target.join("references/guide.md")).unwrap();
        write(&backup.join("SKILL.md"), "last-good");

        sync_skill_pack(&src, &dst).unwrap();
        sync_skill_pack(&src, &dst).unwrap();

        assert_eq!(
            fs::read_to_string(target.join("SKILL.md")).unwrap(),
            "trusted-new"
        );
        assert_eq!(
            fs::read_to_string(target.join("references/guide.md")).unwrap(),
            "trusted-reference"
        );
        assert_eq!(fs::read_to_string(&outside).unwrap(), "outside");
        assert_eq!(
            fs::read_dir(&dst)
                .unwrap()
                .filter_map(Result::ok)
                .filter(|entry| entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".paper-writer."))
                .count(),
            0,
            "successful repeated deploys must leave no recovery artifacts"
        );
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn sync_rejects_symlink_source_and_preserves_previous_skill() {
        use std::os::unix::fs::symlink;

        let tmp = skill_test_root("skillsync-source-symlink");
        let src = tmp.join("src");
        let dst = tmp.join("dst");
        let outside = tmp.join("outside.txt");
        write(&src.join("paper-writer/SKILL.md"), "new");
        write(&outside, "outside");
        symlink(&outside, src.join("paper-writer/reference-link")).unwrap();
        write(&dst.join("paper-writer/SKILL.md"), "old");

        let error = sync_skill_pack(&src, &dst).unwrap_err();

        assert_eq!(error.kind(), std::io::ErrorKind::InvalidData);
        assert_eq!(
            fs::read_to_string(dst.join("paper-writer/SKILL.md")).unwrap(),
            "old"
        );
        assert_eq!(fs::read_to_string(&outside).unwrap(), "outside");
        assert_eq!(
            fs::read_dir(&dst)
                .unwrap()
                .filter_map(Result::ok)
                .filter(|entry| entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".paper-writer."))
                .count(),
            0,
            "failed copy must not leave recovery artifacts"
        );
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn sync_rejects_fifo_source_and_preserves_previous_skill() {
        let tmp = skill_test_root("skillsync-source-fifo");
        let src = tmp.join("src");
        let dst = tmp.join("dst");
        write(&src.join("paper-writer/SKILL.md"), "new");
        let fifo = src.join("paper-writer/events.fifo");
        let status = std::process::Command::new("mkfifo")
            .arg(&fifo)
            .status()
            .unwrap();
        assert!(status.success());
        write(&dst.join("paper-writer/SKILL.md"), "old");

        let error = sync_skill_pack(&src, &dst).unwrap_err();

        assert_eq!(error.kind(), std::io::ErrorKind::InvalidData);
        assert_eq!(
            fs::read_to_string(dst.join("paper-writer/SKILL.md")).unwrap(),
            "old"
        );
        assert_eq!(
            fs::read_dir(&dst)
                .unwrap()
                .filter_map(Result::ok)
                .filter(|entry| entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".paper-writer."))
                .count(),
            0,
            "failed copy must not leave recovery artifacts"
        );
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn sync_replaces_bundled_and_keeps_user_skills() {
        let tmp = std::env::temp_dir().join(format!("skillsync-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        let src = tmp.join("src");
        let dst = tmp.join("dst");

        // Bundled pack: one skill with a nested reference file, plus a top-level
        // plain file (.commit) that must NOT be copied.
        write(&src.join("paper-writer/SKILL.md"), "v2");
        write(&src.join("paper-writer/references/guide.md"), "ref");
        write(&src.join(".commit"), "abc123");
        // A placeholder dir without SKILL.md must not be deployed.
        fs::create_dir_all(src.join("placeholder")).unwrap();

        // Existing workspace: a stale copy of the bundled skill (with a file the
        // new version no longer has) and a user-installed skill.
        write(&dst.join("paper-writer/SKILL.md"), "v1");
        write(&dst.join("paper-writer/obsolete.md"), "old");
        write(&dst.join("my-skill/SKILL.md"), "user");

        sync_skill_pack(&src, &dst).unwrap();

        assert_eq!(
            fs::read_to_string(dst.join("paper-writer/SKILL.md")).unwrap(),
            "v2"
        );
        assert_eq!(
            fs::read_to_string(dst.join("paper-writer/references/guide.md")).unwrap(),
            "ref"
        );
        assert!(
            !dst.join("paper-writer/obsolete.md").exists(),
            "stale file must be gone"
        );
        assert_eq!(
            fs::read_to_string(dst.join("my-skill/SKILL.md")).unwrap(),
            "user"
        );
        assert!(
            !dst.join(".commit").exists(),
            "top-level files are not skills"
        );
        assert!(
            !dst.join("placeholder").exists(),
            "dirs without SKILL.md are not skills"
        );

        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn sync_creates_destination_when_missing() {
        let tmp = std::env::temp_dir().join(format!("skillsync-new-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        let src = tmp.join("src");
        write(&src.join("literature-survey/SKILL.md"), "s");

        let dst = tmp.join("deep/nested/skills");
        sync_skill_pack(&src, &dst).unwrap();
        assert_eq!(
            fs::read_to_string(dst.join("literature-survey/SKILL.md")).unwrap(),
            "s"
        );
        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn sync_skips_python_cache_artifacts_and_copies_normal_files() {
        let tmp = std::env::temp_dir().join(format!("skillsync-pycache-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        let src = tmp.join("src");
        let dst = tmp.join("dst");

        write(&src.join("domain-check/SKILL.md"), "skill");
        write(&src.join("domain-check/scripts/check.py"), "print('ok')");
        write(&src.join("domain-check/.skill-config"), "enabled=true");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(
                src.join("domain-check/scripts/check.py"),
                fs::Permissions::from_mode(0o751),
            )
            .unwrap();
        }
        write(
            &src.join("domain-check/__pycache__/domain_check.cpython-314.pyc"),
            "bytecode",
        );
        write(&src.join("domain-check/scripts/check.pyc"), "bytecode");
        write(&src.join("domain-check/scripts/check.pyo"), "bytecode");

        sync_skill_pack(&src, &dst).unwrap();

        let installed = dst.join("domain-check");
        assert_eq!(
            fs::read_to_string(installed.join("scripts/check.py")).unwrap(),
            "print('ok')"
        );
        assert_eq!(
            fs::read_to_string(installed.join(".skill-config")).unwrap(),
            "enabled=true"
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(installed.join("scripts/check.py"))
                    .unwrap()
                    .permissions()
                    .mode()
                    & 0o777,
                0o751
            );
        }
        assert!(!installed.join("__pycache__").exists());
        assert!(!installed.join("scripts/check.pyc").exists());
        assert!(!installed.join("scripts/check.pyo").exists());

        fs::remove_dir_all(&tmp).unwrap();
    }
}

/// Scrub the retired Spark-owned plaintext Jupyter MCP registration before
/// desktop bootstrap starts OpenCode. This uses the same stop/mutate/restore
/// transaction as every other native config write.
#[tauri::command(async)]
pub fn reconcile_jupyter(
    app: AppHandle,
    state: State<'_, RuntimeState>,
) -> Result<RuntimeRestartResult, String> {
    let (_, runtime_url) =
        with_config_transaction(&app, &state, || reconcile_jupyter_config_files(&app))?;
    Ok(RuntimeRestartResult { runtime_url })
}

/// Remove an entry from a map section of the app-private global OpenCode
/// config ("provider" or "mcp") and restart the sidecar (PATCH /global/config
/// cannot delete keys).
#[tauri::command(async)]
pub fn remove_config_entry(
    app: AppHandle,
    state: State<'_, RuntimeState>,
    section: String,
    key: String,
) -> Result<RuntimeRestartResult, String> {
    if !matches!(section.as_str(), "provider" | "mcp") {
        return Err(format!("section \"{section}\" is not removable"));
    }
    let (_, runtime_url) = with_config_transaction(&app, &state, || {
        let dir = xdg_config_home(&app)?.join("opencode");
        // The server writes opencode.jsonc; older configs may be opencode.json.
        let path = ["opencode.jsonc", "opencode.json"]
            .iter()
            .map(|name| dir.join(name))
            .find(|path| path.exists())
            .ok_or("no global OpenCode config found")?;
        let text = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
        let updated = remove_key_from_config(&text, &section, &key)?;
        write_private_atomic(&path, updated.as_bytes())?;
        Ok(())
    })?;
    Ok(RuntimeRestartResult { runtime_url })
}

/// Drop `key` from the config JSON/JSONC `section` map. The output is normalized
/// to strict JSON, as are the other app-owned config mutations.
fn remove_key_from_config(text: &str, section: &str, key: &str) -> Result<String, String> {
    let mut cfg = crate::opencode_config::parse_config(text, "OpenCode config")?;
    let removed = cfg
        .get_mut(section)
        .and_then(|p| p.as_object_mut())
        .map(|p| p.remove(key).is_some())
        .unwrap_or(false);
    if !removed {
        return Err(format!(
            "\"{key}\" is not in the config's {section} section"
        ));
    }
    serde_json::to_string_pretty(&cfg).map_err(|e| e.to_string())
}

/// Report an exact persisted native preset. Arbitrary custom policies remain
/// distinguishable and are preserved at rest.
#[tauri::command]
pub fn get_approval_mode(app: AppHandle) -> Result<String, String> {
    let path = effective_config_file(&app)?;
    let existing = read_optional_config(&path)?;
    Ok(crate::opencode_config::permission_mode_of(&existing)?
        .unwrap_or(crate::opencode_config::MODE_BALANCED)
        .to_string())
}

/// Switch the approval mode and restart the sidecar so the permission rules
/// take effect. The returned URL may use a fresh port after a bind race.
#[tauri::command(async)]
pub fn set_approval_mode(
    app: AppHandle,
    state: State<'_, RuntimeState>,
    mode: String,
) -> Result<RuntimeRestartResult, String> {
    let (_, runtime_url) = with_config_transaction(&app, &state, || {
        let path = effective_config_file(&app)?;
        let existing = read_optional_config(&path)?;
        let updated = crate::opencode_config::set_permission_mode(&existing, &mode)?;
        write_private_atomic(&path, updated.as_bytes())?;
        Ok(path)
    })?;
    Ok(RuntimeRestartResult { runtime_url })
}

/// The persisted proxy setting plus the proxy the sidecar would use right now.
#[tauri::command]
pub fn get_proxy_setting(
    app: AppHandle,
    state: State<'_, RuntimeState>,
) -> Result<serde_json::Value, String> {
    let _config_guard = state.config.lock().unwrap();
    let (mode, url) = read_proxy_setting(&app);
    let effective = effective_proxy(&mode, &url);
    Ok(serde_json::json!({ "mode": mode, "url": url, "effective": effective }))
}

/// Persist the proxy setting ("system" | "custom" | "none", url for custom)
/// and restart the sidecar so its network env takes effect.
#[tauri::command(async)]
pub fn set_proxy_setting(
    app: AppHandle,
    state: State<'_, RuntimeState>,
    mode: String,
    url: String,
) -> Result<RuntimeRestartResult, String> {
    let line = match mode.as_str() {
        "system" => "system".to_string(),
        "none" => "none".to_string(),
        "custom" => {
            let url = url.trim();
            validate_proxy_url(url)?;
            format!("custom {url}")
        }
        other => return Err(format!("unknown proxy mode: {other}")),
    };
    let (_, runtime_url) = with_config_transaction(&app, &state, || {
        let path = proxy_setting_file(&app)?;
        write_private_atomic(&path, line.as_bytes())?;
        Ok(path)
    })?;
    Ok(RuntimeRestartResult { runtime_url })
}

/// Save a provider API key in the system credential manager, leave only its
/// env placeholder in OpenCode config, and restart the sidecar so the key is
/// injected into its environment; tools it launches can inherit that value.
#[tauri::command(async)]
pub fn save_provider_api_key(
    app: AppHandle,
    state: State<'_, RuntimeState>,
    provider_id: String,
    api_key: String,
) -> Result<RuntimeRestartResult, String> {
    let (_, runtime_url) = with_config_transaction(&app, &state, || {
        crate::credential::save_provider_api_key(
            &crate::credential::SystemCredentialStore,
            &opencode_config_files(&app)?,
            &opencode_auth_file(&app)?,
            &provider_id,
            &api_key,
            &write_private_atomic,
        )
    })?;
    Ok(RuntimeRestartResult { runtime_url })
}

/// Remove an API-key reference before deleting its credential-manager item.
/// `remove_provider_config` is used for custom endpoints; built-in providers
/// retain their unrelated model/base URL overrides.
#[tauri::command(async)]
pub fn remove_provider_api_key(
    app: AppHandle,
    state: State<'_, RuntimeState>,
    provider_id: String,
    remove_provider_config: bool,
) -> Result<RuntimeRestartResult, String> {
    let (_, runtime_url) = with_config_transaction(&app, &state, || {
        crate::credential::remove_provider_api_key(
            &crate::credential::SystemCredentialStore,
            &opencode_config_files(&app)?,
            &opencode_auth_file(&app)?,
            &provider_id,
            remove_provider_config,
            &write_private_atomic,
        )
    })?;
    Ok(RuntimeRestartResult { runtime_url })
}

/// Future allowlisted credential save boundary. It is deliberately fail-closed
/// until native per-call approval and immutable, fully locked targets are
/// enforced; renderer-supplied commands and environments are never accepted.
#[tauri::command(async)]
pub fn save_science_connector_api_key(
    app: AppHandle,
    state: State<'_, RuntimeState>,
    connector_id: String,
    api_key: String,
) -> Result<RuntimeRestartResult, String> {
    crate::science_mcp::ensure_managed_connector_execution_enabled()?;
    let (_, runtime_url) = with_config_transaction(&app, &state, || {
        let command = crate::science_mcp::managed_connector_command(&app, &connector_id)?;
        crate::credential::save_connector_api_key(
            &crate::credential::SystemConnectorCredentialStore,
            &opencode_config_files(&app)?,
            &connector_id,
            &api_key,
            &command,
            &write_private_atomic,
        )
    })?;
    Ok(RuntimeRestartResult { runtime_url })
}

/// Remove every live config reference for an allowlisted curated connector,
/// then delete its system credential and restart the sidecar.
#[tauri::command(async)]
pub fn remove_science_connector(
    app: AppHandle,
    state: State<'_, RuntimeState>,
    connector_id: String,
) -> Result<RuntimeRestartResult, String> {
    let (_, runtime_url) = with_config_transaction(&app, &state, || {
        let command = crate::science_mcp::managed_connector_command(&app, &connector_id)?;
        crate::credential::remove_connector_api_key(
            &crate::credential::SystemConnectorCredentialStore,
            &opencode_config_files(&app)?,
            &connector_id,
            &command,
            &write_private_atomic,
        )
    })?;
    Ok(RuntimeRestartResult { runtime_url })
}

/// Finalize an OpenCode-owned provider login while the sidecar is stopped.
/// Simple API records are moved to the system credential manager; OAuth stays
/// in the private auth file. If API migration fails (including metadata-bearing
/// records that cannot be represented losslessly), the just-created API record
/// is rolled back and the caller receives an error instead of a false
/// "connected" result.
#[tauri::command(async)]
pub fn finalize_provider_login(
    app: AppHandle,
    state: State<'_, RuntimeState>,
    provider_id: String,
) -> Result<RuntimeRestartResult, String> {
    let (_, runtime_url) = with_config_transaction(&app, &state, || {
        let auth_path = opencode_auth_file(&app)?;
        match crate::credential::migrate_and_collect_env(
            &crate::credential::SystemCredentialStore,
            &opencode_config_files(&app)?,
            &auth_path,
            &write_private_atomic,
        ) {
            Ok(_) => Ok(()),
            Err(migration_error) => match crate::credential::rollback_provider_api_auth(
                &auth_path,
                &provider_id,
                &write_private_atomic,
            ) {
                Ok(true) => Err(format!(
                    "{migration_error}; the provider API login was rolled back"
                )),
                Ok(false) => Err(migration_error),
                Err(rollback_error) => Err(format!(
                    "{migration_error}; additionally failed to roll back the provider API login: {rollback_error}"
                )),
            },
        }
    })?;
    Ok(RuntimeRestartResult { runtime_url })
}

/// Legacy onboarding command. Any supplied key is saved securely first; the
/// config merge receives an empty literal and therefore can only retain the
/// credential module's env placeholder.
#[tauri::command(async)]
pub fn configure_opencode(
    app: AppHandle,
    state: State<'_, RuntimeState>,
    provider: String,
    api_key: String,
    model: String,
    base_url: Option<String>,
) -> Result<ConfigureOpenCodeResult, String> {
    let (path, runtime_url) = with_config_transaction(&app, &state, || {
        let provider = crate::credential::normalize_provider_id(&provider)?;
        if !api_key.is_empty() {
            crate::credential::save_provider_api_key(
                &crate::credential::SystemCredentialStore,
                &opencode_config_files(&app)?,
                &opencode_auth_file(&app)?,
                &provider,
                &api_key,
                &write_private_atomic,
            )?;
        }
        let path = effective_config_file(&app)?;
        let existing = read_optional_config(&path)?;
        let merged = merge_config(&existing, &provider, "", &model, base_url.as_deref())?;
        write_private_atomic(&path, merged.as_bytes())?;
        Ok(path)
    })?;
    Ok(ConfigureOpenCodeResult {
        path: path.to_string_lossy().to_string(),
        runtime_url,
    })
}
