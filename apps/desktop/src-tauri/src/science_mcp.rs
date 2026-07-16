// Curated open-source science MCP connectors. Exact packages and every
// credential-bearing execution decision stay native. Keyed connectors are
// reached through a main-process Unix-domain-socket broker; OpenCode only
// starts Apple's fixed /usr/bin/nc relay and never receives a connector key.
use serde_json::Value;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Manager};

pub(crate) const CONNECTOR_OWNER_ENV: &str = "SPARK_SCIENCE_CONNECTOR_OWNER";
pub(crate) const CONNECTOR_OWNER_V1: &str = "spark-agent/v1";

const CONNECTOR_SERVICE: &str = "io.github.shawliu998.sparkagent.opencode-connector";
const NC_PATH: &str = "/usr/bin/nc";
const NC_UNIX_FLAG: &str = "-U";

// The broker and migration substrate stay testable while credential-bearing
// execution remains fail-closed. Independent review found that same-UID
// OpenCode extensions can still mutate downloaded Python environments and can
// exercise an authorized relay outside the per-tool approval path. Do not turn
// this on until target immutability, native per-call approval, a fully hashed
// transitive lock, and packaged macOS E2E are all enforced.
const MANAGED_CONNECTOR_EXECUTION_ENABLED: bool = false;

pub(crate) fn managed_connector_execution_enabled() -> bool {
    MANAGED_CONNECTOR_EXECUTION_ENABLED
}

fn managed_connector_security_gate_error() -> String {
    "Spark credential-bearing science connectors are security-gated until native per-call approval and immutable, fully locked connector targets are enforced"
        .to_string()
}

