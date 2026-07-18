// Optional, app-managed Jupyter integration. The environment and server are
// local to Spark Agent; the authentication token is held by the operating-
// system credential manager and is never returned to the renderer.
use std::path::{Component, Path, PathBuf};
use std::sync::Mutex;
use tauri::{AppHandle, Manager, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

use crate::runtime::{free_port, workspace_dir};

const JUPYTER_TOKEN_SERVICE: &str = "io.github.shawliu998.sparkagent.jupyter";
const JUPYTER_TOKEN_ACCOUNT: &str = "managed-jupyter-token-v1";
const SERVER_META_VERSION: u8 = 2;
const JUPYTER_MCP_NAME: &str = "jupyter";
const LAB_PROCESS_MARKER: &str = "spark_agent_managed_jupyter_lab_v2";

// Pin every direct product-managed dependency. A transitive hash lock is still
// required before the complete environment can be called reproducible.
const PIP_SPEC: &[&str] = &[
    "jupyterlab==4.4.1",
    "ipykernel==6.29.5",
    "nbformat==5.10.4",
    "nbconvert==7.16.6",
    "numpy==2.2.5",
    "pandas==2.2.3",
    "scipy==1.15.2",
    "matplotlib==3.10.3",
    "scikit-learn==1.6.1",
    "statsmodels==0.14.4",
];
const RETIRED_JUPYTER_PACKAGES: &[&str] = &["jupyter-mcp-server", "jupyter-collaboration"];

// JUPYTER_TOKEN exists only in the initial Python child environment. Removing
// it before Lab starts prevents kernels and terminals spawned later from
// inheriting it. The token remains process memory used by IdentityProvider,
// but is absent from both OS argv and the post-bootstrap environment.
const LAB_BOOTSTRAP: &str = r#"import os
token = os.environ.pop("JUPYTER_TOKEN")
import site
site.main()
import sys
from traitlets.config import Config
SPARK_JUPYTER_PROCESS_MARKER = "spark_agent_managed_jupyter_lab_v2"
config = Config()
config.IdentityProvider.token = token
del token
from jupyter_server.serverapp import ServerApp
from jupyterlab.labapp import LabApp
class SparkServerApp(ServerApp):
    def write_server_info_file(self):
        pass
    def remove_server_info_file(self):
        pass
    def write_browser_open_files(self):
        pass
    def remove_browser_open_files(self):
        pass
# Keep the official LabApp module identity so Jupyter Server enables the
# `jupyterlab` extension rather than trying to load this `python -c` module as
# an extension package. Disable opportunistic discovery of unrelated packages
# left in an older managed environment.
LabApp.serverapp_class = SparkServerApp
LabApp.load_other_extensions = False
serverapp = LabApp.initialize_server(argv=sys.argv[1:], config=config)
serverapp.start()
"#;

struct ManagedJupyterChild {
    process: CommandChild,
    pid: u32,
}

#[derive(Default)]
pub struct JupyterState {
    child: Mutex<Option<ManagedJupyterChild>>,
    /// Serializes status/start/stop/re-root so a transition cannot publish a
    /// stale running state or leave two servers fighting over one port.
    lifecycle: Mutex<()>,
}

fn runtime_dir(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?
        .join("runtime"))
}

fn managed_runtime_subdir(runtime: &Path, name: &str, label: &str) -> Result<PathBuf, String> {
    if !runtime.is_absolute() {
        return Err("the Spark runtime path is not absolute".to_string());
    }
    std::fs::create_dir_all(runtime)
        .map_err(|error| format!("could not create the Spark runtime directory: {error}"))?;
    let canonical_runtime = runtime
        .canonicalize()
        .map_err(|error| format!("could not canonicalize the Spark runtime directory: {error}"))?;
    let directory = runtime.join(name);
    match std::fs::symlink_metadata(&directory) {
        Ok(metadata) if metadata.file_type().is_dir() && !metadata.file_type().is_symlink() => {}
        Ok(_) => return Err(format!("the managed {label} is not a regular directory")),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            std::fs::create_dir(&directory)
                .map_err(|error| format!("could not create the managed {label}: {error}"))?;
        }
        Err(error) => return Err(format!("could not inspect the managed {label}: {error}")),
    }
    let canonical = directory
        .canonicalize()
        .map_err(|error| format!("could not canonicalize the managed {label}: {error}"))?;
    if canonical.parent() != Some(canonical_runtime.as_path()) {
        return Err(format!("the managed {label} escaped the Spark runtime"));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&canonical, std::fs::Permissions::from_mode(0o700))
            .map_err(|error| format!("could not protect the managed {label}: {error}"))?;
    }
    Ok(canonical)
}

fn managed_env_dir_from_runtime(runtime: &Path) -> Result<PathBuf, String> {
    managed_runtime_subdir(runtime, "jupyter-env", "Jupyter environment")
}

fn env_dir(app: &AppHandle) -> Result<PathBuf, String> {
    managed_env_dir_from_runtime(&runtime_dir(app)?)
}

fn lab_home_dir(app: &AppHandle) -> Result<PathBuf, String> {
    managed_runtime_subdir(&runtime_dir(app)?, "jupyter-home", "Jupyter home")
}

struct JupyterPrivateDirs {
    home: PathBuf,
    config: PathBuf,
    runtime: PathBuf,
    data: PathBuf,
}

fn jupyter_private_dirs(app: &AppHandle) -> Result<JupyterPrivateDirs, String> {
    let runtime = runtime_dir(app)?;
    Ok(JupyterPrivateDirs {
        home: lab_home_dir(app)?,
        config: managed_runtime_subdir(&runtime, "jupyter-config", "Jupyter config")?,
        runtime: managed_runtime_subdir(&runtime, "jupyter-runtime", "Jupyter runtime")?,
        data: managed_runtime_subdir(&runtime, "jupyter-data", "Jupyter data")?,
    })
}

fn server_meta_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(env_dir(app)?.join("server.json"))
}

fn decimal_process_id(value: &str) -> bool {
    !value.is_empty() && value.bytes().all(|byte| byte.is_ascii_digit())
}

fn is_legacy_runtime_artifact_name(name: &str) -> bool {
    let matches = |prefix: &str, suffix: &str| {
        name.strip_prefix(prefix)
            .and_then(|value| value.strip_suffix(suffix))
            .is_some_and(decimal_process_id)
    };
    matches("jpserver-file-to-run-", "-open.html")
        || matches("jpserver-", "-open.html")
        || matches("jpserver-", ".json")
}

fn legacy_runtime_artifact_paths(runtime: &Path) -> Result<Vec<PathBuf>, String> {
    let mut paths = Vec::new();
    for entry in std::fs::read_dir(runtime)
        .map_err(|error| format!("could not inspect the private Jupyter runtime: {error}"))?
    {
        let entry =
            entry.map_err(|error| format!("could not inspect a Jupyter runtime entry: {error}"))?;
        if entry
            .file_name()
            .to_str()
            .is_some_and(is_legacy_runtime_artifact_name)
        {
            paths.push(entry.path());
        }
    }
    paths.sort();
    Ok(paths)
}

/// Remove only upstream Jupyter server-info and browser redirect artifacts
/// from Spark's dedicated private runtime directory. Symlinks are unlinked as
/// leaves and never followed; an unexpected directory/device fails closed.
fn scrub_legacy_runtime_artifacts(runtime: &Path, paths: &[PathBuf]) -> Result<(), String> {
    for path in paths {
        if path.parent() != Some(runtime) {
            return Err("a Jupyter runtime artifact escaped its managed directory".to_string());
        }
        let metadata = std::fs::symlink_metadata(path)
            .map_err(|error| format!("could not inspect a Jupyter runtime artifact: {error}"))?;
        if !metadata.file_type().is_file() && !metadata.file_type().is_symlink() {
            return Err("a managed Jupyter runtime artifact is not a file".to_string());
        }
        std::fs::remove_file(path)
            .map_err(|error| format!("could not scrub a Jupyter runtime artifact: {error}"))?;
    }
    #[cfg(unix)]
    if !paths.is_empty() {
        std::fs::File::open(runtime)
            .and_then(|directory| directory.sync_all())
            .map_err(|error| format!("could not sync the private Jupyter runtime: {error}"))?;
    }
    Ok(())
}

fn pid_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(env_dir(app)?.join("jupyter.pid"))
}

fn bin(app: &AppHandle, name: &str) -> Result<PathBuf, String> {
    let dir = env_dir(app)?;
    #[cfg(windows)]
    return Ok(dir.join("Scripts").join(format!("{name}.exe")));
    #[cfg(not(windows))]
    Ok(dir.join("bin").join(name))
}

fn validate_bin_parent(path: &Path) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "managed Jupyter executable has no parent".to_string())?;
    let metadata = std::fs::symlink_metadata(parent)
        .map_err(|_| "managed Jupyter executable directory is unavailable".to_string())?;
    if !metadata.file_type().is_dir() || metadata.file_type().is_symlink() {
        return Err("managed Jupyter executable directory is not a regular directory".into());
    }
    Ok(())
}

