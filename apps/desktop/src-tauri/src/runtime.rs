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
const SIDECAR_STOP_TIMEOUT: Duration = Duration::from_secs(3);

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
}

struct ManagedSidecar {
    process: CommandChild,
    pid: u32,
    generation: u64,
    exit: Arc<ExitSignal>,
}

struct SpawnedSidecar {
    process: CommandChild,
    events: tauri::async_runtime::Receiver<CommandEvent>,
    pid: u32,
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

/// The config file to edit in place: the server may have rewritten the config
/// as opencode.jsonc — prefer whichever exists, fall back to opencode.json.
fn effective_config_file(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = xdg_config_home(app)?.join("opencode");
    Ok(["opencode.jsonc", "opencode.json"]
        .iter()
        .map(|n| dir.join(n))
        .find(|p| p.exists())
        .unwrap_or_else(|| dir.join("opencode.json")))
}

/// The user's existing OpenCode auth file (their login / free credits), if any.
/// Read-only: we copy it into our sandbox so the bundled runtime can use the same
/// login, but we never modify the user's file or sessions.
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

/// Copy the user's OpenCode CLI login into the app-private data dir, EXPLICITLY
/// (from the Settings page) — never silently. Returns false when there is no
/// CLI login to import. Restarts the sidecar so it picks the credentials up.
#[tauri::command(async)]
pub fn import_opencode_login(
    app: AppHandle,
    state: State<'_, RuntimeState>,
) -> Result<bool, String> {
    let Some(src) = user_auth_source() else {
        return Ok(false);
    };
    let (imported, _) = with_config_transaction(&app, &state, || {
        let dst = runtime_root(&app)?
            .join("xdg-data")
            .join("opencode")
            .join("auth.json");
        let contents = std::fs::read(&src).map_err(|e| format!("copy failed: {e}"))?;
        write_private_atomic(&dst, &contents)?;
        Ok(true)
    })?;
    Ok(imported)
}

/// Deploy the bundled skill packs (Tauri resources) into the app-private
/// profile's global skills dir (`<xdg-config>/opencode/skills/`), which OpenCode
/// scans regardless of project detection: `skills/` is the external ai4s-skills
/// pack and `skills-core/` contains the product-owned core skills. The
/// workspace's own `.opencode/skills/` stays reserved for skills the user
/// installs. Runs before every sidecar start so app upgrades refresh the packs.
fn deploy_bundled_skills(app: &AppHandle) {
    let dst = match xdg_config_home(app) {
        Ok(cfg) => cfg.join("opencode").join("skills"),
        Err(_) => return,
    };
    let mut bundled: std::collections::HashSet<std::ffi::OsString> =
        std::collections::HashSet::new();
    let mut all_ok = true;
    for resource in ["skills", "skills-core"] {
        let src = match app
            .path()
            .resolve(resource, tauri::path::BaseDirectory::Resource)
        {
            Ok(p) if p.is_dir() => p,
            _ => {
                all_ok = false; // dev run without `fetch-skills.sh` — nothing to deploy
                continue;
            }
        };
        match sync_skill_pack(&src, &dst) {
            Ok(names) => bundled.extend(names),
            Err(e) => {
                all_ok = false;
                eprintln!("failed to deploy bundled skills ({resource}): {e}");
            }
        }
    }
    // The global skills dir is exclusively app-managed (the user's own skills
    // live in the workspace's `.opencode/skills/`), so any skill dir not in the
    // freshly-bundled set is a stale leftover — e.g. one renamed across an app
    // upgrade (`hpc-slurm` → `remote-compute`) — and must be removed so the
    // obsolete duplicate can't shadow or confuse the agent. Prune ONLY when all
    // packs deployed cleanly: a partial deploy would make `bundled`
    // incomplete and wrongly delete valid skills.
    if all_ok {
        prune_stale_skills(&dst, &bundled);
    }
}

/// Remove every SKILL.md-bearing directory in `dst` whose name is not in
/// `bundled` (the set just deployed). Non-skill directories are left untouched.
fn prune_stale_skills(dst: &Path, bundled: &std::collections::HashSet<std::ffi::OsString>) {
    let Ok(entries) = std::fs::read_dir(dst) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() && path.join("SKILL.md").is_file() && !bundled.contains(&entry.file_name())
        {
            let _ = std::fs::remove_dir_all(&path);
        }
    }
}