pub(crate) fn ensure_managed_connector_execution_enabled() -> Result<(), String> {
    if managed_connector_execution_enabled() {
        Ok(())
    } else {
        Err(managed_connector_security_gate_error())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ConnectorEnvironment {
    Shared,
    Managed,
}

#[derive(Clone, Copy, Debug)]
struct ScienceConnectorSpec {
    connector_id: &'static str,
    package: &'static str,
    environment: ConnectorEnvironment,
    managed_script: Option<&'static str>,
    api_key_env: Option<&'static str>,
    socket_name: Option<&'static str>,
}

const SCIENCE_CONNECTORS: &[ScienceConnectorSpec] = &[
    ScienceConnectorSpec {
        connector_id: "paper-search",
        package: "paper-search-mcp==0.1.4",
        environment: ConnectorEnvironment::Shared,
        managed_script: None,
        api_key_env: None,
        socket_name: None,
    },
    ScienceConnectorSpec {
        connector_id: "biomcp",
        package: "biomcp-python==0.7.3",
        environment: ConnectorEnvironment::Shared,
        managed_script: None,
        api_key_env: None,
        socket_name: None,
    },
    ScienceConnectorSpec {
        connector_id: "materials-project",
        package: "mcp-materials-project==0.3.3",
        environment: ConnectorEnvironment::Managed,
        managed_script: Some("mcp-materials-project"),
        api_key_env: Some("MP_API_KEY"),
        socket_name: Some("mp.sock"),
    },
    ScienceConnectorSpec {
        connector_id: "fred",
        package: "fred-mcp==1.0.1",
        environment: ConnectorEnvironment::Managed,
        managed_script: Some("fred-mcp"),
        api_key_env: Some("FRED_API_KEY"),
        socket_name: Some("fred.sock"),
    },
    ScienceConnectorSpec {
        connector_id: "spaceweather",
        package: "spaceweather-mcp==0.1.0",
        environment: ConnectorEnvironment::Shared,
        managed_script: None,
        api_key_env: None,
        socket_name: None,
    },
    ScienceConnectorSpec {
        connector_id: "open-meteo",
        package: "mcp-weather-server==0.6.1",
        environment: ConnectorEnvironment::Shared,
        managed_script: None,
        api_key_env: None,
        socket_name: None,
    },
    ScienceConnectorSpec {
        connector_id: "usgs-water",
        package: "usgs-mcp==0.1.0",
        environment: ConnectorEnvironment::Shared,
        managed_script: None,
        api_key_env: None,
        socket_name: None,
    },
];

fn connector_spec(connector_id: &str) -> Result<ScienceConnectorSpec, String> {
    SCIENCE_CONNECTORS
        .iter()
        .copied()
        .find(|spec| spec.connector_id == connector_id)
        .ok_or_else(|| format!("science connector {connector_id:?} is not allowlisted"))
}

fn keyed_connector_spec(connector_id: &str) -> Result<ScienceConnectorSpec, String> {
    let spec = connector_spec(connector_id)?;
    if spec.environment != ConnectorEnvironment::Managed
        || spec.managed_script.is_none()
        || spec.api_key_env.is_none()
        || spec.socket_name.is_none()
    {
        return Err(format!(
            "science connector {connector_id:?} does not receive a managed credential"
        ));
    }
    Ok(spec)
}

fn keyed_connector_specs() -> impl Iterator<Item = ScienceConnectorSpec> {
    SCIENCE_CONNECTORS
        .iter()
        .copied()
        .filter(|spec| spec.environment == ConnectorEnvironment::Managed)
}

fn shared_env_dir(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?
        .join("runtime")
        .join("science-mcp-env"))
}

fn managed_env_dir_from_app_data(
    app_data_dir: &Path,
    connector_id: &str,
) -> Result<PathBuf, String> {
    let spec = keyed_connector_spec(connector_id)?;
    if !app_data_dir.is_absolute() {
        return Err("Spark app-data path is not absolute".to_string());
    }
    Ok(app_data_dir
        .join("runtime")
        .join("science-mcp-managed")
        .join(spec.connector_id))
}

fn managed_env_dir(app: &AppHandle, connector_id: &str) -> Result<PathBuf, String> {
    managed_env_dir_from_app_data(
        &app.path()
            .app_data_dir()
            .map_err(|error| error.to_string())?,
        connector_id,
    )
}

fn connector_env_dir(app: &AppHandle, spec: ScienceConnectorSpec) -> Result<PathBuf, String> {
    match spec.environment {
        ConnectorEnvironment::Shared => shared_env_dir(app),
        ConnectorEnvironment::Managed => managed_env_dir(app, spec.connector_id),
    }
}

fn python_bin_in(dir: &Path) -> PathBuf {
    #[cfg(windows)]
    return dir.join("Scripts").join("python.exe");
    #[cfg(not(windows))]
    dir.join("bin").join("python")
}

fn python_bin(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(python_bin_in(&shared_env_dir(app)?))
}

fn relay_command(socket_path: &Path, connector_id: &str) -> Result<Vec<String>, String> {
    keyed_connector_spec(connector_id)?;
    if !socket_path.is_absolute() {
        return Err("Spark connector relay socket path is not absolute".to_string());
    }
    Ok(vec![
        NC_PATH.to_string(),
        NC_UNIX_FLAG.to_string(),
        socket_path.to_string_lossy().to_string(),
    ])
}

/// Exact secret-free command stored in OpenCode config for a keyed connector.
/// Starting the broker is idempotent and creates a process-random private
/// socket path that credential migration canonicalizes on every app launch.
pub(crate) fn managed_connector_command(
    app: &AppHandle,
    connector_id: &str,
) -> Result<Vec<String>, String> {
    ensure_connector_broker(app)?;
    #[cfg(target_os = "macos")]
    {
        let broker = current_broker()?;
        let socket = broker.socket_path(connector_id)?;
        relay_command(&socket, connector_id)
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = connector_id;
        Err("Spark managed connector brokerage is supported only on macOS".to_string())
    }
}

fn is_private_relay_socket_path(path: &Path, connector_id: &str) -> bool {
    let Ok(spec) = keyed_connector_spec(connector_id) else {
        return false;
    };
    if path.file_name().and_then(|name| name.to_str()) != spec.socket_name {
        return false;
    }
    let Some(directory) = path.parent() else {
        return false;
    };
    if directory.parent() != Some(Path::new("/private/tmp")) {
        return false;
    }
    let Some(name) = directory.file_name().and_then(|name| name.to_str()) else {
        return false;
    };
    let Some(random) = name.strip_prefix("spark-mcp-") else {
        return false;
    };
    random.len() == 16
        && random
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
}

/// Recognize the exact relay grammar emitted by Spark. The current socket is
/// accepted directly; a syntactically exact prior-process socket is accepted
/// only as migration input and is rewritten before OpenCode starts. Broker
/// authorization itself always requires the current live socket and config.
pub(crate) fn is_managed_connector_relay_command(command: &[String], connector_id: &str) -> bool {
    command.len() == 3
        && command[0] == NC_PATH
        && command[1] == NC_UNIX_FLAG
        && is_private_relay_socket_path(Path::new(&command[2]), connector_id)
}

/// Direct dedicated-environment command used only by the broker. Credential
/// migration also recognizes it as the immediately previous Spark format.
pub(crate) fn managed_connector_target_command(
    app: &AppHandle,
    connector_id: &str,
) -> Result<Vec<String>, String> {
    managed_connector_target_command_from_app_data(
        &app.path()
            .app_data_dir()
            .map_err(|error| error.to_string())?,
        connector_id,
    )
}

fn managed_connector_target_command_from_app_data(
    app_data_dir: &Path,
    connector_id: &str,
) -> Result<Vec<String>, String> {
    let spec = keyed_connector_spec(connector_id)?;
    let script = spec
        .managed_script
        .ok_or_else(|| format!("connector {connector_id:?} has no managed script"))?;
    let dir = managed_env_dir_from_app_data(app_data_dir, connector_id)?;
    #[cfg(windows)]
    let executable = dir.join("Scripts").join(format!("{script}.exe"));
    #[cfg(not(windows))]
    let executable = dir.join("bin").join(script);
    Ok(vec![executable.to_string_lossy().to_string()])
}

/// Retired shared-environment command emitted by older Spark versions. It is
/// migration input only and must never receive a managed credential.
pub(crate) fn legacy_managed_connector_command(
    app: &AppHandle,
    connector_id: &str,
) -> Result<Vec<String>, String> {
    let spec = keyed_connector_spec(connector_id)?;
    let script = spec
        .managed_script
        .ok_or_else(|| format!("connector {connector_id:?} has no managed script"))?;
    let dir = shared_env_dir(app)?;
    #[cfg(windows)]
    let executable = dir.join("Scripts").join(format!("{script}.exe"));
    #[cfg(not(windows))]
    let executable = dir.join("bin").join(script);
    Ok(vec![executable.to_string_lossy().to_string()])
}

fn command_matches(value: Option<&Value>, expected: &[String]) -> bool {
    value.and_then(Value::as_array).is_some_and(|actual| {
        actual.len() == expected.len()
            && actual
                .iter()
                .zip(expected)
                .all(|(actual, expected)| actual.as_str() == Some(expected.as_str()))
    })
}

fn validate_connector_relay_config(
    config_text: &str,
    connector_id: &str,
    expected_command: &[String],
) -> Result<(), String> {
    let spec = keyed_connector_spec(connector_id)?;
    let config = crate::opencode_config::parse_config(config_text, "OpenCode config")?;
    let mcp = config
        .get("mcp")
        .and_then(Value::as_object)
        .ok_or_else(|| "OpenCode config is missing its MCP object".to_string())?;
    let connector = mcp
        .get(spec.connector_id)
        .and_then(Value::as_object)
        .ok_or_else(|| {
            format!(
                "connector {:?} is not enabled by the effective OpenCode config",
                spec.connector_id
            )
        })?;

    let exact_top_level_shape = connector.len() == 4
        && ["type", "command", "enabled", "environment"]
            .iter()
            .all(|key| connector.contains_key(*key));
    let exact_environment_shape = connector
        .get("environment")
        .and_then(Value::as_object)
        .is_some_and(|environment| {
            environment.len() == 1
                && environment.get(CONNECTOR_OWNER_ENV).and_then(Value::as_str)
                    == Some(CONNECTOR_OWNER_V1)
        });
    if !exact_top_level_shape
        || connector.get("type").and_then(Value::as_str) != Some("local")
        || connector.get("enabled").and_then(Value::as_bool) != Some(true)
        || !command_matches(connector.get("command"), expected_command)
        || !exact_environment_shape
    {
        return Err(format!(
            "connector {:?} does not have the strict Spark relay config",
            spec.connector_id
        ));
    }

    if mcp.iter().any(|(other_id, other)| {
        other_id != spec.connector_id && command_matches(other.get("command"), expected_command)
    }) {
        return Err(format!(
            "connector {:?} relay command is also assigned to another MCP id",
            spec.connector_id
        ));
    }
    Ok(())
}

fn effective_opencode_config(app_data_dir: &Path) -> Result<PathBuf, String> {
    if !app_data_dir.is_absolute() {
        return Err("Spark app-data path is not absolute".to_string());
    }
    let config_dir = app_data_dir
        .join("runtime")
        .join("xdg-config")
        .join("opencode");
    let jsonc = config_dir.join("opencode.jsonc");
    if jsonc.is_file() {
        return Ok(jsonc);
    }
    let json = config_dir.join("opencode.json");
    if json.is_file() {
        return Ok(json);
    }
    Err("effective OpenCode config is unavailable".to_string())
}

fn read_verified_effective_config(app_data_dir: &Path) -> Result<String, String> {
    let path = effective_opencode_config(app_data_dir)?;
    let metadata = std::fs::symlink_metadata(&path)
        .map_err(|_| "effective OpenCode config is unavailable".to_string())?;
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err("effective OpenCode config is not a regular private file".to_string());
    }
    let root = app_data_dir
        .canonicalize()
        .map_err(|_| "Spark app-data directory is unavailable".to_string())?;
    let canonical = path
        .canonicalize()
        .map_err(|_| "effective OpenCode config is unavailable".to_string())?;
    if !canonical.starts_with(root) {
        return Err("effective OpenCode config escapes app-private storage".to_string());
    }
    std::fs::read_to_string(canonical)
        .map_err(|_| "effective OpenCode config could not be read".to_string())
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct ConnectorChildTarget {
    connector_id: &'static str,
    executable: PathBuf,
    working_directory: PathBuf,
    api_key_env: &'static str,
}

fn connector_child_target(
    app_data_dir: &Path,
    connector_id: &str,
) -> Result<ConnectorChildTarget, String> {
    let spec = keyed_connector_spec(connector_id)?;
    let command = managed_connector_target_command_from_app_data(app_data_dir, connector_id)?;
    Ok(ConnectorChildTarget {
        connector_id: spec.connector_id,
        executable: PathBuf::from(&command[0]),
        working_directory: managed_env_dir_from_app_data(app_data_dir, connector_id)?,
        api_key_env: spec
            .api_key_env
            .ok_or_else(|| format!("connector {connector_id:?} has no managed API-key field"))?,
    })
}

#[cfg(target_os = "macos")]
fn canonical_connector_child_target(
    app_data_dir: &Path,
    connector_id: &str,
) -> Result<ConnectorChildTarget, String> {
    use std::os::unix::fs::PermissionsExt;

    let mut target = connector_child_target(app_data_dir, connector_id)?;
    let app_data = app_data_dir
        .canonicalize()
        .map_err(|_| "Spark app-data directory is unavailable".to_string())?;
    let managed_metadata = std::fs::symlink_metadata(&target.working_directory)
        .map_err(|_| format!("connector {:?} is not installed", target.connector_id))?;
    if !managed_metadata.is_dir() || managed_metadata.file_type().is_symlink() {
        return Err(format!(
            "connector {:?} managed environment is invalid",
            target.connector_id
        ));
    }
    let managed = target
        .working_directory
        .canonicalize()
        .map_err(|_| format!("connector {:?} is not installed", target.connector_id))?;
    if !managed.starts_with(&app_data) {
        return Err("Spark connector environment escapes app-private storage".to_string());
    }

    let executable_metadata = std::fs::symlink_metadata(&target.executable)
        .map_err(|_| format!("connector {:?} is not installed", target.connector_id))?;
    if !executable_metadata.is_file()
        || executable_metadata.file_type().is_symlink()
        || executable_metadata.permissions().mode() & 0o111 == 0
    {
        return Err(format!(
            "connector {:?} managed executable is invalid",
            target.connector_id
        ));
    }
    let executable = target
        .executable
        .canonicalize()
        .map_err(|_| format!("connector {:?} is not installed", target.connector_id))?;
    if !executable.starts_with(&managed) {
        return Err("Spark connector executable escapes its managed environment".to_string());
    }
    target.executable = executable;
    target.working_directory = managed;
    Ok(target)
}

#[cfg(target_os = "macos")]
mod broker {
    use super::{
        canonical_connector_child_target, keyed_connector_spec, keyed_connector_specs,
        read_verified_effective_config, relay_command, validate_connector_relay_config,
        ConnectorChildTarget, CONNECTOR_SERVICE, NC_PATH,
    };
    use std::collections::BTreeMap;
    use std::ffi::{c_void, OsString};
    use std::fs::{DirBuilder, Permissions};
    use std::mem::MaybeUninit;
    use std::os::fd::{AsRawFd, OwnedFd};
    use std::os::unix::ffi::OsStringExt;
    use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
    use std::os::unix::net::{UnixListener, UnixStream};
    use std::path::{Path, PathBuf};
    use std::process::{Child, Command, Stdio};
    use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
    use std::sync::{Arc, Mutex, OnceLock};
    use std::thread::{self, JoinHandle};
    use std::time::{Duration, Instant};
    use tauri::{AppHandle, Manager};

    const BROKER_ROOT: &str = "/private/tmp";
    const BROKER_PREFIX: &str = "spark-mcp-";
    const MAX_ACTIVE_CONNECTIONS: usize = 4;
    const AUTHORIZATION_RACE_WINDOW: Duration = Duration::from_millis(1500);
    const LISTENER_POLL_INTERVAL: Duration = Duration::from_millis(20);
    const CHILD_POLL_INTERVAL: Duration = Duration::from_millis(25);
    const CONFIG_RECHECK_INTERVAL: Duration = Duration::from_millis(250);
    const SHUTDOWN_DRAIN_TIMEOUT: Duration = Duration::from_secs(2);

    const SOL_LOCAL: i32 = 0;
    const LOCAL_PEERPID: i32 = 0x002;
    const PROC_PIDTBSDINFO: i32 = 3;
    const POLLHUP: i16 = 0x0010;
    const POLLERR: i16 = 0x0008;
    const POLLNVAL: i16 = 0x0020;

    #[repr(C)]
    struct ProcBsdInfo {
        pbi_flags: u32,
        pbi_status: u32,
        pbi_xstatus: u32,
        pbi_pid: u32,
        pbi_ppid: u32,
        pbi_uid: u32,
        pbi_gid: u32,
        pbi_ruid: u32,
        pbi_rgid: u32,
        pbi_svuid: u32,
        pbi_svgid: u32,
        rfu_1: u32,
        pbi_comm: [u8; 16],
        pbi_name: [u8; 32],
        pbi_nfiles: u32,
        pbi_pgid: u32,
        pbi_pjobc: u32,
        e_tdev: u32,
        e_tpgid: u32,
        pbi_nice: i32,
        pbi_start_tvsec: u64,
        pbi_start_tvusec: u64,
    }

    #[repr(C)]
    struct PollFd {
        fd: i32,
        events: i16,
        revents: i16,
    }

    #[link(name = "proc")]
    extern "C" {
        fn proc_pidinfo(
            pid: i32,
            flavor: i32,
            arg: u64,
            buffer: *mut c_void,
            buffer_size: i32,
        ) -> i32;
        fn proc_pidpath(pid: i32, buffer: *mut c_void, buffer_size: u32) -> i32;
    }

    extern "C" {
        fn getpeereid(socket: i32, effective_uid: *mut u32, effective_gid: *mut u32) -> i32;
        fn geteuid() -> u32;
        fn getsockopt(
            socket: i32,
            level: i32,
            option_name: i32,
            option_value: *mut c_void,
            option_len: *mut u32,
        ) -> i32;
        fn poll(file_descriptors: *mut PollFd, count: u32, timeout_ms: i32) -> i32;
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    struct ProcessIdentity {
        pid: u32,
        start_seconds: u64,
        start_microseconds: u64,
    }

    #[derive(Clone, Copy, Debug)]
    struct Authorization {
        identity: ProcessIdentity,
        generation: u64,
    }

    struct BrokerShared {
        app_data_dir: PathBuf,
        socket_paths: BTreeMap<&'static str, PathBuf>,
        authorization: Mutex<Option<Authorization>>,
        generation: AtomicU64,
        active_connections: AtomicUsize,
        shutting_down: AtomicBool,
    }

    pub(super) struct ConnectorBroker {
        root: PathBuf,
        shared: Arc<BrokerShared>,
        listeners: Mutex<Vec<JoinHandle<()>>>,
    }

    fn broker_slot() -> &'static Mutex<Option<Arc<ConnectorBroker>>> {
        static BROKER: OnceLock<Mutex<Option<Arc<ConnectorBroker>>>> = OnceLock::new();
        BROKER.get_or_init(|| Mutex::new(None))
    }

    pub(super) fn current() -> Result<Arc<ConnectorBroker>, String> {
        broker_slot()
            .lock()
            .map_err(|_| "Spark connector broker state is unavailable".to_string())?
            .clone()
            .ok_or_else(|| "Spark connector broker is not running".to_string())
    }

    fn create_private_root() -> Result<PathBuf, String> {
        for _ in 0..16 {
            let mut random = [0_u8; 8];
            getrandom::fill(&mut random)
                .map_err(|_| "could not create a private connector broker path".to_string())?;
            let suffix = random
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect::<String>();
            let path = Path::new(BROKER_ROOT).join(format!("{BROKER_PREFIX}{suffix}"));
            let mut builder = DirBuilder::new();
            builder.mode(0o700);
            match builder.create(&path) {
                Ok(()) => {
                    std::fs::set_permissions(&path, Permissions::from_mode(0o700)).map_err(
                        |_| "could not protect the connector broker directory".to_string(),
                    )?;
                    let metadata = std::fs::symlink_metadata(&path).map_err(|_| {
                        "could not verify the connector broker directory".to_string()
                    })?;
                    if !metadata.is_dir()
                        || metadata.file_type().is_symlink()
                        || metadata.permissions().mode() & 0o777 != 0o700
                    {
                        let _ = std::fs::remove_dir(&path);
                        return Err(
                            "connector broker directory is not a private regular directory"
                                .to_string(),
                        );
                    }
                    return Ok(path);
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(_) => return Err("could not create the connector broker directory".to_string()),
            }
        }
        Err("could not allocate a unique connector broker directory".to_string())
    }

    fn cleanup_paths(root: &Path, sockets: impl Iterator<Item = PathBuf>) {
        for socket in sockets {
            let _ = std::fs::remove_file(socket);
        }
        let _ = std::fs::remove_dir(root);
    }

    impl ConnectorBroker {
        fn start(app: &AppHandle) -> Result<Arc<Self>, String> {
            let app_data_dir = app
                .path()
                .app_data_dir()
                .map_err(|error| error.to_string())?;
            let root = create_private_root()?;
            let mut socket_paths = BTreeMap::new();
            let mut bound = Vec::new();

            for spec in keyed_connector_specs() {
                let socket_name = spec
                    .socket_name
                    .ok_or_else(|| "managed connector has no broker socket name".to_string())?;
                let path = root.join(socket_name);
                let listener = match UnixListener::bind(&path) {
                    Ok(listener) => listener,
                    Err(_) => {
                        cleanup_paths(
                            &root,
                            socket_paths
                                .values()
                                .cloned()
                                .collect::<Vec<_>>()
                                .into_iter(),
                        );
                        return Err("could not bind a private connector broker socket".to_string());
                    }
                };
                std::fs::set_permissions(&path, Permissions::from_mode(0o600)).map_err(|_| {
                    cleanup_paths(
                        &root,
                        socket_paths
                            .values()
                            .cloned()
                            .collect::<Vec<_>>()
                            .into_iter(),
                    );
                    "could not protect a connector broker socket".to_string()
                })?;
                listener
                    .set_nonblocking(true)
                    .map_err(|_| "could not configure a connector broker socket".to_string())?;
                socket_paths.insert(spec.connector_id, path);
                bound.push((spec.connector_id, listener));
            }

            let shared = Arc::new(BrokerShared {
                app_data_dir,
                socket_paths,
                authorization: Mutex::new(None),
                generation: AtomicU64::new(1),
                active_connections: AtomicUsize::new(0),
                shutting_down: AtomicBool::new(false),
            });
            let broker = Arc::new(Self {
                root,
                shared: shared.clone(),
                listeners: Mutex::new(Vec::new()),
            });

            let mut handles = Vec::new();
            for (connector_id, listener) in bound {
                let shared = shared.clone();
                handles.push(thread::spawn(move || {
                    listener_loop(shared, connector_id, listener);
                }));
            }
            *broker
                .listeners
                .lock()
                .map_err(|_| "connector broker listener state is unavailable".to_string())? =
                handles;
            Ok(broker)
        }

        pub(super) fn socket_path(&self, connector_id: &str) -> Result<PathBuf, String> {
            keyed_connector_spec(connector_id)?;
            self.shared
                .socket_paths
                .get(connector_id)
                .cloned()
                .ok_or_else(|| format!("connector {connector_id:?} has no broker socket"))
        }

        fn shutdown(&self) {
            self.shared.shutting_down.store(true, Ordering::Release);
            self.shared.generation.fetch_add(1, Ordering::AcqRel);
            if let Ok(mut authorization) = self.shared.authorization.lock() {
                *authorization = None;
            }
            if let Ok(mut listeners) = self.listeners.lock() {
                for handle in listeners.drain(..) {
                    let _ = handle.join();
                }
            }
            let deadline = Instant::now() + SHUTDOWN_DRAIN_TIMEOUT;
            while self.shared.active_connections.load(Ordering::Acquire) != 0
                && Instant::now() < deadline
            {
                thread::sleep(CHILD_POLL_INTERVAL);
            }
            cleanup_paths(&self.root, self.shared.socket_paths.values().cloned());
        }
    }

    pub(super) fn ensure(app: &AppHandle) -> Result<(), String> {
        let mut slot = broker_slot()
            .lock()
            .map_err(|_| "Spark connector broker state is unavailable".to_string())?;
        if slot.is_none() {
            *slot = Some(ConnectorBroker::start(app)?);
        }
        Ok(())
    }

    fn process_info(pid: u32) -> Result<ProcBsdInfo, String> {
        if pid == 0 || pid > i32::MAX as u32 {
            return Err("invalid process identity".to_string());
        }
        let mut info = MaybeUninit::<ProcBsdInfo>::zeroed();
        // SAFETY: proc_pidinfo writes at most the exact size supplied into a
        // properly aligned uninitialized ProcBsdInfo buffer.
        let read = unsafe {
            proc_pidinfo(
                pid as i32,
                PROC_PIDTBSDINFO,
                0,
                info.as_mut_ptr().cast::<c_void>(),
                std::mem::size_of::<ProcBsdInfo>() as i32,
            )
        };
        if read != std::mem::size_of::<ProcBsdInfo>() as i32 {
            return Err("process identity is unavailable".to_string());
        }
        // SAFETY: the exact structure size was initialized by proc_pidinfo.
        Ok(unsafe { info.assume_init() })
    }

    fn process_identity(pid: u32) -> Result<ProcessIdentity, String> {
        let info = process_info(pid)?;
        if info.pbi_pid != pid {
            return Err("process identity changed during verification".to_string());
        }
        Ok(ProcessIdentity {
            pid,
            start_seconds: info.pbi_start_tvsec,
            start_microseconds: info.pbi_start_tvusec,
        })
    }

    fn process_path(pid: u32) -> Result<PathBuf, String> {
        if pid == 0 || pid > i32::MAX as u32 {
            return Err("invalid process identity".to_string());
        }
        let mut buffer = vec![0_u8; 4096];
        // SAFETY: the buffer is writable for the complete reported capacity.
        let length = unsafe {
            proc_pidpath(
                pid as i32,
                buffer.as_mut_ptr().cast::<c_void>(),
                buffer.len() as u32,
            )
        };
        if length <= 0 {
            return Err("process executable is unavailable".to_string());
        }
        buffer.truncate(length as usize);
        while buffer.last() == Some(&0) {
            buffer.pop();
        }
        Ok(PathBuf::from(OsString::from_vec(buffer)))
    }

    fn peer_pid(stream: &UnixStream) -> Result<u32, String> {
        let socket = stream.as_raw_fd();
        let mut peer_uid = 0_u32;
        let mut peer_gid = 0_u32;
        // SAFETY: both output pointers are valid for one uid_t/gid_t value.
        if unsafe { getpeereid(socket, &mut peer_uid, &mut peer_gid) } != 0 {
            return Err("connector relay peer credentials are unavailable".to_string());
        }
        // SAFETY: geteuid has no arguments or retained pointers.
        if peer_uid != unsafe { geteuid() } {
            return Err("connector relay belongs to another operating-system user".to_string());
        }

        let mut pid = 0_i32;
        let mut length = std::mem::size_of::<i32>() as u32;
        // SAFETY: pid and length are writable and correctly describe the buffer.
        if unsafe {
            getsockopt(
                socket,
                SOL_LOCAL,
                LOCAL_PEERPID,
                (&mut pid as *mut i32).cast::<c_void>(),
                &mut length,
            )
        } != 0
            || length as usize != std::mem::size_of::<i32>()
            || pid <= 0
        {
            return Err("connector relay peer process is unavailable".to_string());
        }
        Ok(pid as u32)
    }

    fn authenticate_relay_peer(stream: &UnixStream) -> Result<ProcessIdentity, String> {
        let pid = peer_pid(stream)?;
        let actual = process_path(pid)?
            .canonicalize()
            .map_err(|_| "connector relay executable is unavailable".to_string())?;
        let expected = Path::new(NC_PATH)
            .canonicalize()
            .map_err(|_| "Apple nc executable is unavailable".to_string())?;
        if actual != expected {
            return Err("connector relay is not Apple /usr/bin/nc".to_string());
        }
        let peer = process_info(pid)?;
        if peer.pbi_pid != pid || peer.pbi_ppid == 0 {
            return Err("connector relay parent identity is unavailable".to_string());
        }
        process_identity(peer.pbi_ppid)
    }

    pub(super) fn authorize(pid: u32) -> Result<(), String> {
        let broker = current()?;
        let deadline = Instant::now() + AUTHORIZATION_RACE_WINDOW;
        let identity = loop {
            match process_identity(pid) {
                Ok(identity) => break identity,
                Err(error) if Instant::now() < deadline => {
                    let _ = error;
                    thread::sleep(LISTENER_POLL_INTERVAL);
                }
                Err(_) => return Err("OpenCode sidecar identity is unavailable".to_string()),
            }
        };
        let mut authorization = broker
            .shared
            .authorization
            .lock()
            .map_err(|_| "connector broker authorization state is unavailable".to_string())?;
        if authorization
            .as_ref()
            .is_some_and(|current| current.identity == identity)
        {
            return Ok(());
        }
        let generation = broker.shared.generation.fetch_add(1, Ordering::AcqRel) + 1;
        *authorization = Some(Authorization {
            identity,
            generation,
        });
        Ok(())
    }

    pub(super) fn revoke(pid: Option<u32>) {
        let Ok(broker) = current() else {
            return;
        };
        let Ok(mut authorization) = broker.shared.authorization.lock() else {
            broker.shared.generation.fetch_add(1, Ordering::AcqRel);
            return;
        };
        let should_revoke = match (pid, authorization.as_ref()) {
            (None, _) => true,
            (Some(pid), Some(current)) => current.identity.pid == pid,
            (Some(_), None) => false,
        };
        if should_revoke {
            *authorization = None;
            broker.shared.generation.fetch_add(1, Ordering::AcqRel);
        }
    }

    pub(super) fn shutdown() {
        let broker = broker_slot().lock().ok().and_then(|mut slot| slot.take());
        if let Some(broker) = broker {
            broker.shutdown();
        }
    }

    fn wait_for_authorization(
        shared: &BrokerShared,
        parent: ProcessIdentity,
    ) -> Result<u64, String> {
        let deadline = Instant::now() + AUTHORIZATION_RACE_WINDOW;
        loop {
            if shared.shutting_down.load(Ordering::Acquire) {
                return Err("connector broker is shutting down".to_string());
            }
            let authorization = shared
                .authorization
                .lock()
                .map_err(|_| "connector broker authorization state is unavailable".to_string())?;
            if let Some(authorization) = *authorization {
                if authorization.identity == parent {
                    return Ok(authorization.generation);
                }
                return Err(
                    "connector relay parent is not the authorized OpenCode sidecar".to_string(),
                );
            }
            drop(authorization);
            if Instant::now() >= deadline {
                return Err("OpenCode sidecar was not authorized in time".to_string());
            }
            thread::sleep(LISTENER_POLL_INTERVAL);
        }
    }

    fn validate_live_config(shared: &BrokerShared, connector_id: &str) -> Result<(), String> {
        let socket = shared
            .socket_paths
            .get(connector_id)
            .ok_or_else(|| format!("connector {connector_id:?} has no broker socket"))?;
        let expected = relay_command(socket, connector_id)?;
        let text = read_verified_effective_config(&shared.app_data_dir)?;
        validate_connector_relay_config(&text, connector_id, &expected)
    }

    fn read_connector_secret(connector_id: &str) -> Result<String, String> {
        super::ensure_managed_connector_execution_enabled()?;
        keyed_connector_spec(connector_id)?;
        let entry = keyring::Entry::new(CONNECTOR_SERVICE, connector_id)
            .map_err(|_| format!("connector {connector_id:?} credential is unavailable"))?;
        match entry.get_password() {
            Ok(secret) if !secret.trim().is_empty() => Ok(secret),
            Ok(_) | Err(keyring::Error::NoEntry) => Err(format!(
                "connector {connector_id:?} has no system credential"
            )),
            Err(_) => Err(format!(
                "connector {connector_id:?} credential could not be read"
            )),
        }
    }

    fn spawn_child(
        target: &ConnectorChildTarget,
        secret: &str,
        stream: &UnixStream,
    ) -> Result<Child, String> {
        let stdin_stream = stream
            .try_clone()
            .map_err(|_| "connector relay input could not be attached".to_string())?;
        let stdout_stream = stream
            .try_clone()
            .map_err(|_| "connector relay output could not be attached".to_string())?;
        let stdin_fd: OwnedFd = stdin_stream.into();
        let stdout_fd: OwnedFd = stdout_stream.into();
        Command::new(&target.executable)
            .current_dir(&target.working_directory)
            .env_clear()
            .env(target.api_key_env, secret)
            .stdin(Stdio::from(stdin_fd))
            .stdout(Stdio::from(stdout_fd))
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| format!("connector {:?} could not start", target.connector_id))
    }

    fn relay_disconnected(stream: &UnixStream) -> bool {
        let mut descriptor = PollFd {
            fd: stream.as_raw_fd(),
            events: POLLHUP | POLLERR,
            revents: 0,
        };
        // SAFETY: descriptor is a valid one-element pollfd array.
        let result = unsafe { poll(&mut descriptor, 1, 0) };
        result < 0 || descriptor.revents & (POLLHUP | POLLERR | POLLNVAL) != 0
    }

    fn stop_child(child: &mut Child) {
        let _ = child.kill();
        let _ = child.wait();
    }

    struct ActiveConnection(Arc<BrokerShared>);

    impl Drop for ActiveConnection {
        fn drop(&mut self) {
            self.0.active_connections.fetch_sub(1, Ordering::AcqRel);
        }
    }

    fn reserve_connection(shared: &Arc<BrokerShared>) -> Option<ActiveConnection> {
        shared
            .active_connections
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |active| {
                (active < MAX_ACTIVE_CONNECTIONS).then_some(active + 1)
            })
            .ok()
            .map(|_| ActiveConnection(shared.clone()))
    }

    fn serve_connection(
        shared: Arc<BrokerShared>,
        connector_id: &'static str,
        stream: UnixStream,
        _active: ActiveConnection,
    ) {
        let parent = match authenticate_relay_peer(&stream) {
            Ok(parent) => parent,
            Err(_) => return,
        };
        let generation = match wait_for_authorization(&shared, parent) {
            Ok(generation) => generation,
            Err(_) => return,
        };
        if validate_live_config(&shared, connector_id).is_err() {
            return;
        }
        let target = match canonical_connector_child_target(&shared.app_data_dir, connector_id) {
            Ok(target) => target,
            Err(_) => return,
        };

        // The credential is read only after peer, parent, config, and exact
        // target validation. It is never serialized or written to the socket.
        let secret = match read_connector_secret(connector_id) {
            Ok(secret) => secret,
            Err(_) => return,
        };
        let refreshed_target =
            match canonical_connector_child_target(&shared.app_data_dir, connector_id) {
                Ok(target) => target,
                Err(_) => return,
            };
        if shared.generation.load(Ordering::Acquire) != generation
            || validate_live_config(&shared, connector_id).is_err()
            || refreshed_target != target
        {
            return;
        }
        let mut child = match spawn_child(&target, &secret, &stream) {
            Ok(child) => child,
            Err(_) => return,
        };
        drop(secret);

        let mut last_config_check = Instant::now();
        loop {
            match child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) => {}
                Err(_) => {
                    stop_child(&mut child);
                    return;
                }
            }
            if shared.shutting_down.load(Ordering::Acquire)
                || shared.generation.load(Ordering::Acquire) != generation
                || relay_disconnected(&stream)
            {
                stop_child(&mut child);
                return;
            }
            if last_config_check.elapsed() >= CONFIG_RECHECK_INTERVAL {
                if validate_live_config(&shared, connector_id).is_err() {
                    stop_child(&mut child);
                    return;
                }
                last_config_check = Instant::now();
            }
            thread::sleep(CHILD_POLL_INTERVAL);
        }
    }

    fn listener_loop(
        shared: Arc<BrokerShared>,
        connector_id: &'static str,
        listener: UnixListener,
    ) {
        while !shared.shutting_down.load(Ordering::Acquire) {
            match listener.accept() {
                Ok((stream, _)) => {
                    let Some(active) = reserve_connection(&shared) else {
                        continue;
                    };
                    let shared = shared.clone();
                    let _ = thread::Builder::new()
                        .name(format!("spark-mcp-{connector_id}"))
                        .spawn(move || serve_connection(shared, connector_id, stream, active));
                }
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    thread::sleep(LISTENER_POLL_INTERVAL);
                }
                Err(_) => return,
            }
        }
    }

    #[cfg(test)]
    pub(super) fn authorization_matches(
        authorized: (u32, u64, u64),
        peer_parent: (u32, u64, u64),
    ) -> bool {
        ProcessIdentity {
            pid: authorized.0,
            start_seconds: authorized.1,
            start_microseconds: authorized.2,
        } == ProcessIdentity {
            pid: peer_parent.0,
            start_seconds: peer_parent.1,
            start_microseconds: peer_parent.2,
        }
    }
}