fn validated_regular_bin(app: &AppHandle, name: &str) -> Result<PathBuf, String> {
    let env = env_dir(app)?;
    let path = bin(app, name)?;
    validate_bin_parent(&path)?;
    let metadata = std::fs::symlink_metadata(&path)
        .map_err(|_| format!("managed Jupyter executable {name:?} is unavailable"))?;
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return Err(format!(
            "managed Jupyter executable {name:?} is not a regular file"
        ));
    }
    let canonical = path
        .canonicalize()
        .map_err(|_| format!("managed Jupyter executable {name:?} is unavailable"))?;
    if !canonical.starts_with(&env) {
        return Err(format!(
            "managed Jupyter executable {name:?} escaped its environment"
        ));
    }
    Ok(canonical)
}

fn validated_python(app: &AppHandle) -> Result<PathBuf, String> {
    let env = env_dir(app)?;
    let path = bin(app, "python")?;
    validate_bin_parent(&path)?;
    let metadata = std::fs::symlink_metadata(&path)
        .map_err(|_| "managed Jupyter Python is unavailable".to_string())?;
    if !metadata.file_type().is_file() && !metadata.file_type().is_symlink() {
        return Err("managed Jupyter Python is not a file".into());
    }
    let canonical = path
        .canonicalize()
        .map_err(|_| "managed Jupyter Python is unavailable".to_string())?;
    let uv_python = runtime_dir(app)?.join("uv-python");
    let in_uv_python = uv_python
        .canonicalize()
        .map(|root| canonical.starts_with(root))
        .unwrap_or(false);
    if !canonical.starts_with(&env) && !in_uv_python {
        return Err("managed Jupyter Python escaped Spark-managed roots".into());
    }
    Ok(path)
}

/// The managed env's Python, if safely provisioned. It also backs the local
/// notebook Run button so app and agent computations use the same packages.
pub(crate) fn env_python(app: &AppHandle) -> Option<PathBuf> {
    validated_python(app).ok()
}

#[derive(serde::Serialize, Clone, Copy, Debug, PartialEq, Eq)]
struct ServerMeta {
    version: u8,
    port: u16,
}

#[derive(Debug, PartialEq, Eq)]
enum ParsedServerMeta {
    V1 { port: u16, token: String },
    V2(ServerMeta),
}

fn parse_server_meta(text: &str) -> Result<ParsedServerMeta, String> {
    let value: serde_json::Value =
        serde_json::from_str(text).map_err(|_| "Jupyter server metadata is invalid".to_string())?;
    let object = value
        .as_object()
        .ok_or_else(|| "Jupyter server metadata must be an object".to_string())?;
    let version = match object.get("version") {
        None => 1,
        Some(value) => value
            .as_u64()
            .and_then(|value| u8::try_from(value).ok())
            .ok_or_else(|| "Jupyter server metadata version is invalid".to_string())?,
    };
    let port = object
        .get("port")
        .and_then(serde_json::Value::as_u64)
        .and_then(|value| u16::try_from(value).ok())
        .filter(|port| *port != 0)
        .ok_or_else(|| "Jupyter server metadata port is invalid".to_string())?;
    match version {
        1 => {
            if object
                .keys()
                .any(|key| !matches!(key.as_str(), "version" | "port" | "token"))
            {
                return Err("legacy Jupyter server metadata has unsupported fields".into());
            }
            let token = object
                .get("token")
                .and_then(serde_json::Value::as_str)
                .ok_or_else(|| "legacy Jupyter server metadata has no token".to_string())?;
            validate_token(token)?;
            Ok(ParsedServerMeta::V1 {
                port,
                token: token.to_string(),
            })
        }
        SERVER_META_VERSION => {
            if object.len() != 2
                || object
                    .keys()
                    .any(|key| !matches!(key.as_str(), "version" | "port"))
            {
                return Err("Jupyter server metadata v2 contains unsupported fields".into());
            }
            Ok(ParsedServerMeta::V2(ServerMeta {
                version: SERVER_META_VERSION,
                port,
            }))
        }
        _ => Err(format!(
            "unsupported Jupyter server metadata version {version}"
        )),
    }
}

fn validate_token(token: &str) -> Result<(), String> {
    if token.is_empty()
        || token.len() > 512
        || token.trim() != token
        || token.chars().any(char::is_control)
    {
        return Err("Jupyter token is empty or invalid".to_string());
    }
    Ok(())
}

trait JupyterTokenStore {
    fn get(&self) -> Result<Option<String>, String>;
    fn set(&self, token: &str) -> Result<(), String>;
}

struct SystemJupyterTokenStore;

impl SystemJupyterTokenStore {
    fn entry() -> Result<keyring::Entry, String> {
        keyring::Entry::new(JUPYTER_TOKEN_SERVICE, JUPYTER_TOKEN_ACCOUNT)
            .map_err(|_| "could not open the system Jupyter credential entry".to_string())
    }
}

impl JupyterTokenStore for SystemJupyterTokenStore {
    fn get(&self) -> Result<Option<String>, String> {
        match Self::entry()?.get_password() {
            Ok(token) => {
                validate_token(&token)?;
                Ok(Some(token))
            }
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(_) => Err("could not read the system Jupyter credential".to_string()),
        }
    }

    fn set(&self, token: &str) -> Result<(), String> {
        validate_token(token)?;
        Self::entry()?
            .set_password(token)
            .map_err(|_| "could not save the system Jupyter credential".to_string())
    }
}

struct ServerMaterial {
    meta: ServerMeta,
    token: String,
}

type AtomicWriter<'a> = dyn Fn(&Path, &[u8]) -> Result<(), String> + 'a;

fn read_optional(path: &Path) -> Result<Option<String>, String> {
    match std::fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_file() && !metadata.file_type().is_symlink() => {
            std::fs::read_to_string(path)
                .map(Some)
                .map_err(|error| format!("could not read Jupyter server metadata: {error}"))
        }
        Ok(_) => Err("Jupyter server metadata is not a regular file".to_string()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(format!(
            "could not inspect Jupyter server metadata: {error}"
        )),
    }
}

fn serialized_meta(meta: ServerMeta) -> Result<Vec<u8>, String> {
    serde_json::to_vec(&meta).map_err(|error| error.to_string())
}

fn reconcile_server_meta(
    path: &Path,
    store: &dyn JupyterTokenStore,
    create: bool,
    allocate_port: &mut dyn FnMut() -> Result<u16, String>,
    generate_token: &mut dyn FnMut() -> String,
    write_atomic: &AtomicWriter<'_>,
) -> Result<ServerMaterial, String> {
    let parsed = read_optional(path)?
        .map(|text| parse_server_meta(&text))
        .transpose()?;
    let stored = store.get()?;
    match parsed {
        Some(ParsedServerMeta::V2(meta)) => {
            let token = stored.ok_or_else(|| {
                "Jupyter server metadata exists but its system credential is missing".to_string()
            })?;
            Ok(ServerMaterial { meta, token })
        }
        Some(ParsedServerMeta::V1 { port, token }) => {
            let _ = (stored, token);
            // Released v1 tokens may also have reached OpenCode config. Rotate
            // instead of preserving that compromised value: durable keychain
            // replacement comes first, followed by plaintext metadata scrub.
            // A failed file write is retry-safe and rotates again next time.
            let token = generate_token();
            validate_token(&token)?;
            store.set(&token)?;
            let meta = ServerMeta {
                version: SERVER_META_VERSION,
                port,
            };
            write_atomic(path, &serialized_meta(meta)?)?;
            Ok(ServerMaterial { meta, token })
        }
        None if create => {
            // Metadata is the ownership record. Never resurrect a stale
            // keychain value when it is absent; create a fresh pair instead.
            let _ = stored;
            let token = generate_token();
            validate_token(&token)?;
            store.set(&token)?;
            let port = allocate_port()?;
            if port == 0 {
                return Err("could not allocate a Jupyter port".into());
            }
            let meta = ServerMeta {
                version: SERVER_META_VERSION,
                port,
            };
            write_atomic(path, &serialized_meta(meta)?)?;
            Ok(ServerMaterial { meta, token })
        }
        None => Err("Jupyter setup is incomplete (no server metadata)".to_string()),
    }
}

fn reconcile_server_meta_for_setup(
    path: &Path,
    store: &dyn JupyterTokenStore,
    allocate_port: &mut dyn FnMut() -> Result<u16, String>,
    generate_token: &mut dyn FnMut() -> String,
    write_atomic: &AtomicWriter<'_>,
) -> Result<ServerMaterial, String> {
    let parsed = read_optional(path)?
        .map(|text| parse_server_meta(&text))
        .transpose()?;
    if let Some(ParsedServerMeta::V2(meta)) = parsed {
        return match store.get()? {
            Some(token) => {
                validate_token(&token)?;
                Ok(ServerMaterial { meta, token })
            }
            None => rotate_v2_server_credential(meta, store, generate_token),
        };
    }
    reconcile_server_meta(
        path,
        store,
        true,
        allocate_port,
        generate_token,
        write_atomic,
    )
}

fn rotate_v2_server_credential(
    meta: ServerMeta,
    store: &dyn JupyterTokenStore,
    generate_token: &mut dyn FnMut() -> String,
) -> Result<ServerMaterial, String> {
    let token = generate_token();
    validate_token(&token)?;
    store.set(&token)?;
    Ok(ServerMaterial { meta, token })
}