/// Copy every skill directory under `src` into `dst`, replacing same-named
/// directories (so bundled updates win) and leaving everything else in `dst`
/// alone. Each replacement is staged beside its destination and rolled back on
/// failure, so an interrupted app upgrade never leaves a half-copied skill.
/// Returns the deployed names for stale pruning. Directories without SKILL.md
/// (placeholders) are skipped.
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
        .unwrap_or("skill");
    let suffix = random_hex(8);
    let staging = parent.join(format!(".{name}.{suffix}.staging"));
    let backup = parent.join(format!(".{name}.{suffix}.backup"));
    std::fs::create_dir(&staging)?;
    if let Err(error) = copy_dir(src, &staging) {
        let _ = std::fs::remove_dir_all(&staging);
        return Err(error);
    }

    let had_previous = std::fs::symlink_metadata(target).is_ok();
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
    if had_previous {
        std::fs::remove_dir_all(backup)?;
    }
    Ok(())
}

fn copy_dir(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let to = dst.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_dir(&entry.path(), &to)?;
        } else {
            std::fs::copy(entry.path(), &to)?;
        }
    }
    Ok(())
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
/// (unix). The runtime root carries provider/connector API keys in
/// `opencode.jsonc`/`auth.json`, and the sidecar rewrites those files with a
/// default umask while running — locking the DIRECTORY is what holds, since a
/// 700 dir is unreachable for other users whatever the file modes inside. On
/// Windows, %APPDATA% is per-user ACL'd already; nothing to do.
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
/// `/global/config` (which carries provider API keys). The webview gets it
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
    mut is_ready: impl FnMut() -> bool,
    mut wait_for_next_probe: impl FnMut() -> bool,
) -> Result<(), String> {
    loop {
        if is_shutting_down() {
            return Err("runtime startup cancelled during app shutdown".into());
        }
        if let Some(error) = startup_failure() {
            return Err(error);
        }
        if is_ready() {
            return Ok(());
        }
        if !wait_for_next_probe() {
            return Err("OpenCode did not become ready before the startup timeout".into());
        }
    }
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

fn sidecar_health_ready(port: u16) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, SIDECAR_HTTP_TIMEOUT) else {
        return false;
    };
    if stream.set_read_timeout(Some(SIDECAR_HTTP_TIMEOUT)).is_err()
        || stream
            .set_write_timeout(Some(SIDECAR_HTTP_TIMEOUT))
            .is_err()
    {
        return false;
    }
    let credential = base64_encode(format!("opencode:{}", server_password()).as_bytes());
    let request = format!(
        "GET /global/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Basic {credential}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
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
            Err(_) => return false,
        }
    }
    let Ok(response) = std::str::from_utf8(&response) else {
        return false;
    };
    let Some((headers, body)) = response.split_once("\r\n\r\n") else {
        return false;
    };
    let status_ok = headers
        .lines()
        .next()
        .is_some_and(|line| line.starts_with("HTTP/1.1 200 ") || line.starts_with("HTTP/1.0 200 "));
    let healthy = serde_json::from_str::<serde_json::Value>(body)
        .ok()
        .and_then(|value| value.get("healthy").and_then(serde_json::Value::as_bool));
    status_ok && healthy == Some(true)
}