#[cfg(target_os = "macos")]
fn current_broker() -> Result<std::sync::Arc<broker::ConnectorBroker>, String> {
    broker::current()
}

/// Start the in-process keyed-connector broker. On non-macOS platforms keyed
/// connector execution fails closed; unkeyed connector provisioning is unchanged.
pub(crate) fn ensure_connector_broker(app: &AppHandle) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        broker::ensure(app)
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = app;
        Err("Spark managed connector brokerage is supported only on macOS".to_string())
    }
}

/// Authorize exactly one spawned OpenCode process identity (PID plus kernel
/// start time). Re-authorizing the same identity is idempotent.
pub(crate) fn authorize_connector_broker(pid: u32) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        broker::authorize(pid)
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = pid;
        Err("Spark managed connector brokerage is supported only on macOS".to_string())
    }
}

/// Revoke the current sidecar only when its PID matches, protecting a
/// replacement sidecar from a late watcher belonging to the old process.
pub(crate) fn revoke_connector_broker(pid: u32) {
    #[cfg(target_os = "macos")]
    broker::revoke(Some(pid));
    #[cfg(not(target_os = "macos"))]
    let _ = pid;
}

/// Stop listeners, invalidate authorization, kill active connector children,
/// and remove the per-process socket directory.
pub(crate) fn shutdown_connector_broker() {
    #[cfg(target_os = "macos")]
    broker::shutdown();
}