fn write_private_atomic(path: &Path, contents: &[u8]) -> Result<(), String> {
    use std::io::Write;

    let parent = path
        .parent()
        .ok_or_else(|| "Jupyter metadata path has no parent".to_string())?;
    let parent_metadata = std::fs::symlink_metadata(parent)
        .map_err(|_| "Jupyter metadata parent is unavailable".to_string())?;
    if !parent_metadata.file_type().is_dir() || parent_metadata.file_type().is_symlink() {
        return Err("Jupyter metadata parent is not a regular directory".to_string());
    }
    match std::fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_file() && !metadata.file_type().is_symlink() => {}
        Ok(_) => return Err("Jupyter metadata destination is not a regular file".to_string()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(format!("could not inspect Jupyter metadata: {error}")),
    }
    let tmp = parent.join(format!(".server.{}.tmp", crate::runtime::random_hex(8)));
    let result = (|| -> Result<(), String> {
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&tmp).map_err(|error| error.to_string())?;
        file.write_all(contents)
            .map_err(|error| error.to_string())?;
        file.sync_all().map_err(|error| error.to_string())?;
        drop(file);
        std::fs::rename(&tmp, path).map_err(|error| error.to_string())?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))
                .map_err(|error| error.to_string())?;
            std::fs::File::open(parent)
                .and_then(|directory| directory.sync_all())
                .map_err(|error| error.to_string())?;
        }
        Ok(())
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
    result
}

fn random_token() -> String {
    crate::runtime::random_hex(16)
}

fn load_server(app: &AppHandle) -> Result<ServerMaterial, String> {
    reconcile_server_meta(
        &server_meta_path(app)?,
        &SystemJupyterTokenStore,
        false,
        &mut free_port,
        &mut random_token,
        &write_private_atomic,
    )
}

fn ensure_server_for_setup(app: &AppHandle) -> Result<ServerMaterial, String> {
    reconcile_server_meta_for_setup(
        &server_meta_path(app)?,
        &SystemJupyterTokenStore,
        &mut free_port,
        &mut random_token,
        &write_private_atomic,
    )
}

fn rotate_v2_server(meta: ServerMeta) -> Result<ServerMaterial, String> {
    rotate_v2_server_credential(meta, &SystemJupyterTokenStore, &mut random_token)
}

fn parsed_server_meta(app: &AppHandle) -> Result<Option<ParsedServerMeta>, String> {
    read_optional(&server_meta_path(app)?)?
        .map(|text| parse_server_meta(&text))
        .transpose()
}

#[derive(serde::Serialize, Debug, PartialEq, Eq)]
pub struct JupyterStatus {
    pub installed: bool,
    pub running: bool,
    /// True means a native secret-withholding MCP broker is registered. A
    /// direct OpenCode-launched jupyter-mcp-server never qualifies.
    pub registered: bool,
}

fn is_installed(app: &AppHandle) -> Result<bool, String> {
    let path = bin(app, "jupyter-lab")?;
    if !path.exists() {
        return Ok(false);
    }
    validated_regular_bin(app, "jupyter-lab")?;
    validated_python(app)?;
    Ok(true)
}

fn reconcile_jupyter_security_locked(app: &AppHandle, state: &JupyterState) -> Result<(), String> {
    let parsed = parsed_server_meta(app)?;
    let private_runtime =
        managed_runtime_subdir(&runtime_dir(app)?, "jupyter-runtime", "Jupyter runtime")?;
    let artifacts = legacy_runtime_artifact_paths(&private_runtime)?;
    let legacy_metadata = matches!(&parsed, Some(ParsedServerMeta::V1 { .. }));
    let replace_process = legacy_metadata || !artifacts.is_empty();
    let restart = replace_process && state.child.lock().unwrap().is_some();

    if replace_process {
        // A server authenticated by an exposed credential must not outlive the
        // rotation. Stop the tracked child, then kill only an identity-checked
        // orphan before touching credential or artifact state.
        stop_locked(state);
        kill_orphan_jupyter(app);
        scrub_legacy_runtime_artifacts(&private_runtime, &artifacts)?;
    } else if state.child.lock().unwrap().is_none() {
        // A force-quit orphan is never adopted into a new app generation.
        kill_orphan_jupyter(app);
    }

    match parsed {
        Some(ParsedServerMeta::V1 { .. }) => {
            let _ = load_server(app)?;
        }
        Some(ParsedServerMeta::V2(meta)) if !artifacts.is_empty() => {
            let _ = rotate_v2_server(meta)?;
        }
        Some(ParsedServerMeta::V2(_)) => {}
        None => {
            if restart {
                return Err(
                    "cannot restart Jupyter after credential cleanup without server metadata"
                        .to_string(),
                );
            }
        }
    }

    if restart {
        spawn_lab_locked(app, state)?;
    }
    Ok(())
}

/// Native pre-OpenCode boundary. Renderer ordering is only a UX optimization;
/// every direct sidecar start calls this while holding the Jupyter lifecycle.
pub(crate) fn reconcile_jupyter_security(app: &AppHandle) -> Result<(), String> {
    let state = app.state::<JupyterState>();
    let _lifecycle = state.lifecycle.lock().unwrap();
    reconcile_jupyter_security_locked(app, &state)
}

fn status_locked(app: &AppHandle, state: &JupyterState) -> Result<JupyterStatus, String> {
    reconcile_jupyter_security_locked(app, state)?;
    let installed = is_installed(app)?;
    let parsed = parsed_server_meta(app)?;
    if installed && parsed.is_none() {
        return Err("Jupyter setup is incomplete (no server metadata)".to_string());
    }
    if parsed.is_some() {
        let _ = load_server(app)?;
    }
    Ok(JupyterStatus {
        installed,
        running: state.child.lock().unwrap().is_some(),
        // No safe native Jupyter-MCP broker exists yet. Returning false avoids
        // treating the retired plaintext frontend registration as managed.
        registered: false,
    })
}

#[tauri::command]
pub fn jupyter_status(
    app: AppHandle,
    state: State<'_, JupyterState>,
) -> Result<JupyterStatus, String> {
    let _lifecycle = state.lifecycle.lock().unwrap();
    status_locked(&app, &state)
}

/// Provision the isolated Jupyter environment with the bundled uv. All pins
/// are exact and uv itself runs with its product-owned fixed index policy.
#[tauri::command]
pub async fn setup_jupyter(app: AppHandle) -> Result<(), String> {
    let dir = env_dir(&app)?;

    crate::uv::run_uv(
        &app,
        "jupyter",
        vec![
            "venv".into(),
            dir.to_string_lossy().to_string(),
            "--python".into(),
            "3.12".into(),
            "--allow-existing".into(),
        ],
        "uv venv",
    )
    .await?;

    let python = validated_python(&app)?;
    let mut uninstall_args = vec![
        "pip".to_string(),
        "uninstall".to_string(),
        "--python".to_string(),
        python
            .to_str()
            .ok_or_else(|| "managed Jupyter Python path is not valid UTF-8".to_string())?
            .to_string(),
    ];
    uninstall_args.extend(RETIRED_JUPYTER_PACKAGES.iter().map(|name| name.to_string()));
    crate::uv::run_uv(
        &app,
        "jupyter",
        uninstall_args,
        "uv pip uninstall retired Jupyter packages",
    )
    .await?;

    let mut args = vec![
        "pip".to_string(),
        "install".to_string(),
        "--python".to_string(),
        python
            .to_str()
            .ok_or_else(|| "managed Jupyter Python path is not valid UTF-8".to_string())?
            .to_string(),
    ];
    args.extend(PIP_SPEC.iter().map(|spec| spec.to_string()));
    crate::uv::run_uv(&app, "jupyter", args, "uv pip install").await?;

    validated_regular_bin(&app, "jupyter-lab")?;
    let _ = ensure_server_for_setup(&app)?;
    Ok(())
}

struct LabLaunchPlan {
    args: Vec<String>,
    environment: Vec<(&'static str, String)>,
}

fn lab_launch_plan(
    port: u16,
    workspace: &Path,
    private_dirs: &JupyterPrivateDirs,
    safe_path: &str,
    token: &str,
) -> Result<LabLaunchPlan, String> {
    if port == 0 {
        return Err("Jupyter port is invalid".to_string());
    }
    validate_token(token)?;
    let workspace = workspace
        .to_str()
        .ok_or_else(|| "workspace path is not valid UTF-8".to_string())?;
    let private_path = |path: &Path, label: &str| {
        path.to_str()
            .map(str::to_string)
            .ok_or_else(|| format!("managed {label} path is not valid UTF-8"))
    };
    let home = private_path(&private_dirs.home, "Jupyter home")?;
    let config = private_path(&private_dirs.config, "Jupyter config")?;
    let runtime = private_path(&private_dirs.runtime, "Jupyter runtime")?;
    let data = private_path(&private_dirs.data, "Jupyter data")?;
    Ok(LabLaunchPlan {
        args: vec![
            "-I".to_string(),
            "-S".to_string(),
            "-c".to_string(),
            LAB_BOOTSTRAP.to_string(),
            "--no-browser".to_string(),
            "--ip".to_string(),
            "127.0.0.1".to_string(),
            "--port".to_string(),
            port.to_string(),
            format!("--ServerApp.root_dir={workspace}"),
            "--ServerApp.port_retries=0".to_string(),
        ],
        environment: vec![
            ("HOME", home),
            ("PATH", safe_path.to_string()),
            ("JUPYTER_CONFIG_DIR", config),
            ("JUPYTER_RUNTIME_DIR", runtime),
            ("JUPYTER_DATA_DIR", data),
            ("PYTHONNOUSERSITE", "1".to_string()),
            ("JUPYTER_TOKEN", token.to_string()),
        ],
    })
}

fn safe_lab_path(python: &Path) -> Result<String, String> {
    #[cfg(unix)]
    {
        let _ = python;
        Ok("/usr/bin:/bin:/usr/sbin:/sbin".to_string())
    }
    #[cfg(windows)]
    {
        let python_dir = python
            .parent()
            .and_then(Path::to_str)
            .ok_or_else(|| "managed Jupyter Python directory is not valid UTF-8".to_string())?;
        let system_root = std::env::var("SystemRoot").unwrap_or_else(|_| "C:\\Windows".into());
        Ok(format!("{python_dir};{system_root}\\System32"))
    }
}

fn managed_lab_command_matches(command_line: &str, python: &Path) -> bool {
    let expected = python.to_str();
    let canonical = python.canonicalize().ok();
    let canonical = canonical.as_deref().and_then(Path::to_str);
    let exact_python = expected.is_some_and(|path| command_line.starts_with(path))
        || canonical.is_some_and(|path| command_line.starts_with(path));
    exact_python && command_line.contains(LAB_PROCESS_MARKER)
}

fn legacy_lab_command_matches(command_line: &str, lab: &Path) -> bool {
    let expected = lab.to_str();
    let canonical = lab.canonicalize().ok();
    let canonical = canonical.as_deref().and_then(Path::to_str);
    let exact_lab = expected.is_some_and(|path| command_line.contains(path))
        || canonical.is_some_and(|path| command_line.contains(path));
    exact_lab
        && command_line.contains("--IdentityProvider.token=")
        && command_line.contains("--ServerApp.root_dir=")
}

fn recorded_pid(path: &Path) -> Option<u32> {
    let metadata = std::fs::symlink_metadata(path).ok()?;
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return None;
    }
    std::fs::read_to_string(path).ok()?.trim().parse().ok()
}