fn startup_event_failure(
    events: &mut tauri::async_runtime::Receiver<CommandEvent>,
) -> Option<String> {
    loop {
        match events.try_recv() {
            Ok(CommandEvent::Terminated(status)) => {
                return Some(format!(
                    "OpenCode exited during startup (code {:?}, signal {:?})",
                    status.code, status.signal
                ));
            }
            Ok(CommandEvent::Error(error)) => {
                return Some(format!("OpenCode startup event error: {error}"));
            }
            Ok(CommandEvent::Stdout(_) | CommandEvent::Stderr(_)) => {}
            Ok(_) => {}
            Err(tokio::sync::mpsc::error::TryRecvError::Empty) => return None,
            Err(tokio::sync::mpsc::error::TryRecvError::Disconnected) => {
                return Some("OpenCode event channel closed during startup".into());
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

fn terminate_unpublished(sidecar: SpawnedSidecar, context: &str) -> Result<(), String> {
    let SpawnedSidecar {
        process,
        mut events,
        pid,
    } = sidecar;
    let exit = Arc::new(ExitSignal::default());
    let watcher_exit = Arc::clone(&exit);
    std::thread::spawn(move || {
        while let Some(event) = events.blocking_recv() {
            if matches!(event, CommandEvent::Terminated(_)) {
                break;
            }
        }
        watcher_exit.mark_exited();
    });
    let result = terminate_checked(process, pid, context, CommandChild::kill).and_then(|()| {
        if exit.wait(SIDECAR_STOP_TIMEOUT) {
            Ok(())
        } else {
            Err(format!(
                "OpenCode process {} accepted {context} but did not report termination within {}s",
                pid,
                SIDECAR_STOP_TIMEOUT.as_secs()
            ))
        }
    });
    if let Err(error) = &result {
        eprintln!("OpenCode unpublished-process cleanup failed: {error}");
    }
    result
}

fn terminate_managed(sidecar: ManagedSidecar, context: &str) -> Result<(), String> {
    let ManagedSidecar {
        process, pid, exit, ..
    } = sidecar;
    terminate_checked(process, pid, context, CommandChild::kill)?;
    if !exit.wait(SIDECAR_STOP_TIMEOUT) {
        return Err(format!(
            "OpenCode process {pid} accepted {context} but did not report termination within {}s",
            SIDECAR_STOP_TIMEOUT.as_secs()
        ));
    }
    Ok(())
}

fn spawn_sidecar(
    app: &AppHandle,
    port: u16,
    state: &RuntimeState,
) -> Result<SpawnedSidecar, String> {
    // lifecycle is held by every caller before this function acquires config.
    // Keep config locked only through profile preparation and process spawn;
    // readiness may legitimately wait on a macOS TCC prompt for minutes.
    let (mut events, process) = {
        let _config_guard = state.config.lock().unwrap();
        let root = runtime_root(app)?;
        let cfg = root.join("xdg-config");
        let data = root.join("xdg-data");
        let cache = root.join("xdg-cache");
        let runtime_state = root.join("xdg-state");
        // Run OpenCode inside the user-facing workspace, NOT the app's cwd (which is `/`
        // when launched from Finder) — otherwise it scans the whole filesystem root.
        let workspace = workspace_dir(app)?;
        for d in [&cfg, &data, &cache, &runtime_state] {
            std::fs::create_dir_all(d).map_err(|e| e.to_string())?;
        }
        // Ship the bundled scientific skills into the app-private OpenCode profile.
        deploy_bundled_skills(app);
        // Safety default (AGENTS.md non-negotiable): enforce the internal safe
        // policy on every start, repairing legacy full/partial permission configs.
        let cfg_file = effective_config_file(app)?;
        let existing = std::fs::read_to_string(&cfg_file).unwrap_or_default();
        if let Some(seeded) = crate::opencode_config::seed_default_permission(&existing) {
            write_private_atomic(&cfg_file, seeded.as_bytes())?;
        }
        // Secrets live under the runtime root (provider/connector keys in
        // opencode.jsonc, OpenCode's auth.json) — owner-only on every start, so
        // existing installs are repaired and whatever the sidecar later rewrites
        // inside stays unreachable to other users regardless of its umask.
        tighten_private(&root);
        tighten_private(&cfg_file);
        let home = std::env::var("HOME").unwrap_or_default();
        let port_str = port.to_string();

        let cmd = app
            .shell()
            .sidecar("opencode")
            .map_err(|e| format!("sidecar not found: {e}"))?
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
            // App-private dirs: OpenCode never touches the user's ~/.config/opencode.
            .env("XDG_CONFIG_HOME", cfg.to_string_lossy().to_string())
            .env("XDG_DATA_HOME", data.to_string_lossy().to_string())
            .env("XDG_CACHE_HOME", cache.to_string_lossy().to_string())
            .env(
                "XDG_STATE_HOME",
                runtime_state.to_string_lossy().to_string(),
            )
            .env("HOME", home)
            // Lets bundled skill helpers stamp the recording app version into
            // provenance when they run outside the app.
            .env(
                "OPENSCIENCE_APP_VERSION",
                app.package_info().version.to_string(),
            )
            .current_dir(workspace);
        // GUI-launched apps get a minimal PATH; give the agent the user's real tools.
        let mut cmd = cmd.env("PATH", enriched_path());
        // Apply the network-proxy setting so provider logins and API calls work
        // where direct connections are blocked (see resolve_proxy_env).
        let (proxy_mode, proxy_url) = read_proxy_setting(app);
        for (key, value) in resolve_proxy_env(&proxy_mode, &proxy_url) {
            cmd = cmd.env(key, value);
        }

        cmd.spawn()
            .map_err(|e| format!("failed to spawn opencode: {e}"))?
    };

    let pid = process.pid();
    let deadline = Instant::now() + SIDECAR_START_TIMEOUT;
    let mut consecutive_healthy_probes = 0_u8;
    let readiness = wait_until_ready(
        || state.shutting_down.load(Ordering::Acquire),
        || startup_event_failure(&mut events),
        || {
            if sidecar_health_ready(port) {
                consecutive_healthy_probes += 1;
            } else {
                consecutive_healthy_probes = 0;
            }
            // A second authenticated response gives the process event channel
            // time to report an immediate bind/config failure before publish.
            consecutive_healthy_probes >= 2
        },
        || {
            let now = Instant::now();
            if now >= deadline {
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
        return match terminate_unpublished(sidecar, "clean up unready") {
            Ok(()) => Err(startup_error),
            Err(kill_error) => Err(format!("{startup_error}; {kill_error}")),
        };
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
    *state.child.lock().unwrap() = Some(ManagedSidecar {
        process,
        pid,
        generation,
        exit: Arc::clone(&exit),
    });

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
        if cleared {
            eprintln!("OpenCode process {pid} exited unexpectedly ({reason})");
        }
    });
}

/// Kill and respawn the sidecar on its stable port. The caller must hold the
/// lifecycle mutex for this entire transition.
fn restart_sidecar_locked(app: &AppHandle, state: &RuntimeState) -> Result<String, String> {
    if state.shutting_down.load(Ordering::Acquire) {
        return Err("runtime is shutting down".into());
    }
    let port = {
        let mut published_port = state.port.lock().unwrap();
        match *published_port {
            Some(port) => port,
            None => {
                let port = free_port()?;
                *published_port = Some(port);
                port
            }
        }
    };
    *state.url.lock().unwrap() = None;
    if let Some(sidecar) = state.child.lock().unwrap().take() {
        if let Err(error) = terminate_managed(sidecar, "restart") {
            *state.port.lock().unwrap() = None;
            return Err(error);
        }
    }
    if state.shutting_down.load(Ordering::Acquire) {
        *state.port.lock().unwrap() = None;
        return Err("runtime is shutting down".into());
    }
    let sidecar = match spawn_sidecar(app, port, state) {
        Ok(sidecar) => sidecar,
        Err(error) => {
            *state.port.lock().unwrap() = None;
            return Err(error);
        }
    };
    if state.shutting_down.load(Ordering::Acquire) {
        *state.port.lock().unwrap() = None;
        terminate_unpublished(sidecar, "cancel restart during shutdown")?;
        return Err("runtime is shutting down".into());
    }
    let url = format!("http://127.0.0.1:{port}");
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
                if let Some(sidecar) = state.child.lock().unwrap().take() {
                    if let Err(error) = terminate_managed(sidecar, "pause for config update") {
                        *state.port.lock().unwrap() = None;
                        return Err(error);
                    }
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
            // Reuse a stable port across restarts so the frontend URL doesn't change.
            let port = {
                let mut published_port = state.port.lock().unwrap();
                match *published_port {
                    Some(port) => port,
                    None => {
                        let port = free_port()?;
                        *published_port = Some(port);
                        port
                    }
                }
            };
            let sidecar = match spawn_sidecar(&app, port, &state) {
                Ok(sidecar) => sidecar,
                Err(error) => {
                    *state.port.lock().unwrap() = None;
                    return Err(error);
                }
            };
            Ok((format!("http://127.0.0.1:{port}"), sidecar))
        },
        |sidecar| publish_sidecar(&state, sidecar),
        |sidecar| terminate_unpublished(sidecar, "cancel startup during shutdown"),
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
    _state: State<'_, RuntimeState>,
    path: String,
) -> Result<String, String> {
    let dir = PathBuf::from(&path);
    if !dir.is_absolute() {
        return Err("workspace path must be absolute".into());
    }
    std::fs::create_dir_all(&dir).map_err(|e| format!("could not create folder: {e}"))?;
    let canon = dir.canonicalize().map_err(|e| e.to_string())?;
    std::fs::write(
        active_workspace_file(&app)?,
        canon.to_string_lossy().as_bytes(),
    )
    .map_err(|e| e.to_string())?;

    // No sidecar restart: OpenCode serves every folder from one process via
    // per-directory instances, and the frontend reconnects its event stream
    // with `?directory=<new folder>`. Restarting here used to cost 3-6 s per
    // history-session switch (process boot + reconnect polling).
    // Jupyter-lab, however, pins its root_dir at spawn time — re-root it (in
    // the background) so agent-created notebooks land in the new folder.
    crate::jupyter::reroot_jupyter(&app);
    // Refresh this session's local copy of the remote-machine list from the
    // canonical base file, so a machine configured in Settings is visible to
    // every session's agent without reaching outside the workspace.
    crate::compute::materialize_active(&app);
    Ok(canon.to_string_lossy().to_string())
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
        match state.child.lock().unwrap().take() {
            Some(sidecar) => terminate_managed(sidecar, "stop"),
            None => Ok(()),
        }
    })
}

#[cfg(test)]
mod tests {
    use super::{
        base64_encode, config_transaction, current_sidecar_matches, parse_scutil_proxy,
        prune_stale_skills, random_hex, remove_key_from_config, resolve_proxy_env, server_password,
        sidecar_health_ready, start_once, sync_skill_pack, terminate_checked, validate_proxy_url,
        wait_until_ready, with_lifecycle, write_private_atomic,
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
        let probes = Cell::new(0);
        wait_until_ready(
            || false,
            || None,
            || {
                probes.set(probes.get() + 1);
                probes.get() == 3
            },
            || true,
        )
        .unwrap();
        assert_eq!(probes.get(), 3);

        let failure =
            wait_until_ready(|| false, || Some("process exited".into()), || true, || true);
        assert_eq!(failure, Err("process exited".into()));

        let timeout = wait_until_ready(|| false, || None, || false, || false);
        assert!(timeout.unwrap_err().contains("startup timeout"));

        let shutdown = Cell::new(false);
        let cancelled = wait_until_ready(
            || shutdown.get(),
            || None,
            || false,
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

        let (healthy_port, healthy_server) = serve(r#"{"healthy": true}"#);
        assert!(sidecar_health_ready(healthy_port));
        healthy_server.join().unwrap();

        let (unhealthy_port, unhealthy_server) = serve(r#"{"healthy": false}"#);
        assert!(!sidecar_health_ready(unhealthy_port));
        unhealthy_server.join().unwrap();

        let (spoofed_port, spoofed_server) = serve(r#"{"message":"\"healthy\":true"}"#);
        assert!(!sidecar_health_ready(spoofed_port));
        spoofed_server.join().unwrap();
    }

    #[test]
    fn termination_errors_include_action_and_pid() {
        let error = terminate_checked((), 4242, "restart", |_| Err("denied")).unwrap_err();
        assert!(error.contains("restart"));
        assert!(error.contains("4242"));
        assert!(error.contains("denied"));
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
        for name in ["remote-compute", "hpc-slurm"] {
            fs::create_dir_all(dst.join(name)).unwrap();
            fs::write(dst.join(name).join("SKILL.md"), b"---\n").unwrap();
        }
        // A directory without a SKILL.md must never be touched.
        fs::create_dir_all(dst.join("notes")).unwrap();

        let mut bundled = std::collections::HashSet::new();
        bundled.insert(std::ffi::OsString::from("remote-compute"));
        prune_stale_skills(&dst, &bundled);

        assert!(dst.join("remote-compute").is_dir(), "bundled skill kept");
        assert!(
            !dst.join("hpc-slurm").exists(),
            "stale renamed skill removed"
        );
        assert!(dst.join("notes").is_dir(), "non-skill dir left alone");
        let _ = fs::remove_dir_all(&dst);
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

        // The runtime root holds provider/connector keys (opencode.jsonc,
        // auth.json) — it must be unreadable to other users even when the
        // sidecar later rewrites files inside with a default umask.
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
        assert!(remove_key_from_config("// jsonc comment\n{}", "provider", "x").is_err());
    }

    fn write(path: &std::path::Path, content: &str) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, content).unwrap();
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
) -> Result<(), String> {
    if !matches!(section.as_str(), "provider" | "mcp") {
        return Err(format!("section \"{section}\" is not removable"));
    }
    with_config_transaction(&app, &state, || {
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
    Ok(())
}

/// Drop `key` from the config JSON's `section` map, erroring when the config
/// is not plain JSON or the key is absent.
fn remove_key_from_config(text: &str, section: &str, key: &str) -> Result<String, String> {
    let mut cfg: serde_json::Value =
        serde_json::from_str(text).map_err(|e| format!("config is not plain JSON: {e}"))?;
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

/// The internal MVP exposes one approval mode. Legacy config is repaired when
/// the runtime starts, and this command never advertises full access.
#[tauri::command]
pub fn get_approval_mode(_app: AppHandle) -> Result<String, String> {
    Ok(crate::opencode_config::MODE_APPROVE.to_string())
}

/// Switch the approval mode and restart the sidecar so the permission rules
/// take effect. Returns the (stable-port) base URL when it was running.
#[tauri::command(async)]
pub fn set_approval_mode(
    app: AppHandle,
    state: State<'_, RuntimeState>,
    mode: String,
) -> Result<String, String> {
    let (path, restarted_url) = with_config_transaction(&app, &state, || {
        let path = effective_config_file(&app)?;
        let existing = std::fs::read_to_string(&path).unwrap_or_default();
        let updated = crate::opencode_config::set_permission_mode(&existing, &mode)?;
        write_private_atomic(&path, updated.as_bytes())?;
        Ok(path)
    })?;
    Ok(restarted_url.unwrap_or_else(|| path.to_string_lossy().to_string()))
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
) -> Result<String, String> {
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
    let (path, restarted_url) = with_config_transaction(&app, &state, || {
        let path = proxy_setting_file(&app)?;
        write_private_atomic(&path, line.as_bytes())?;
        Ok(path)
    })?;
    Ok(restarted_url.unwrap_or_else(|| path.to_string_lossy().to_string()))
}

/// Write the provider key/model into the app-private OpenCode config and restart
/// the sidecar so it picks them up. Returns the same base URL (stable port).
#[tauri::command(async)]
pub fn configure_opencode(
    app: AppHandle,
    state: State<'_, RuntimeState>,
    provider: String,
    api_key: String,
    model: String,
    base_url: Option<String>,
) -> Result<String, String> {
    let (path, restarted_url) = with_config_transaction(&app, &state, || {
        let path = opencode_config_file(&app)?;
        let existing = std::fs::read_to_string(&path).unwrap_or_default();
        let merged = merge_config(&existing, &provider, &api_key, &model, base_url.as_deref())?;
        write_private_atomic(&path, merged.as_bytes())?;
        Ok(path)
    })?;
    Ok(restarted_url.unwrap_or_else(|| path.to_string_lossy().to_string()))
}