/// The managed interpreter path if the shared environment exists.
#[tauri::command]
pub fn science_mcp_python(app: AppHandle) -> Result<Option<String>, String> {
    let python = python_bin(&app)?;
    Ok(python
        .exists()
        .then(|| python.to_string_lossy().to_string()))
}

/// Provision one allowlisted exact package. Keyed connectors use independent
/// environments and setup verifies their exact expected console script.
#[tauri::command]
pub async fn setup_science_mcp(app: AppHandle, connector_id: String) -> Result<String, String> {
    let spec = connector_spec(&connector_id)?;
    if spec.environment == ConnectorEnvironment::Managed {
        ensure_managed_connector_execution_enabled()?;
    }
    if !is_safe_package(spec.package) {
        return Err(format!(
            "science connector {:?} has an invalid native package pin",
            spec.connector_id
        ));
    }
    let dir = connector_env_dir(&app, spec)?;
    std::fs::create_dir_all(&dir).map_err(|error| error.to_string())?;

    crate::uv::run_uv(
        &app,
        "science",
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

    let python = python_bin_in(&dir);
    crate::uv::run_uv(
        &app,
        "science",
        vec![
            "pip".into(),
            "install".into(),
            "--python".into(),
            python.to_string_lossy().to_string(),
            spec.package.to_string(),
        ],
        "uv pip install",
    )
    .await?;
    if let Some(script) = spec.managed_script {
        let command = managed_connector_target_command(&app, spec.connector_id)?;
        if !Path::new(&command[0]).is_file() {
            return Err(format!(
                "science connector {:?} installed without expected script {script:?}",
                spec.connector_id
            ));
        }
    }
    Ok(python.to_string_lossy().to_string())
}

fn is_safe_package(package: &str) -> bool {
    let core = package
        .split_once("==")
        .map(|(name, _)| name)
        .unwrap_or(package);
    !core.is_empty()
        && !core.starts_with('-')
        && core.chars().all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | '-')
        })
        && package.chars().all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | '-' | '=')
        })
}