/// Kill an orphan from a force-quit before attempting to bind the stable port.
/// A recorded PID is killed only when its live command line contains the fixed
/// Lab bootstrap marker and the validated managed Python. Ordinary notebook
/// kernels use the same interpreter but never carry this marker.
fn kill_orphan_jupyter(app: &AppHandle) {
    #[cfg(unix)]
    {
        let candidate = (|| {
            let python = validated_python(app).ok()?;
            let legacy_lab = bin(app, "jupyter-lab").ok()?;
            let path = pid_path(app).ok()?;
            let pid = recorded_pid(&path)?;
            let output = crate::runtime::quiet_command("/bin/ps")
                .args(["-p", &pid.to_string(), "-o", "command="])
                .output()
                .ok()?;
            if !output.status.success() {
                return None;
            }
            let command_line = String::from_utf8(output.stdout).ok()?;
            let command_line = command_line.trim();
            (managed_lab_command_matches(command_line, &python)
                || legacy_lab_command_matches(command_line, &legacy_lab))
            .then_some(pid)
        })();
        if let Some(pid) = candidate {
            let _ = crate::runtime::quiet_command("/bin/kill")
                .args(["-9", &pid.to_string()])
                .output();
            std::thread::sleep(std::time::Duration::from_millis(400));
        }
    }
    // Windows cleanup is deliberately fail-closed until native process-path
    // and command-line verification is available; PID + image name is not
    // enough to distinguish Lab from a notebook kernel after PID reuse.
    #[cfg(windows)]
    let _ = app;
}

fn canonical_workspace(app: &AppHandle) -> Result<PathBuf, String> {
    let workspace = workspace_dir(app)?;
    let canonical = workspace
        .canonicalize()
        .map_err(|error| format!("could not canonicalize the active workspace: {error}"))?;
    if !canonical.is_dir() {
        return Err("the active workspace is not a directory".to_string());
    }
    Ok(canonical)
}

#[cfg(any(target_os = "macos", test))]
fn lsof_output_proves_listener(output: &[u8], pid: u32, port: u16) -> bool {
    let Ok(output) = std::str::from_utf8(output) else {
        return false;
    };
    let expected_pid = format!("p{pid}");
    let expected_listener = format!("n127.0.0.1:{port}");
    let mut current_process_matches = false;
    for line in output.lines() {
        if line.starts_with('p') {
            current_process_matches = line == expected_pid;
        } else if current_process_matches && line == expected_listener {
            return true;
        }
    }
    false
}

#[cfg(target_os = "macos")]
fn child_owns_listener(pid: u32, port: u16) -> Result<bool, String> {
    let lsof = Path::new("/usr/sbin/lsof");
    let metadata = std::fs::symlink_metadata(lsof)
        .map_err(|error| format!("could not inspect the system lsof executable: {error}"))?;
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return Err("the system lsof executable is not a regular file".to_string());
    }
    let pid_argument = pid.to_string();
    let endpoint = format!("-iTCP@127.0.0.1:{port}");
    let output = crate::runtime::quiet_command(lsof)
        .args([
            "-nP",
            "-a",
            "-p",
            pid_argument.as_str(),
            endpoint.as_str(),
            "-sTCP:LISTEN",
            "-F",
            "pn",
        ])
        .output()
        .map_err(|error| format!("could not verify the Jupyter listener owner: {error}"))?;
    if !output.status.success() {
        return Ok(false);
    }
    Ok(lsof_output_proves_listener(&output.stdout, pid, port))
}

#[cfg(not(target_os = "macos"))]
fn child_owns_listener(_pid: u32, _port: u16) -> Result<bool, String> {
    Err(
        "secure Jupyter listener ownership verification is unavailable on this platform"
            .to_string(),
    )
}

fn wait_for_lab_ready(
    events: &mut tauri::async_runtime::Receiver<CommandEvent>,
    pid: u32,
    port: u16,
) -> Result<(), String> {
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
    loop {
        while let Ok(event) = events.try_recv() {
            match event {
                CommandEvent::Terminated(status) => {
                    return Err(format!(
                        "Jupyter exited before readiness (code {:?}, signal {:?})",
                        status.code, status.signal
                    ))
                }
                CommandEvent::Error(error) => {
                    return Err(format!("Jupyter failed before readiness: {error}"))
                }
                _ => {}
            }
        }
        if child_owns_listener(pid, port)? {
            return Ok(());
        }
        if std::time::Instant::now() >= deadline {
            return Err("Jupyter did not become ready before the startup timeout".to_string());
        }
        std::thread::sleep(std::time::Duration::from_millis(75));
    }
}

fn spawn_lab_locked(app: &AppHandle, state: &JupyterState) -> Result<(), String> {
    validated_regular_bin(app, "jupyter-lab")?;
    let python = validated_python(app)?;
    // Caller proved no current managed child is published. Remove only an
    // identity-checked legacy/new orphan before any v1 token rotation.
    kill_orphan_jupyter(app);
    let material = load_server(app)?;
    let workspace = canonical_workspace(app)?;
    let private_dirs = jupyter_private_dirs(app)?;
    let safe_path = safe_lab_path(&python)?;
    let plan = lab_launch_plan(
        material.meta.port,
        &workspace,
        &private_dirs,
        &safe_path,
        &material.token,
    )?;

    let mut command = app
        .shell()
        .command(&python)
        .env_clear()
        .args(plan.args)
        .current_dir(&workspace);
    for (key, value) in plan.environment {
        command = command.env(key, value);
    }
    let (mut events, child) = command
        .spawn()
        .map_err(|error| format!("failed to start Jupyter: {error}"))?;
    let pid = child.pid();
    let pid_file = pid_path(app)?;
    if let Err(error) = write_private_atomic(&pid_file, pid.to_string().as_bytes()) {
        let _ = child.kill();
        return Err(format!(
            "could not record the managed Jupyter process identity: {error}"
        ));
    }
    if let Err(error) = wait_for_lab_ready(&mut events, pid, material.meta.port) {
        let _ = child.kill();
        let _ = std::fs::remove_file(&pid_file);
        return Err(error);
    }
    *state.child.lock().unwrap() = Some(ManagedJupyterChild {
        process: child,
        pid,
    });

    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            if matches!(event, CommandEvent::Terminated(_) | CommandEvent::Error(_)) {
                break;
            }
        }
        let state = app.state::<JupyterState>();
        let _lifecycle = state.lifecycle.lock().unwrap();
        let mut current = state.child.lock().unwrap();
        if current.as_ref().is_some_and(|current| current.pid == pid) {
            current.take();
            if let Ok(path) = pid_path(&app) {
                let _ = std::fs::remove_file(path);
            }
        }
    });
    Ok(())
}

#[tauri::command(async)]
pub fn start_jupyter(
    app: AppHandle,
    state: State<'_, JupyterState>,
) -> Result<JupyterStatus, String> {
    let _lifecycle = state.lifecycle.lock().unwrap();
    reconcile_jupyter_security_locked(&app, &state)?;
    if state.child.lock().unwrap().is_none() {
        spawn_lab_locked(&app, &state)?;
    }
    status_locked(&app, &state)
}

fn stop_locked(state: &JupyterState) {
    if let Some(child) = state.child.lock().unwrap().take() {
        let _ = child.process.kill();
    }
}

/// Follow a workspace switch. The lifecycle lock makes stop + spawn one
/// transaction from status/start's perspective.
pub fn reroot_jupyter(app: &AppHandle) {
    let app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<JupyterState>();
        let _lifecycle = state.lifecycle.lock().unwrap();
        if state.child.lock().unwrap().is_none() {
            return;
        }
        if let Err(error) = reconcile_jupyter_security_locked(&app, &state) {
            eprintln!("Jupyter security reconciliation failed before re-root: {error}");
            return;
        }
        stop_locked(&state);
        if let Err(error) = spawn_lab_locked(&app, &state) {
            eprintln!("Jupyter re-root failed: {error}");
        }
    });
}

/// Exit-only cleanup API retained for the app lifecycle hook.
pub fn kill_jupyter(state: &JupyterState) {
    let _lifecycle = state.lifecycle.lock().unwrap();
    stop_locked(state);
}

fn percent_encode_path_segment(segment: &str) -> String {
    let mut encoded = String::new();
    for byte in segment.as_bytes() {
        if byte.is_ascii_alphanumeric() || matches!(*byte, b'-' | b'.' | b'_' | b'~') {
            encoded.push(char::from(*byte));
        } else {
            encoded.push_str(&format!("%{byte:02X}"));
        }
    }
    encoded
}

fn notebook_route(notebook: Option<&str>) -> Result<String, String> {
    let Some(notebook) = notebook.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(String::new());
    };
    let path = Path::new(notebook);
    if path.is_absolute()
        || notebook.contains('\\')
        || notebook.chars().any(char::is_control)
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err("notebook path must be a normalized workspace-relative path".to_string());
    }
    path.components()
        .map(|component| match component {
            Component::Normal(segment) => segment
                .to_str()
                .map(percent_encode_path_segment)
                .ok_or_else(|| "notebook path is not valid UTF-8".to_string()),
            _ => unreachable!("components were validated above"),
        })
        .collect::<Result<Vec<_>, _>>()
        .map(|segments| format!("/tree/{}", segments.join("/")))
}

fn validated_notebook_route(workspace: &Path, notebook: Option<&str>) -> Result<String, String> {
    let route = notebook_route(notebook)?;
    let Some(notebook) = notebook.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(route);
    };
    let target = workspace.join(notebook);
    let metadata = std::fs::symlink_metadata(&target)
        .map_err(|_| "notebook is unavailable in the active workspace".to_string())?;
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return Err("notebook is not a regular workspace file".to_string());
    }
    let canonical = target
        .canonicalize()
        .map_err(|_| "notebook is unavailable in the active workspace".to_string())?;
    if !canonical.starts_with(workspace) {
        return Err("notebook escaped the active workspace".to_string());
    }
    Ok(route)
}

fn jupyter_url(meta: ServerMeta, token: &str, route: &str) -> Result<String, String> {
    validate_token(token)?;
    Ok(format!(
        "http://127.0.0.1:{}/lab{}?token={}",
        meta.port,
        route,
        percent_encode_path_segment(token)
    ))
}

#[cfg(target_os = "macos")]
fn open_url_native(url: &str) -> Result<(), String> {
    use std::ffi::{c_char, c_void, CString};

    #[link(name = "AppKit", kind = "framework")]
    extern "C" {}
    #[link(name = "Foundation", kind = "framework")]
    extern "C" {}
    #[link(name = "objc")]
    extern "C" {
        fn objc_getClass(name: *const c_char) -> *mut c_void;
        fn sel_registerName(name: *const c_char) -> *mut c_void;
        fn objc_msgSend();
    }

    unsafe fn send_id(receiver: *mut c_void, selector: *mut c_void) -> *mut c_void {
        // SAFETY: the cast matches Objective-C methods with no explicit args
        // and object return values used below.
        let send = unsafe {
            std::mem::transmute::<
                unsafe extern "C" fn(),
                unsafe extern "C" fn(*mut c_void, *mut c_void) -> *mut c_void,
            >(objc_msgSend)
        };
        // SAFETY: receiver and selector are returned by the Objective-C runtime.
        unsafe { send(receiver, selector) }
    }

    unsafe fn send_c_string(
        receiver: *mut c_void,
        selector: *mut c_void,
        value: *const c_char,
    ) -> *mut c_void {
        // SAFETY: this signature matches +[NSString stringWithUTF8String:].
        let send = unsafe {
            std::mem::transmute::<
                unsafe extern "C" fn(),
                unsafe extern "C" fn(*mut c_void, *mut c_void, *const c_char) -> *mut c_void,
            >(objc_msgSend)
        };
        // SAFETY: value is a live NUL-terminated CString.
        unsafe { send(receiver, selector, value) }
    }

    unsafe fn send_object(
        receiver: *mut c_void,
        selector: *mut c_void,
        value: *mut c_void,
    ) -> *mut c_void {
        // SAFETY: this signature matches object-argument/object-return methods.
        let send = unsafe {
            std::mem::transmute::<
                unsafe extern "C" fn(),
                unsafe extern "C" fn(*mut c_void, *mut c_void, *mut c_void) -> *mut c_void,
            >(objc_msgSend)
        };
        // SAFETY: all pointers are Objective-C objects/runtime selectors.
        unsafe { send(receiver, selector, value) }
    }

    unsafe fn send_bool(receiver: *mut c_void, selector: *mut c_void, value: *mut c_void) -> i8 {
        // SAFETY: this signature matches -[NSWorkspace openURL:] returning BOOL.
        let send = unsafe {
            std::mem::transmute::<
                unsafe extern "C" fn(),
                unsafe extern "C" fn(*mut c_void, *mut c_void, *mut c_void) -> i8,
            >(objc_msgSend)
        };
        // SAFETY: all pointers are Objective-C objects/runtime selectors.
        unsafe { send(receiver, selector, value) }
    }

    unsafe fn send_void(receiver: *mut c_void, selector: *mut c_void) {
        // SAFETY: this signature matches -[NSAutoreleasePool drain].
        let send = unsafe {
            std::mem::transmute::<
                unsafe extern "C" fn(),
                unsafe extern "C" fn(*mut c_void, *mut c_void),
            >(objc_msgSend)
        };
        // SAFETY: receiver and selector are Objective-C runtime objects.
        unsafe { send(receiver, selector) }
    }

    fn c_string(value: &str) -> Result<CString, String> {
        CString::new(value).map_err(|_| "URL contains a NUL byte".to_string())
    }

    let ns_string_name = c_string("NSString")?;
    let ns_url_name = c_string("NSURL")?;
    let workspace_name = c_string("NSWorkspace")?;
    let pool_name = c_string("NSAutoreleasePool")?;
    let alloc_selector = c_string("alloc")?;
    let init_selector = c_string("init")?;
    let drain_selector = c_string("drain")?;
    let string_selector = c_string("stringWithUTF8String:")?;
    let url_selector = c_string("URLWithString:")?;
    let shared_selector = c_string("sharedWorkspace")?;
    let open_selector = c_string("openURL:")?;
    let url = c_string(url)?;

    // SAFETY: class and selector names are fixed framework APIs. No raw pointer
    // escapes this function and every null result is checked before use.
    unsafe {
        let ns_string_class = objc_getClass(ns_string_name.as_ptr());
        let ns_url_class = objc_getClass(ns_url_name.as_ptr());
        let workspace_class = objc_getClass(workspace_name.as_ptr());
        let pool_class = objc_getClass(pool_name.as_ptr());
        if ns_string_class.is_null()
            || ns_url_class.is_null()
            || workspace_class.is_null()
            || pool_class.is_null()
        {
            return Err("macOS URL-opening classes are unavailable".to_string());
        }
        let pool = send_id(
            send_id(pool_class, sel_registerName(alloc_selector.as_ptr())),
            sel_registerName(init_selector.as_ptr()),
        );
        if pool.is_null() {
            return Err("could not create a macOS autorelease pool".to_string());
        }
        let result = (|| {
            let string = send_c_string(
                ns_string_class,
                sel_registerName(string_selector.as_ptr()),
                url.as_ptr(),
            );
            if string.is_null() {
                return Err("could not create a macOS URL string".to_string());
            }
            let ns_url = send_object(
                ns_url_class,
                sel_registerName(url_selector.as_ptr()),
                string,
            );
            let workspace = send_id(workspace_class, sel_registerName(shared_selector.as_ptr()));
            if ns_url.is_null() || workspace.is_null() {
                return Err("could not create the macOS Jupyter URL".to_string());
            }
            if send_bool(workspace, sel_registerName(open_selector.as_ptr()), ns_url) == 0 {
                return Err("macOS refused to open the Jupyter URL".to_string());
            }
            Ok(())
        })();
        send_void(pool, sel_registerName(drain_selector.as_ptr()));
        result
    }
}

#[cfg(not(target_os = "macos"))]
fn open_url_native(_url: &str) -> Result<(), String> {
    Err("token-safe native Jupyter URL opening is currently supported only on macOS".to_string())
}

fn open_url_on_main_thread(app: &AppHandle, url: String) -> Result<(), String> {
    let (sender, receiver) = std::sync::mpsc::sync_channel(1);
    app.run_on_main_thread(move || {
        let _ = sender.send(open_url_native(&url));
    })
    .map_err(|error| format!("could not schedule the Jupyter URL on the main thread: {error}"))?;
    receiver
        .recv_timeout(std::time::Duration::from_secs(5))
        .map_err(|_| "macOS did not finish opening the Jupyter URL".to_string())?
}