#[cfg(test)]
mod tests {
    use super::{
        connector_child_target, connector_spec, is_managed_connector_relay_command,
        is_safe_package, managed_connector_execution_enabled, relay_command,
        validate_connector_relay_config, ConnectorEnvironment, CONNECTOR_OWNER_ENV,
        CONNECTOR_OWNER_V1, NC_PATH, NC_UNIX_FLAG, SCIENCE_CONNECTORS,
    };
    use serde_json::json;
    use std::path::Path;

    #[test]
    fn accepts_real_package_names_and_pins() {
        assert!(is_safe_package("paper-search-mcp"));
        assert!(is_safe_package("biomcp-python"));
        assert!(is_safe_package("jupyter-mcp-server==0.14.0"));
    }

    #[test]
    fn rejects_package_argument_injection() {
        for package in [
            "",
            "--upgrade",
            "pkg; rm -rf /",
            "pkg && echo",
            "pkg --index-url http://evil",
            "pkg\nother",
        ] {
            assert!(!is_safe_package(package));
        }
    }

    #[test]
    fn native_catalog_pins_every_package_and_isolates_keyed_connectors() {
        assert!(SCIENCE_CONNECTORS
            .iter()
            .all(|spec| spec.package.contains("==")));
        let materials = connector_spec("materials-project").unwrap();
        assert_eq!(materials.package, "mcp-materials-project==0.3.3");
        assert_eq!(materials.managed_script, Some("mcp-materials-project"));
        assert_eq!(materials.api_key_env, Some("MP_API_KEY"));
        assert_eq!(materials.environment, ConnectorEnvironment::Managed);
        let fred = connector_spec("fred").unwrap();
        assert_eq!(fred.package, "fred-mcp==1.0.1");
        assert_eq!(fred.managed_script, Some("fred-mcp"));
        assert_eq!(fred.api_key_env, Some("FRED_API_KEY"));
        assert_eq!(fred.environment, ConnectorEnvironment::Managed);
        assert_ne!(materials.socket_name, fred.socket_name);
        assert!(connector_spec("renderer-package-name").is_err());
    }

    #[test]
    fn credential_bearing_connector_execution_is_fail_closed() {
        assert!(!managed_connector_execution_enabled());
    }

    #[test]
    fn relay_command_is_exact_secretless_and_per_connector() {
        let materials_socket = Path::new("/private/tmp/spark-mcp-0123456789abcdef/mp.sock");
        let fred_socket = Path::new("/private/tmp/spark-mcp-0123456789abcdef/fred.sock");
        let materials = relay_command(materials_socket, "materials-project").unwrap();
        let fred = relay_command(fred_socket, "fred").unwrap();
        assert_eq!(
            materials,
            vec![
                NC_PATH.to_string(),
                NC_UNIX_FLAG.to_string(),
                materials_socket.to_string_lossy().to_string(),
            ]
        );
        assert_eq!(fred[0], NC_PATH);
        assert_eq!(fred[1], NC_UNIX_FLAG);
        assert_ne!(materials[2], fred[2]);
        assert!(!materials.join("\0").contains("MP_API_KEY"));
        assert!(!fred.join("\0").contains("FRED_API_KEY"));
        assert!(is_managed_connector_relay_command(
            &materials,
            "materials-project"
        ));
        assert!(is_managed_connector_relay_command(&fred, "fred"));
        let prior = relay_command(
            Path::new("/private/tmp/spark-mcp-0123456789abcdef/fred.sock"),
            "fred",
        )
        .unwrap();
        assert!(is_managed_connector_relay_command(&prior, "fred"));
        for invalid in [
            vec![
                "/tmp/nc".to_string(),
                "-U".to_string(),
                "/private/tmp/spark-mcp-0123456789abcdef/fred.sock".to_string(),
            ],
            vec![
                NC_PATH.to_string(),
                "-u".to_string(),
                "/private/tmp/spark-mcp-0123456789abcdef/fred.sock".to_string(),
            ],
            vec![
                NC_PATH.to_string(),
                "-U".to_string(),
                "/private/tmp/spark-mcp-not-random/fred.sock".to_string(),
            ],
            vec![
                NC_PATH.to_string(),
                "-U".to_string(),
                "/private/tmp/spark-mcp-0123456789abcdef/mp.sock".to_string(),
            ],
        ] {
            assert!(!is_managed_connector_relay_command(&invalid, "fred"));
        }
        assert!(relay_command(Path::new("relative.sock"), "fred").is_err());
        assert!(relay_command(materials_socket, "paper-search").is_err());
    }