/// Start the managed server if needed and open it without exposing its URL or
/// token to the renderer. macOS uses NSWorkspace directly, never `/usr/bin/open`.
#[tauri::command(async)]
pub fn open_jupyter_lab(
    app: AppHandle,
    state: State<'_, JupyterState>,
    notebook: Option<String>,
) -> Result<bool, String> {
    let lifecycle = state.lifecycle.lock().unwrap();
    reconcile_jupyter_security_locked(&app, &state)?;
    if !is_installed(&app)? {
        return Ok(false);
    }
    let workspace = canonical_workspace(&app)?;
    let route = validated_notebook_route(&workspace, notebook.as_deref())?;
    if state.child.lock().unwrap().is_none() {
        spawn_lab_locked(&app, &state)?;
    }
    let material = load_server(&app)?;
    let url = jupyter_url(material.meta, &material.token, &route)?;
    drop(lifecycle);
    open_url_on_main_thread(&app, url)?;
    Ok(true)
}

fn exact_legacy_mcp_entry(
    entry: &serde_json::Value,
    expected_command: &str,
    expected_url: &str,
) -> Result<bool, String> {
    let Some(entry) = entry.as_object() else {
        return Ok(false);
    };
    if entry
        .keys()
        .any(|key| !matches!(key.as_str(), "type" | "command" | "enabled" | "environment"))
        || entry.get("type").and_then(serde_json::Value::as_str) != Some("local")
        || entry
            .get("command")
            .and_then(serde_json::Value::as_array)
            .filter(|command| command.len() == 1)
            .and_then(|command| command[0].as_str())
            != Some(expected_command)
        || entry.get("enabled").and_then(serde_json::Value::as_bool) != Some(true)
    {
        return Ok(false);
    }
    let Some(environment) = entry
        .get("environment")
        .and_then(serde_json::Value::as_object)
    else {
        return Ok(false);
    };
    if environment.len() != 3
        || environment
            .get("JUPYTER_URL")
            .and_then(serde_json::Value::as_str)
            != Some(expected_url)
        || environment
            .get("ALLOW_IMG_OUTPUT")
            .and_then(serde_json::Value::as_str)
            != Some("true")
    {
        return Ok(false);
    }
    let Some(token) = environment
        .get("JUPYTER_TOKEN")
        .and_then(serde_json::Value::as_str)
    else {
        return Ok(false);
    };
    // Ownership is the exact retired command/URL/shape, not equality with the
    // current keychain token: v1 migration deliberately rotates that leaked
    // token before this config is scrubbed.
    validate_token(token)?;
    Ok(true)
}

fn entry_targets_managed_command(entry: &serde_json::Value, expected_command: &str) -> bool {
    match entry.get("command") {
        Some(serde_json::Value::Array(command)) => {
            command.first().and_then(serde_json::Value::as_str) == Some(expected_command)
        }
        Some(serde_json::Value::String(command)) => command == expected_command,
        _ => false,
    }
}

fn config_targets_managed_command(existing: &str, expected_command: &str) -> Result<bool, String> {
    let config = crate::opencode_config::parse_config(existing, "OpenCode config")?;
    Ok(config
        .get("mcp")
        .and_then(serde_json::Value::as_object)
        .and_then(|mcp| mcp.get(JUPYTER_MCP_NAME))
        .is_some_and(|entry| entry_targets_managed_command(entry, expected_command)))
}

fn plan_jupyter_mcp_reconcile(
    existing: &str,
    expected_command: &str,
    expected_url: &str,
) -> Result<Option<String>, String> {
    let mut config = crate::opencode_config::parse_config(existing, "OpenCode config")?;
    let Some(mcp) = config.get_mut("mcp") else {
        return Ok(None);
    };
    let mcp = mcp
        .as_object_mut()
        .ok_or_else(|| "OpenCode config mcp must be an object".to_string())?;
    let Some(entry) = mcp.get(JUPYTER_MCP_NAME) else {
        return Ok(None);
    };
    // A user-owned same-name connector with a different command is preserved.
    // Collision safety means Spark never overwrites it; it does not prevent the
    // rest of OpenCode from starting.
    if !entry_targets_managed_command(entry, expected_command) {
        return Ok(None);
    }
    if !exact_legacy_mcp_entry(entry, expected_command, expected_url)? {
        return Err(
            "Spark-managed Jupyter MCP config has conflicting fields; config was not changed"
                .to_string(),
        );
    }
    // This exact shape is the retired Spark frontend registration. Removing it
    // both scrubs its plaintext token and disables unsafe direct execution. A
    // future native broker may register a new, separately owned entry.
    mcp.remove(JUPYTER_MCP_NAME);
    serde_json::to_string_pretty(&config)
        .map(Some)
        .map_err(|error| error.to_string())
}

/// Runtime integration point: call under RuntimeState's config transaction for
/// every effective/legacy config file, then atomically persist any `Some`
/// output. Custom same-name entries fail closed and are never overwritten.
pub(crate) fn reconcile_jupyter_mcp_config(
    app: &AppHandle,
    existing: &str,
) -> Result<Option<String>, String> {
    let command = bin(app, "jupyter-mcp-server")?;
    let command = command
        .to_str()
        .ok_or_else(|| "managed Jupyter MCP path is not valid UTF-8".to_string())?;
    // Fresh installs and unrelated user-owned connectors are no-ops and must
    // not require server metadata or a keychain entry during AppShell startup.
    if !config_targets_managed_command(existing, command)? {
        return Ok(None);
    }
    let material = load_server(app)?;
    let url = format!("http://127.0.0.1:{}", material.meta.port);
    plan_jupyter_mcp_reconcile(existing, command, &url)
}

#[cfg(test)]
mod tests {
    use super::{
        config_targets_managed_command, exact_legacy_mcp_entry, lab_launch_plan,
        legacy_lab_command_matches, legacy_runtime_artifact_paths, lsof_output_proves_listener,
        managed_env_dir_from_runtime, managed_lab_command_matches, notebook_route,
        parse_server_meta, plan_jupyter_mcp_reconcile, read_optional, reconcile_server_meta,
        reconcile_server_meta_for_setup, rotate_v2_server_credential,
        scrub_legacy_runtime_artifacts, validated_notebook_route, JupyterPrivateDirs,
        JupyterStatus, JupyterTokenStore, ParsedServerMeta, ServerMeta, LAB_BOOTSTRAP,
        LAB_PROCESS_MARKER, PIP_SPEC, RETIRED_JUPYTER_PACKAGES, SERVER_META_VERSION,
    };
    use std::cell::RefCell;
    use std::path::Path;

    #[derive(Default)]
    struct MemoryStore {
        value: RefCell<Option<String>>,
        events: RefCell<Vec<&'static str>>,
    }

    impl JupyterTokenStore for MemoryStore {
        fn get(&self) -> Result<Option<String>, String> {
            Ok(self.value.borrow().clone())
        }

        fn set(&self, token: &str) -> Result<(), String> {
            self.events.borrow_mut().push("keychain-set");
            *self.value.borrow_mut() = Some(token.to_string());
            Ok(())
        }
    }