    #[test]
    fn strict_config_authorizes_only_exact_enabled_owner_and_unique_command() {
        let command = relay_command(
            Path::new("/private/tmp/spark-mcp-0123456789abcdef/fred.sock"),
            "fred",
        )
        .unwrap();
        let canonical = json!({
            "mcp": {
                "fred": {
                    "type": "local",
                    "command": command,
                    "enabled": true,
                    "environment": {(CONNECTOR_OWNER_ENV): CONNECTOR_OWNER_V1}
                }
            }
        });
        let expected = canonical["mcp"]["fred"]["command"]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| value.as_str().unwrap().to_string())
            .collect::<Vec<_>>();
        assert!(validate_connector_relay_config(
            &serde_json::to_string(&canonical).unwrap(),
            "fred",
            &expected,
        )
        .is_ok());

        for mutation in [
            "disabled",
            "placeholder",
            "extra",
            "duplicate",
            "wrong-socket",
        ] {
            let mut invalid = canonical.clone();
            match mutation {
                "disabled" => invalid["mcp"]["fred"]["enabled"] = json!(false),
                "placeholder" => {
                    invalid["mcp"]["fred"]["environment"]["FRED_API_KEY"] =
                        json!("{env:SPARK_OPENCODE_CONNECTOR_KEY_FAKE}")
                }
                "extra" => invalid["mcp"]["fred"]["cwd"] = json!("/tmp"),
                "duplicate" => invalid["mcp"]["other"] = invalid["mcp"]["fred"].clone(),
                "wrong-socket" => {
                    invalid["mcp"]["fred"]["command"][2] = json!("/private/tmp/attacker.sock")
                }
                _ => unreachable!(),
            }
            assert!(validate_connector_relay_config(
                &serde_json::to_string(&invalid).unwrap(),
                "fred",
                &expected,
            )
            .is_err());
        }
    }

    #[test]
    fn child_target_has_only_the_allowlisted_key_name_and_exact_path() {
        let materials =
            connector_child_target(Path::new("/secure/app-data"), "materials-project").unwrap();
        assert_eq!(materials.api_key_env, "MP_API_KEY");
        assert!(materials
            .executable
            .ends_with("science-mcp-managed/materials-project/bin/mcp-materials-project"));
        let fred = connector_child_target(Path::new("/secure/app-data"), "fred").unwrap();
        assert_eq!(fred.api_key_env, "FRED_API_KEY");
        assert!(fred
            .executable
            .ends_with("science-mcp-managed/fred/bin/fred-mcp"));
        assert_ne!(materials.api_key_env, fred.api_key_env);
        assert!(connector_child_target(Path::new("/secure/app-data"), "paper-search").is_err());
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn authorization_identity_includes_pid_and_start_time() {
        assert!(super::broker::authorization_matches(
            (42, 100, 7),
            (42, 100, 7)
        ));
        assert!(!super::broker::authorization_matches(
            (42, 100, 7),
            (42, 101, 7)
        ));
        assert!(!super::broker::authorization_matches(
            (42, 100, 7),
            (43, 100, 7)
        ));
    }
}