    fn root(label: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!(
            "jupyter-{label}-{}-{}",
            std::process::id(),
            crate::runtime::random_hex(4)
        ))
    }

    fn atomic(path: &Path, bytes: &[u8]) -> Result<(), String> {
        std::fs::write(path, bytes).map_err(|error| error.to_string())
    }

    #[test]
    fn legacy_meta_saves_keychain_before_scrubbing_plaintext() {
        let dir = root("migration");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("server.json");
        std::fs::write(&path, r#"{"port":4321,"token":"legacy-token"}"#).unwrap();
        let store = MemoryStore::default();
        let events = &store.events;
        let writer = |path: &Path, bytes: &[u8]| {
            events.borrow_mut().push("metadata-write");
            atomic(path, bytes)
        };
        let material = reconcile_server_meta(
            &path,
            &store,
            false,
            &mut || Ok(9999),
            &mut || "new-token".to_string(),
            &writer,
        )
        .unwrap();
        assert_eq!(material.meta.port, 4321);
        assert_eq!(material.token, "new-token");
        assert_eq!(store.value.borrow().as_deref(), Some("new-token"));
        assert_eq!(*events.borrow(), ["keychain-set", "metadata-write"]);
        let output = std::fs::read_to_string(&path).unwrap();
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&output).unwrap(),
            serde_json::json!({"version": 2, "port": 4321})
        );
        assert!(!output.contains("token"));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn legacy_meta_rotates_even_when_a_stale_keychain_value_exists() {
        let dir = root("stale-keychain");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("server.json");
        let original = r#"{"version":1,"port":4321,"token":"file-token"}"#;
        std::fs::write(&path, original).unwrap();
        let store = MemoryStore {
            value: RefCell::new(Some("different-token".to_string())),
            ..Default::default()
        };
        let material = reconcile_server_meta(
            &path,
            &store,
            false,
            &mut || Ok(9999),
            &mut || "new-token".to_string(),
            &atomic,
        )
        .unwrap();
        assert_eq!(material.token, "new-token");
        assert_eq!(store.value.borrow().as_deref(), Some("new-token"));
        assert_eq!(*store.events.borrow(), ["keychain-set"]);
        assert!(!std::fs::read_to_string(&path)
            .unwrap()
            .contains("file-token"));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn missing_metadata_never_resurrects_a_stale_keychain_token() {
        let dir = root("missing-meta");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("server.json");
        let store = MemoryStore {
            value: RefCell::new(Some("stale-token".to_string())),
            ..Default::default()
        };
        let material = reconcile_server_meta(
            &path,
            &store,
            true,
            &mut || Ok(4321),
            &mut || "fresh-token".to_string(),
            &atomic,
        )
        .unwrap();
        assert_eq!(material.token, "fresh-token");
        assert_eq!(store.value.borrow().as_deref(), Some("fresh-token"));
        assert_eq!(
            parse_server_meta(&std::fs::read_to_string(&path).unwrap()).unwrap(),
            ParsedServerMeta::V2(ServerMeta {
                version: SERVER_META_VERSION,
                port: 4321
            })
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn failed_v1_scrub_is_retry_safe_after_keychain_rotation() {
        let dir = root("retry");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("server.json");
        let original = r#"{"port":4321,"token":"legacy-token"}"#;
        std::fs::write(&path, original).unwrap();
        let store = MemoryStore::default();
        let first = reconcile_server_meta(
            &path,
            &store,
            false,
            &mut || Ok(9999),
            &mut || "rotated-once".to_string(),
            &|_, _| Err("simulated durable write failure".to_string()),
        )
        .err()
        .unwrap();
        assert!(first.contains("simulated"));
        assert_eq!(store.value.borrow().as_deref(), Some("rotated-once"));
        assert_eq!(std::fs::read_to_string(&path).unwrap(), original);

        let material = reconcile_server_meta(
            &path,
            &store,
            false,
            &mut || Ok(9999),
            &mut || "rotated-twice".to_string(),
            &atomic,
        )
        .unwrap();
        assert_eq!(material.token, "rotated-twice");
        assert_eq!(store.value.borrow().as_deref(), Some("rotated-twice"));
        assert!(!std::fs::read_to_string(&path).unwrap().contains("token"));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn v2_metadata_without_keychain_fails_closed() {
        let dir = root("v2-missing-keychain");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("server.json");
        let original = r#"{"version":2,"port":4321}"#;
        std::fs::write(&path, original).unwrap();
        let store = MemoryStore::default();
        let error = reconcile_server_meta(
            &path,
            &store,
            false,
            &mut || Ok(9999),
            &mut || "must-not-generate".to_string(),
            &|_, _| panic!("v2 metadata must not be rewritten"),
        )
        .err()
        .unwrap();
        assert!(error.contains("system credential is missing"));
        assert_eq!(std::fs::read_to_string(&path).unwrap(), original);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn explicit_setup_repairs_a_missing_v2_credential_without_changing_metadata() {
        let dir = root("v2-setup-repair");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("server.json");
        let original = r#"{"version":2,"port":4321}"#;
        std::fs::write(&path, original).unwrap();
        let store = MemoryStore::default();
        let material = reconcile_server_meta_for_setup(
            &path,
            &store,
            &mut || panic!("an existing v2 port must be preserved"),
            &mut || "repaired-token".to_string(),
            &|_, _| panic!("v2 metadata must not be rewritten"),
        )
        .unwrap();
        assert_eq!(material.meta.port, 4321);
        assert_eq!(material.token, "repaired-token");
        assert_eq!(store.value.borrow().as_deref(), Some("repaired-token"));
        assert_eq!(std::fs::read_to_string(&path).unwrap(), original);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn v2_meta_is_exactly_version_and_port() {
        assert_eq!(
            parse_server_meta(r#"{"version":2,"port":4321}"#).unwrap(),
            ParsedServerMeta::V2(ServerMeta {
                version: SERVER_META_VERSION,
                port: 4321
            })
        );
        assert!(parse_server_meta(r#"{"version":2,"port":4321,"token":"leak"}"#).is_err());
        assert!(parse_server_meta(r#"{"version":3,"port":4321}"#).is_err());
    }

    #[test]
    fn exposed_v2_runtime_artifact_rotates_the_credential_without_rewriting_metadata() {
        let store = MemoryStore {
            value: RefCell::new(Some("exposed-token".to_string())),
            ..Default::default()
        };
        let meta = ServerMeta {
            version: SERVER_META_VERSION,
            port: 4321,
        };
        let material =
            rotate_v2_server_credential(meta, &store, &mut || "replacement-token".to_string())
                .unwrap();
        assert_eq!(material.meta, meta);
        assert_eq!(material.token, "replacement-token");
        assert_eq!(store.value.borrow().as_deref(), Some("replacement-token"));
        assert_eq!(*store.events.borrow(), ["keychain-set"]);
    }

    #[test]
    fn private_runtime_scrub_is_exact_and_rejects_directories() {
        let dir = root("runtime-scrub");
        std::fs::create_dir_all(&dir).unwrap();
        for name in [
            "jpserver-123.json",
            "jpserver-123-open.html",
            "jpserver-file-to-run-123-open.html",
        ] {
            std::fs::write(dir.join(name), "exposed-token").unwrap();
        }
        for name in [
            "jupyter_cookie_secret",
            "jpserver-not-a-pid.json",
            "jpserver-123.json.backup",
        ] {
            std::fs::write(dir.join(name), "keep").unwrap();
        }
        let paths = legacy_runtime_artifact_paths(&dir).unwrap();
        assert_eq!(paths.len(), 3);
        scrub_legacy_runtime_artifacts(&dir, &paths).unwrap();
        assert!(legacy_runtime_artifact_paths(&dir).unwrap().is_empty());
        assert_eq!(
            std::fs::read_to_string(dir.join("jupyter_cookie_secret")).unwrap(),
            "keep"
        );
        assert_eq!(
            std::fs::read_to_string(dir.join("jpserver-not-a-pid.json")).unwrap(),
            "keep"
        );

        let invalid = dir.join("jpserver-456.json");
        std::fs::create_dir(&invalid).unwrap();
        let paths = legacy_runtime_artifact_paths(&dir).unwrap();
        assert!(scrub_legacy_runtime_artifacts(&dir, &paths)
            .unwrap_err()
            .contains("not a file"));
        assert!(invalid.is_dir());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn private_runtime_scrub_unlinks_a_symlink_without_following_it() {
        use std::os::unix::fs::symlink;
        let dir = root("runtime-symlink");
        std::fs::create_dir_all(&dir).unwrap();
        let outside = dir.with_extension("outside");
        std::fs::write(&outside, "do-not-delete").unwrap();
        symlink(&outside, dir.join("jpserver-123.json")).unwrap();
        let paths = legacy_runtime_artifact_paths(&dir).unwrap();
        scrub_legacy_runtime_artifacts(&dir, &paths).unwrap();
        assert_eq!(std::fs::read_to_string(&outside).unwrap(), "do-not-delete");
        assert!(!dir.join("jpserver-123.json").exists());
        std::fs::remove_dir_all(dir).unwrap();
        std::fs::remove_file(outside).unwrap();
    }

    #[test]
    fn lab_token_is_only_initial_env_and_bootstrap_clears_it() {
        let token = "distinct-test-token";
        let private_dirs = JupyterPrivateDirs {
            home: "/private/tmp/jupyter-home".into(),
            config: "/private/tmp/jupyter-config".into(),
            runtime: "/private/tmp/jupyter-runtime".into(),
            data: "/private/tmp/jupyter-data".into(),
        };
        let plan = lab_launch_plan(
            4321,
            Path::new("/private/tmp/workspace"),
            &private_dirs,
            "/usr/bin:/bin:/usr/sbin:/sbin",
            token,
        )
        .unwrap();
        assert_eq!(
            plan.environment,
            vec![
                ("HOME", "/private/tmp/jupyter-home".to_string()),
                ("PATH", "/usr/bin:/bin:/usr/sbin:/sbin".to_string()),
                (
                    "JUPYTER_CONFIG_DIR",
                    "/private/tmp/jupyter-config".to_string()
                ),
                (
                    "JUPYTER_RUNTIME_DIR",
                    "/private/tmp/jupyter-runtime".to_string()
                ),
                ("JUPYTER_DATA_DIR", "/private/tmp/jupyter-data".to_string()),
                ("PYTHONNOUSERSITE", "1".to_string()),
                ("JUPYTER_TOKEN", token.to_string()),
            ]
        );
        assert_eq!(
            plan.environment
                .iter()
                .map(|(key, _)| *key)
                .collect::<Vec<_>>(),
            [
                "HOME",
                "PATH",
                "JUPYTER_CONFIG_DIR",
                "JUPYTER_RUNTIME_DIR",
                "JUPYTER_DATA_DIR",
                "PYTHONNOUSERSITE",
                "JUPYTER_TOKEN"
            ]
        );
        assert!(plan.args.iter().all(|argument| !argument.contains(token)));
        assert!(plan
            .args
            .iter()
            .all(|argument| !argument.contains("IdentityProvider.token=")));
        assert_eq!(&plan.args[..3], ["-I", "-S", "-c"]);
        assert!(plan
            .args
            .iter()
            .any(|argument| argument == "--ServerApp.port_retries=0"));
        assert!(plan
            .args
            .iter()
            .all(|argument| argument != "--ServerApp.write_server_info_file=False"));
        assert!(LAB_BOOTSTRAP.contains("class SparkServerApp(ServerApp):"));
        assert!(LAB_BOOTSTRAP.contains("def write_server_info_file(self):"));
        assert!(LAB_BOOTSTRAP.contains("def remove_server_info_file(self):"));
        assert!(LAB_BOOTSTRAP.contains("def write_browser_open_files(self):"));
        assert!(LAB_BOOTSTRAP.contains("def remove_browser_open_files(self):"));
        assert!(LAB_BOOTSTRAP.contains("LabApp.serverapp_class = SparkServerApp"));
        assert!(LAB_BOOTSTRAP.contains("LabApp.load_other_extensions = False"));
        let pop = LAB_BOOTSTRAP.find("os.environ.pop").unwrap();
        let site = LAB_BOOTSTRAP.find("site.main()").unwrap();
        assert!(!LAB_BOOTSTRAP.contains("launch_instance"));
        assert!(
            LAB_BOOTSTRAP.contains("LabApp.initialize_server(argv=sys.argv[1:], config=config)")
        );
        assert!(LAB_BOOTSTRAP.contains("serverapp.start()"));
        let launch = LAB_BOOTSTRAP.find("LabApp.initialize_server").unwrap();
        assert!(
            pop < site && site < launch,
            "the token must be removed before site hooks or Lab start"
        );
    }

    #[test]
    fn lsof_readiness_requires_exact_child_pid_and_loopback_listener() {
        let output = b"p4242\nf12\nn127.0.0.1:4321\n";
        assert!(lsof_output_proves_listener(output, 4242, 4321));
        assert!(!lsof_output_proves_listener(output, 4243, 4321));
        assert!(!lsof_output_proves_listener(output, 4242, 4322));
        assert!(!lsof_output_proves_listener(
            b"p4242\nf12\nn*:4321\n",
            4242,
            4321
        ));
        assert!(!lsof_output_proves_listener(
            b"p4242\np4243\nf12\nn127.0.0.1:4321\n",
            4242,
            4321
        ));
        assert!(!lsof_output_proves_listener(
            b"p4242\nf12\nn127.0.0.1:4321\xff",
            4242,
            4321
        ));
    }

    #[test]
    fn exact_legacy_mcp_is_removed_but_user_custom_entry_is_preserved() {
        let token = "legacy-token";
        let entry = serde_json::json!({
            "type": "local",
            "command": ["/managed/bin/jupyter-mcp-server"],
            "enabled": true,
            "environment": {
                "JUPYTER_URL": "http://127.0.0.1:4321",
                "JUPYTER_TOKEN": token,
                "ALLOW_IMG_OUTPUT": "true"
            }
        });
        assert!(exact_legacy_mcp_entry(
            &entry,
            "/managed/bin/jupyter-mcp-server",
            "http://127.0.0.1:4321"
        )
        .unwrap());
        let config = serde_json::json!({"mcp": {"jupyter": entry}, "model": "keep/me"});
        let output = plan_jupyter_mcp_reconcile(
            &config.to_string(),
            "/managed/bin/jupyter-mcp-server",
            "http://127.0.0.1:4321",
        )
        .unwrap()
        .unwrap();
        let output: serde_json::Value = serde_json::from_str(&output).unwrap();
        assert!(output["mcp"].get("jupyter").is_none());
        assert_eq!(output["model"], "keep/me");

        let custom = r#"{"mcp":{"jupyter":{"type":"local","command":["/user/custom"]}}}"#;
        let custom_result = plan_jupyter_mcp_reconcile(
            custom,
            "/managed/bin/jupyter-mcp-server",
            "http://127.0.0.1:4321",
        )
        .unwrap();
        assert_eq!(custom_result, None);
        assert!(!config_targets_managed_command("{}", "/managed/bin/jupyter-mcp-server").unwrap());

        let tampered_managed = r#"{"mcp":{"jupyter":{"type":"local","command":["/managed/bin/jupyter-mcp-server"],"enabled":false}}}"#;
        let error = plan_jupyter_mcp_reconcile(
            tampered_managed,
            "/managed/bin/jupyter-mcp-server",
            "http://127.0.0.1:4321",
        )
        .unwrap_err();
        assert!(error.contains("conflicting fields"));

        for conflicting in [
            r#"{"mcp":{"jupyter":{"type":"local","command":["/managed/bin/jupyter-mcp-server","--extra"],"enabled":true}}}"#,
            r#"{"mcp":{"jupyter":{"type":"local","command":"/managed/bin/jupyter-mcp-server","enabled":true}}}"#,
        ] {
            assert!(
                config_targets_managed_command(conflicting, "/managed/bin/jupyter-mcp-server")
                    .unwrap()
            );
            assert!(plan_jupyter_mcp_reconcile(
                conflicting,
                "/managed/bin/jupyter-mcp-server",
                "http://127.0.0.1:4321",
            )
            .unwrap_err()
            .contains("conflicting fields"));
        }
    }

    #[test]
    fn status_serialization_contains_no_secret_or_connection_material() {
        let value = serde_json::to_value(JupyterStatus {
            installed: true,
            running: true,
            registered: false,
        })
        .unwrap();
        assert_eq!(
            value,
            serde_json::json!({"installed": true, "running": true, "registered": false})
        );
        let text = value.to_string();
        assert!(!text.contains("token"));
        assert!(!text.contains("url"));
        assert!(!text.contains("command"));
    }

    #[test]
    fn every_managed_jupyter_package_is_exactly_pinned() {
        assert!(PIP_SPEC.iter().all(|spec| {
            let mut parts = spec.split("==");
            parts.next().is_some_and(|name| !name.is_empty())
                && parts.next().is_some_and(|version| !version.is_empty())
                && parts.next().is_none()
        }));
        assert!(PIP_SPEC
            .iter()
            .all(|spec| !spec.starts_with("jupyter-mcp-server==")));
        assert!(PIP_SPEC
            .iter()
            .all(|spec| !spec.starts_with("jupyter-collaboration==")));
        assert_eq!(
            RETIRED_JUPYTER_PACKAGES,
            ["jupyter-mcp-server", "jupyter-collaboration"]
        );
    }

    #[test]
    fn managed_environment_covers_autonomous_data_and_notebook_execution() {
        let package_names = PIP_SPEC
            .iter()
            .filter_map(|spec| spec.split_once("==").map(|(name, _)| name))
            .collect::<std::collections::BTreeSet<_>>();
        for required in [
            "jupyterlab",
            "nbformat",
            "nbconvert",
            "numpy",
            "pandas",
            "scipy",
            "matplotlib",
            "scikit-learn",
            "statsmodels",
        ] {
            assert!(
                package_names.contains(required),
                "managed research environment is missing {required}"
            );
        }
    }

    #[test]
    fn notebook_routes_are_normalized_and_encoded() {
        assert_eq!(
            notebook_route(Some("results/a b.ipynb")).unwrap(),
            "/tree/results/a%20b.ipynb"
        );
        assert!(notebook_route(Some("../secret.ipynb")).is_err());
        assert!(notebook_route(Some("/absolute.ipynb")).is_err());
        assert!(notebook_route(Some("nested\\windows.ipynb")).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn notebook_validation_rejects_symlink_escape() {
        use std::os::unix::fs::symlink;
        let dir = root("notebook-symlink");
        let workspace = dir.join("workspace");
        let outside = dir.join("outside.ipynb");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::write(&outside, "{}").unwrap();
        symlink(&outside, workspace.join("escape.ipynb")).unwrap();
        let workspace = workspace.canonicalize().unwrap();
        assert!(validated_notebook_route(&workspace, Some("escape.ipynb")).is_err());
        std::fs::write(workspace.join("safe.ipynb"), "{}").unwrap();
        assert_eq!(
            validated_notebook_route(&workspace, Some("safe.ipynb")).unwrap(),
            "/tree/safe.ipynb"
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn orphan_matcher_excludes_ordinary_notebook_kernels() {
        let python = Path::new("/managed/jupyter-env/bin/python");
        let legacy_lab = Path::new("/managed/jupyter-env/bin/jupyter-lab");
        assert!(!managed_lab_command_matches(
            "/managed/jupyter-env/bin/python -m ipykernel_launcher -f kernel.json",
            python
        ));
        assert!(managed_lab_command_matches(
            &format!("/managed/jupyter-env/bin/python -c code containing {LAB_PROCESS_MARKER}"),
            python
        ));
        assert!(legacy_lab_command_matches(
            "/managed/jupyter-env/bin/python /managed/jupyter-env/bin/jupyter-lab --IdentityProvider.token=old --ServerApp.root_dir=/workspace",
            legacy_lab
        ));
        assert!(!legacy_lab_command_matches(
            "/managed/jupyter-env/bin/python -m ipykernel_launcher",
            legacy_lab
        ));
    }

    #[cfg(unix)]
    #[test]
    fn server_metadata_leaf_symlink_is_rejected() {
        use std::os::unix::fs::symlink;
        let dir = root("meta-symlink");
        std::fs::create_dir_all(&dir).unwrap();
        let outside = dir.join("outside.json");
        let server = dir.join("server.json");
        std::fs::write(&outside, r#"{"version":2,"port":4321}"#).unwrap();
        symlink(&outside, &server).unwrap();
        assert!(read_optional(&server)
            .unwrap_err()
            .contains("not a regular file"));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn managed_environment_rejects_leaf_symlinks() {
        use std::os::unix::fs::symlink;
        let dir = root("env-symlink");
        let runtime = dir.join("runtime");
        let outside = dir.join("outside");
        std::fs::create_dir_all(&runtime).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        symlink(&outside, runtime.join("jupyter-env")).unwrap();
        assert!(managed_env_dir_from_runtime(&runtime).is_err());
        std::fs::remove_dir_all(dir).unwrap();
    }
}
