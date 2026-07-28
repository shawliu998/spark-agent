//! Fail-closed lifecycle for the optional packaged Docker Science Core runtime.
//!
//! Release builds validate bundled resources, load exact OCI image IDs, and run
//! one fixed Compose project. Debug and non-macOS builds remain unavailable.

#![cfg_attr(any(not(target_os = "macos"), debug_assertions), allow(dead_code))]

use std::collections::BTreeSet;
use std::fs::{self, File, Metadata, OpenOptions};
use std::io::{Read, Seek, Write};
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc, Arc, Condvar, Mutex,
};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const PROJECT_NAME: &str = "spark-agent-science-core";
const SERVICE_NAME: &str = "science-core";
const RESOURCE_DIRECTORY: &str = "science-core";
const COMPOSE_FILE: &str = "compose.yaml";
const MANIFEST_FILE: &str = "manifest.json";
const CORE_ARCHIVE: &str = "science-core.oci.tar";
const RUNTIME_ARCHIVE: &str = "science-runtime.oci.tar";
const CORE_IMAGE: &str = "io.github.shawliu998.sparkagent/science-core:0.2.0";
const RUNTIME_IMAGE: &str = "io.github.shawliu998.sparkagent/science-runtime:0.2.0";
const MANIFEST_SCHEMA_VERSION: u32 = 1;
const MAX_MANIFEST_BYTES: u64 = 64 * 1024;
const MAX_COMMAND_OUTPUT_BYTES: usize = 64 * 1024;
const DOCKER_PREFLIGHT_TIMEOUT: Duration = Duration::from_secs(15);
const IMAGE_LOAD_TIMEOUT: Duration = Duration::from_secs(180);
const IMAGE_INSPECT_TIMEOUT: Duration = Duration::from_secs(15);
const COMPOSE_WAIT_TIMEOUT_SECONDS: &str = "90";
const COMPOSE_COMMAND_TIMEOUT: Duration = Duration::from_secs(105);
const COMPOSE_CLEANUP_TIMEOUT: Duration = Duration::from_secs(30);
const COMPOSE_PORT_TIMEOUT: Duration = Duration::from_secs(15);
const KEYCHAIN_TIMEOUT: Duration = Duration::from_secs(15);
const HEALTH_ATTEMPTS: usize = 30;
const HEALTH_INTERVAL: Duration = Duration::from_millis(500);
const EXIT_WORKER_WAIT_TIMEOUT: Duration = Duration::from_secs(240);
const EXPECTED_CORE_VERSION: &str = "0.2.0";
const KEYCHAIN_SERVICE: &str = "io.github.shawliu998.sparkagent.model-api-key";
const KEYCHAIN_ACCOUNT: &str = "openai-compatible";
const MODEL_CONFIG_FILE: &str = "model-config.json";
const SKILL_MCP_DESCRIPTOR_FILE: &str = "spark-skill-mcp-connection.json";
const MAX_MODEL_CONFIG_BYTES: u64 = 16 * 1024;
const OWNER_MARKER: &str = ".owner";
const OWNER_MARKER_CONTENT: &[u8] = b"spark-agent-science-core\n";
const UNAVAILABLE_MESSAGE: &str =
    "Science Core is unavailable because packaged offline runtime resources were not found";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CommandSpec {
    program: &'static str,
    args: Vec<String>,
}

#[allow(dead_code)]
trait CommandRunner: Send + Sync {
    fn run(&self, spec: &CommandSpec, timeout: Duration) -> Result<String, String>;
    fn run_cancellable(
        &self,
        spec: &CommandSpec,
        timeout: Duration,
        _cancelled: &AtomicBool,
    ) -> Result<String, String> {
        self.run(spec, timeout)
    }
    fn run_with_stdin(
        &self,
        spec: &CommandSpec,
        stdin: File,
        timeout: Duration,
    ) -> Result<String, String>;
}

#[allow(dead_code)]
struct SystemRunner;

impl CommandRunner for SystemRunner {
    fn run(&self, spec: &CommandSpec, timeout: Duration) -> Result<String, String> {
        self.run_command(spec, None, timeout, None)
    }

    fn run_cancellable(
        &self,
        spec: &CommandSpec,
        timeout: Duration,
        cancelled: &AtomicBool,
    ) -> Result<String, String> {
        self.run_command(spec, None, timeout, Some(cancelled))
    }

    fn run_with_stdin(
        &self,
        spec: &CommandSpec,
        stdin: File,
        timeout: Duration,
    ) -> Result<String, String> {
        self.run_command(spec, Some(stdin), timeout, None)
    }
}

impl SystemRunner {
    fn run_command(
        &self,
        spec: &CommandSpec,
        stdin: Option<File>,
        timeout: Duration,
        cancelled: Option<&AtomicBool>,
    ) -> Result<String, String> {
        let child_stdin = stdin.map(Stdio::from).unwrap_or_else(Stdio::null);
        let program = if spec.program == "docker" {
            resolve_docker_program()
        } else {
            PathBuf::from(spec.program)
        };
        let mut child = crate::runtime::quiet_command(program)
            .args(&spec.args)
            .stdin(child_stdin)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|_| "Docker Desktop is not available".to_string())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "Docker command output is unavailable".to_string())?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| "Docker command output is unavailable".to_string())?;
        let (sender, receiver) = mpsc::channel();
        let stdout_sender = sender.clone();
        thread::spawn(move || {
            let _ = stdout_sender.send((true, drain_pipe(stdout, true)));
        });
        thread::spawn(move || {
            let _ = sender.send((false, drain_pipe(stderr, false)));
        });

        let deadline = Instant::now() + timeout;
        let status = loop {
            match child
                .try_wait()
                .map_err(|_| "Docker command failed".to_string())?
            {
                Some(status) => break status,
                None if cancelled.is_some_and(|cancelled| cancelled.load(Ordering::Acquire)) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err("Docker command was cancelled".into());
                }
                None if Instant::now() >= deadline => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err("Docker command timed out".into());
                }
                None => thread::sleep(Duration::from_millis(10)),
            }
        };
        if !status.success() {
            return Err("Docker command failed".into());
        }

        let mut captured_stdout = None;
        for _ in 0..2 {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err("Docker command timed out".into());
            }
            let (is_stdout, drained) = receiver
                .recv_timeout(remaining)
                .map_err(|_| "Docker command timed out".to_string())?;
            let bytes = drained.map_err(|_| "Docker command output is invalid".to_string())?;
            if is_stdout {
                captured_stdout = Some(bytes);
            }
        }
        String::from_utf8(captured_stdout.unwrap_or_default())
            .map_err(|_| "Docker command output is invalid".to_string())
    }
}

fn resolve_docker_program() -> PathBuf {
    let mut candidates = Vec::new();
    if let Some(path) = std::env::var_os("PATH") {
        candidates.extend(std::env::split_paths(&path).map(|directory| directory.join("docker")));
    }
    if let Some(home) = std::env::var_os("HOME") {
        let home = PathBuf::from(home);
        candidates.push(home.join(".docker/bin/docker"));
        candidates.push(home.join(".orbstack/bin/docker"));
    }
    candidates.extend([
        PathBuf::from("/usr/local/bin/docker"),
        PathBuf::from("/opt/homebrew/bin/docker"),
        PathBuf::from("/Applications/Docker.app/Contents/Resources/bin/docker"),
        PathBuf::from("/Applications/OrbStack.app/Contents/MacOS/xbin/docker"),
    ]);
    first_existing_program(candidates).unwrap_or_else(|| PathBuf::from("docker"))
}

fn first_existing_program(candidates: impl IntoIterator<Item = PathBuf>) -> Option<PathBuf> {
    candidates
        .into_iter()
        .find(|candidate| is_executable_file(candidate))
}

fn is_executable_file(candidate: &Path) -> bool {
    let Ok(metadata) = candidate.metadata() else {
        return false;
    };
    if !metadata.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        metadata.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    {
        true
    }
}

fn drain_pipe<R: Read>(mut pipe: R, capture: bool) -> Result<Vec<u8>, ()> {
    let mut captured = Vec::new();
    let mut overflow = false;
    let mut buffer = [0_u8; 16 * 1024];
    loop {
        let count = pipe.read(&mut buffer).map_err(|_| ())?;
        if count == 0 {
            break;
        }
        if capture && !overflow {
            let remaining = MAX_COMMAND_OUTPUT_BYTES.saturating_sub(captured.len());
            if count > remaining {
                overflow = true;
                captured.clear();
            } else {
                captured.extend_from_slice(&buffer[..count]);
            }
        }
    }
    if overflow {
        Err(())
    } else {
        Ok(captured)
    }
}

#[derive(Clone, Debug, serde::Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ScienceCoreStatus {
    pub state: &'static str,
    pub endpoint: Option<String>,
    pub docker_ready: bool,
    pub compose_ready: bool,
    pub message: Option<String>,
}

impl ScienceCoreStatus {
    fn unavailable() -> Self {
        Self {
            state: "unavailable",
            endpoint: None,
            docker_ready: false,
            compose_ready: false,
            message: Some(UNAVAILABLE_MESSAGE.to_string()),
        }
    }

    fn starting() -> Self {
        Self {
            state: "starting",
            endpoint: None,
            docker_ready: false,
            compose_ready: false,
            message: Some("Science Core is starting from verified offline resources".into()),
        }
    }

    fn ready(endpoint: String) -> Self {
        Self {
            state: "ready",
            endpoint: Some(endpoint),
            docker_ready: true,
            compose_ready: true,
            message: None,
        }
    }

    fn failed(message: String, docker_ready: bool) -> Self {
        Self {
            state: "failed",
            endpoint: None,
            docker_ready,
            compose_ready: false,
            message: Some(sanitize_message(&message)),
        }
    }

    fn stopping() -> Self {
        Self {
            state: "stopping",
            endpoint: None,
            docker_ready: true,
            compose_ready: false,
            message: Some("Science Core is stopping".into()),
        }
    }

    fn stopped() -> Self {
        Self {
            state: "stopped",
            endpoint: None,
            docker_ready: false,
            compose_ready: false,
            message: Some("Science Core was stopped by the user".into()),
        }
    }
}

#[derive(Clone, Debug, serde::Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ScienceCoreConnection {
    endpoint: String,
    token: String,
}

#[derive(Clone, Default)]
pub struct ScienceCoreState {
    inner: Arc<Mutex<LifecycleState>>,
}

struct LifecycleState {
    status: ScienceCoreStatus,
    active: Option<ActiveRuntime>,
    start_cancel: Option<Arc<AtomicBool>>,
    start_worker: Option<Arc<WorkerCompletion>>,
    generation: u64,
}

impl Default for LifecycleState {
    fn default() -> Self {
        Self {
            status: ScienceCoreStatus::unavailable(),
            active: None,
            start_cancel: None,
            start_worker: None,
            generation: 0,
        }
    }
}

#[derive(Default)]
struct WorkerCompletion {
    complete: Mutex<bool>,
    changed: Condvar,
}

impl WorkerCompletion {
    fn finish(&self) {
        *self.complete.lock().unwrap() = true;
        self.changed.notify_all();
    }

    fn wait(&self, timeout: Duration) -> bool {
        let complete = self.complete.lock().unwrap();
        if *complete {
            return true;
        }
        let (complete, _) = self
            .changed
            .wait_timeout_while(complete, timeout, |complete| !*complete)
            .unwrap();
        *complete
    }
}

#[derive(Clone, Debug)]
struct ActiveRuntime {
    compose: PathBuf,
    env_file: PathBuf,
    session_dir: PathBuf,
    staging_dir: PathBuf,
    endpoint: String,
    token: String,
}

#[derive(Debug)]
struct LifecycleFailure {
    message: String,
    docker_ready: bool,
    cleanup: Option<Box<ActiveRuntime>>,
}

trait CredentialProvider: Send + Sync {
    fn model_secret(&self, model: &ModelConfig) -> Result<Option<Vec<u8>>, String>;
}

trait HealthProbe: Send + Sync {
    fn wait_ready(&self, endpoint: &str) -> Result<(), String>;
}

struct MacKeychainCredential;
struct SystemHealthProbe;

#[derive(Debug)]
struct BundledResources {
    #[allow(dead_code)]
    root: PathBuf,
    #[allow(dead_code)]
    compose: PathBuf,
    compose_sha256: String,
    compose_source: File,
    #[allow(dead_code)]
    images: [BundledImage; 2],
}

#[derive(Debug)]
struct BundledImage {
    archive: PathBuf,
    image: String,
    image_id: String,
    archive_sha256: String,
    source: File,
}

#[derive(Debug)]
struct PreparedImage {
    image: String,
    image_id: String,
    snapshot: File,
}

#[derive(Debug, PartialEq, Eq)]
struct LoadedImages {
    image_ids: [String; 2],
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ResourceManifest {
    schema_version: u32,
    compose_sha256: String,
    images: [ImageManifest; 2],
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ImageManifest {
    archive: String,
    image: String,
    image_id: String,
    sha256: String,
}

#[allow(dead_code)]
fn fixed_compose_args(compose: &Path, env_file: &Path, action: &[&str]) -> CommandSpec {
    let mut args = vec![
        "compose".into(),
        "--project-name".into(),
        PROJECT_NAME.into(),
        "--env-file".into(),
        env_file.display().to_string(),
        "--file".into(),
        compose.display().to_string(),
    ];
    args.extend(action.iter().map(|value| (*value).to_owned()));
    CommandSpec {
        program: "docker",
        args,
    }
}

#[allow(dead_code)]
fn fixed_preflight_specs() -> [CommandSpec; 3] {
    [
        CommandSpec {
            program: "docker",
            args: vec![
                "version".into(),
                "--format".into(),
                "{{.Server.Version}}".into(),
            ],
        },
        CommandSpec {
            program: "docker",
            args: vec!["compose".into(), "version".into(), "--short".into()],
        },
        CommandSpec {
            program: "docker",
            args: vec![
                "info".into(),
                "--format".into(),
                "{{.ServerVersion}}".into(),
            ],
        },
    ]
}

#[allow(dead_code)]
fn fixed_start_spec(compose: &Path, env_file: &Path) -> CommandSpec {
    fixed_compose_args(
        compose,
        env_file,
        &[
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "--wait",
            "--wait-timeout",
            COMPOSE_WAIT_TIMEOUT_SECONDS,
        ],
    )
}

#[allow(dead_code)]
fn fixed_port_spec(compose: &Path, env_file: &Path) -> CommandSpec {
    fixed_compose_args(compose, env_file, &["port", SERVICE_NAME, "8765"])
}

fn fixed_stop_spec(compose: &Path, env_file: &Path) -> CommandSpec {
    fixed_compose_args(
        compose,
        env_file,
        &["down", "--timeout", "10", "--volumes", "--remove-orphans"],
    )
}

fn fixed_image_load_spec() -> CommandSpec {
    CommandSpec {
        program: "docker",
        args: vec!["image".into(), "load".into()],
    }
}

fn fixed_image_inspect_spec(image: &PreparedImage) -> CommandSpec {
    CommandSpec {
        program: "docker",
        args: vec![
            "image".into(),
            "inspect".into(),
            "--format".into(),
            "{{.Id}}\t{{.Os}}\t{{.Architecture}}".into(),
            image.image.clone(),
        ],
    }
}

#[allow(dead_code)]
fn parse_loopback_endpoint(output: &str) -> Result<String, String> {
    let value = output.trim();
    let address: SocketAddr = value
        .parse()
        .map_err(|_| "Science Core did not publish a loopback endpoint".to_string())?;
    if address.ip() != IpAddr::V4(Ipv4Addr::LOCALHOST) || address.port() == 0 {
        return Err("Science Core did not publish a loopback endpoint".into());
    }
    Ok(format!("http://127.0.0.1:{}", address.port()))
}

fn expected_docker_architecture() -> Result<&'static str, String> {
    match std::env::consts::ARCH {
        "aarch64" => Ok("arm64"),
        "x86_64" => Ok("amd64"),
        _ => Err("Science Core does not support this CPU architecture".into()),
    }
}

fn validate_inspected_image(
    output: &str,
    expected_image_id: &str,
    expected_architecture: &str,
) -> Result<(), String> {
    let line = output.strip_suffix('\n').unwrap_or(output);
    if line.contains('\r') || line.contains('\n') {
        return Err("Docker returned invalid Science Core image metadata".into());
    }
    let fields = line.split('\t').collect::<Vec<_>>();
    if fields.as_slice() != [expected_image_id, "linux", expected_architecture] {
        return Err("Docker loaded an unexpected Science Core image".into());
    }
    Ok(())
}

fn validate_session_token(token: &str) -> Result<(), String> {
    if token.len() != 64
        || !token
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err("Invalid Science Core session token".into());
    }
    Ok(())
}

fn sanitize_message(value: &str) -> String {
    let normalized = value
        .chars()
        .filter(|character| !character.is_control())
        .take(240)
        .collect::<String>();
    if normalized.is_empty() {
        "Science Core failed without a diagnostic".into()
    } else {
        normalized
    }
}

impl CredentialProvider for MacKeychainCredential {
    fn model_secret(&self, model: &ModelConfig) -> Result<Option<Vec<u8>>, String> {
        #[cfg(target_os = "macos")]
        {
            let Some(account) = model_credential_account(model) else {
                return Ok(None);
            };
            let status = CommandSpec {
                program: "/usr/bin/security",
                args: vec![
                    "find-generic-password".into(),
                    "-a".into(),
                    account.clone(),
                    "-s".into(),
                    KEYCHAIN_SERVICE.into(),
                ],
            };
            if SystemRunner.run(&status, KEYCHAIN_TIMEOUT).is_err() {
                return Ok(None);
            }
            let read = CommandSpec {
                program: "/usr/bin/security",
                args: vec![
                    "find-generic-password".into(),
                    "-a".into(),
                    account,
                    "-s".into(),
                    KEYCHAIN_SERVICE.into(),
                    "-w".into(),
                ],
            };
            let value = SystemRunner.run(&read, KEYCHAIN_TIMEOUT).map_err(|_| {
                "The model credential could not be read from macOS Keychain".to_string()
            })?;
            normalize_secret(value.as_bytes()).map(Some)
        }
        #[cfg(not(target_os = "macos"))]
        {
            Ok(None)
        }
    }
}

fn normalize_secret(value: &[u8]) -> Result<Vec<u8>, String> {
    if value.len() > 4096 {
        return Err("The model credential exceeds the safe size limit".into());
    }
    let text = std::str::from_utf8(value)
        .map_err(|_| "The model credential is not valid UTF-8".to_string())?;
    let normalized = text.trim();
    if normalized
        .chars()
        .any(|character| character.is_control() || character == '\u{7f}')
    {
        return Err("The model credential contains invalid data".into());
    }
    Ok(normalized.as_bytes().to_vec())
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct HealthResponse {
    status: String,
    version: String,
    database: String,
    runtime: String,
}

impl HealthProbe for SystemHealthProbe {
    fn wait_ready(&self, endpoint: &str) -> Result<(), String> {
        let client = reqwest::blocking::Client::builder()
            .connect_timeout(Duration::from_secs(2))
            .timeout(Duration::from_secs(2))
            .build()
            .map_err(|_| "Science Core health client is unavailable".to_string())?;
        let url = format!("{endpoint}/health");
        for _ in 0..HEALTH_ATTEMPTS {
            if let Ok(response) = client.get(&url).send() {
                if response.status().is_success() {
                    if let Ok(body) = response.text() {
                        if let Ok(health) = serde_json::from_str::<HealthResponse>(&body) {
                            if health.status == "ok"
                                && health.database == "ok"
                                && health.runtime == "ready"
                                && health.version == EXPECTED_CORE_VERSION
                            {
                                return Ok(());
                            }
                        }
                    }
                }
            }
            thread::sleep(HEALTH_INTERVAL);
        }
        Err("Science Core did not pass its authenticated local health contract".into())
    }
}

fn validate_bundled_resources(resources: &Path) -> Result<BundledResources, String> {
    let root = resources.join(RESOURCE_DIRECTORY);
    let root_before = fs::symlink_metadata(&root)
        .map_err(|_| "Science Core runtime resources are not bundled".to_string())?;
    validate_directory_metadata(&root_before, "Science Core runtime resource directory")?;

    let expected_entries = BTreeSet::from([
        COMPOSE_FILE.to_string(),
        MANIFEST_FILE.to_string(),
        CORE_ARCHIVE.to_string(),
        RUNTIME_ARCHIVE.to_string(),
    ]);
    let actual_entries = fs::read_dir(&root)
        .map_err(|_| "Could not inspect Science Core runtime resources".to_string())?
        .map(|entry| {
            entry
                .map_err(|_| "Could not inspect Science Core runtime resources".to_string())?
                .file_name()
                .into_string()
                .map_err(|_| "Science Core runtime contains an invalid resource name".to_string())
        })
        .collect::<Result<BTreeSet<_>, _>>()?;
    if actual_entries != expected_entries {
        return Err(
            "Science Core runtime resource set is incomplete or contains extra files".into(),
        );
    }

    let manifest_path = root.join(MANIFEST_FILE);
    let manifest_file = open_regular_file(&manifest_path, "Science Core runtime manifest")?;
    if manifest_file
        .metadata()
        .map_err(|_| "Could not inspect Science Core runtime manifest".to_string())?
        .len()
        > MAX_MANIFEST_BYTES
    {
        return Err("Science Core runtime manifest is too large".into());
    }
    let manifest: ResourceManifest = serde_json::from_reader(manifest_file)
        .map_err(|_| "Science Core runtime manifest is invalid".to_string())?;
    validate_manifest(&manifest)?;

    let compose = root.join(COMPOSE_FILE);
    let compose_source = open_verified_file(
        &compose,
        &manifest.compose_sha256,
        "Science Core production compose",
    )?;

    let mut images = Vec::with_capacity(2);
    for (archive, image) in [(CORE_ARCHIVE, CORE_IMAGE), (RUNTIME_ARCHIVE, RUNTIME_IMAGE)] {
        let entry = manifest
            .images
            .iter()
            .find(|entry| entry.archive == archive && entry.image == image)
            .ok_or_else(|| {
                "Science Core runtime manifest has an unexpected image identity".to_string()
            })?;
        let path = root.join(archive);
        let source = open_verified_file(&path, &entry.sha256, "Science Core OCI archive")?;
        images.push(BundledImage {
            archive: path,
            image: entry.image.clone(),
            image_id: entry.image_id.clone(),
            archive_sha256: entry.sha256.clone(),
            source,
        });
    }

    let root_after = fs::symlink_metadata(&root).map_err(|_| {
        "Science Core runtime resource directory changed during validation".to_string()
    })?;
    validate_directory_metadata(&root_after, "Science Core runtime resource directory")?;
    if !same_file(&root_before, &root_after) {
        return Err("Science Core runtime resource directory changed during validation".into());
    }

    let images: [BundledImage; 2] = images
        .try_into()
        .map_err(|_| "Science Core runtime manifest is invalid".to_string())?;
    Ok(BundledResources {
        root,
        compose,
        compose_sha256: manifest.compose_sha256,
        compose_source,
        images,
    })
}

fn validate_manifest(manifest: &ResourceManifest) -> Result<(), String> {
    if manifest.schema_version != MANIFEST_SCHEMA_VERSION
        || !is_sha256(&manifest.compose_sha256)
        || manifest.images.iter().any(|entry| {
            !is_sha256(&entry.sha256)
                || !is_image_id(&entry.image_id)
                || !matches!(
                    (entry.archive.as_str(), entry.image.as_str()),
                    (CORE_ARCHIVE, CORE_IMAGE) | (RUNTIME_ARCHIVE, RUNTIME_IMAGE)
                )
        })
    {
        return Err("Science Core runtime manifest is invalid".into());
    }
    let pairs = manifest
        .images
        .iter()
        .map(|entry| (entry.archive.as_str(), entry.image.as_str()))
        .collect::<BTreeSet<_>>();
    let expected = BTreeSet::from([(CORE_ARCHIVE, CORE_IMAGE), (RUNTIME_ARCHIVE, RUNTIME_IMAGE)]);
    if pairs != expected {
        return Err("Science Core runtime manifest has an unexpected image identity".into());
    }
    let image_ids = manifest
        .images
        .iter()
        .map(|entry| entry.image_id.as_str())
        .collect::<BTreeSet<_>>();
    if image_ids.len() != manifest.images.len() {
        return Err("Science Core runtime manifest contains duplicate image IDs".into());
    }
    Ok(())
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn is_image_id(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(is_sha256)
}

fn open_verified_file(path: &Path, expected: &str, label: &str) -> Result<File, String> {
    if !is_sha256(expected) {
        return Err(format!("{label} has an invalid SHA-256"));
    }
    let mut file = open_regular_file(path, label)?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|_| format!("Could not read {label}"))?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    let actual = format!("{:x}", digest.finalize());
    if actual != expected {
        return Err(format!("{label} failed SHA-256 verification"));
    }
    file.rewind()
        .map_err(|_| format!("Could not rewind {label}"))?;
    Ok(file)
}

fn open_regular_file(path: &Path, label: &str) -> Result<File, String> {
    let path_metadata = fs::symlink_metadata(path).map_err(|_| format!("{label} is missing"))?;
    validate_regular_metadata(&path_metadata, label)?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let file = options
        .open(path)
        .map_err(|_| format!("Could not open {label}"))?;
    let file_metadata = file
        .metadata()
        .map_err(|_| format!("Could not inspect {label}"))?;
    validate_regular_metadata(&file_metadata, label)?;
    if !same_file(&path_metadata, &file_metadata) {
        return Err(format!("{label} changed during validation"));
    }
    Ok(file)
}

fn validate_regular_metadata(metadata: &Metadata, label: &str) -> Result<(), String> {
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(format!("{label} must be a regular file"));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.nlink() != 1 {
            return Err(format!("{label} must not be hard-linked"));
        }
    }
    Ok(())
}

fn validate_directory_metadata(metadata: &Metadata, label: &str) -> Result<(), String> {
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(format!("{label} must be a real directory"));
    }
    Ok(())
}

fn open_real_directory(path: &Path, label: &str) -> Result<File, String> {
    let path_metadata = fs::symlink_metadata(path).map_err(|_| format!("{label} is missing"))?;
    validate_directory_metadata(&path_metadata, label)?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW);
    }
    let directory = options
        .open(path)
        .map_err(|_| format!("Could not open {label}"))?;
    let opened_metadata = directory
        .metadata()
        .map_err(|_| format!("Could not inspect {label}"))?;
    validate_directory_metadata(&opened_metadata, label)?;
    if !same_file(&path_metadata, &opened_metadata) {
        return Err(format!("{label} changed during validation"));
    }
    Ok(directory)
}

#[cfg(unix)]
fn same_file(left: &Metadata, right: &Metadata) -> bool {
    use std::os::unix::fs::MetadataExt;
    left.dev() == right.dev() && left.ino() == right.ino()
}

#[cfg(not(unix))]
fn same_file(left: &Metadata, right: &Metadata) -> bool {
    left.file_type() == right.file_type() && left.len() == right.len()
}

#[allow(dead_code)]
fn ensure_private_dir(path: &Path) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "Invalid Science Core runtime directory".to_string())?;
    let parent_before = fs::symlink_metadata(parent)
        .map_err(|_| "Invalid Science Core runtime directory parent".to_string())?;
    validate_directory_metadata(&parent_before, "Science Core runtime directory parent")?;

    match fs::create_dir(path) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(_) => return Err("Could not create Science Core runtime directory".into()),
    }

    let parent_after = fs::symlink_metadata(parent)
        .map_err(|_| "Science Core runtime directory parent changed".to_string())?;
    validate_directory_metadata(&parent_after, "Science Core runtime directory parent")?;
    if !same_file(&parent_before, &parent_after) {
        return Err("Science Core runtime directory parent changed".into());
    }

    let directory = open_real_directory(path, "Science Core runtime directory")?;
    let directory_before = directory
        .metadata()
        .map_err(|_| "Invalid Science Core runtime directory".to_string())?;
    validate_directory_metadata(&directory_before, "Science Core runtime directory")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        directory
            .set_permissions(fs::Permissions::from_mode(0o700))
            .map_err(|_| "Could not protect Science Core runtime directory".to_string())?;
    }
    let directory_after = directory
        .metadata()
        .map_err(|_| "Invalid Science Core runtime directory".to_string())?;
    validate_directory_metadata(&directory_after, "Science Core runtime directory")?;
    if !same_file(&directory_before, &directory_after) {
        return Err("Science Core runtime directory changed".into());
    }
    let path_after = fs::symlink_metadata(path)
        .map_err(|_| "Science Core runtime directory changed".to_string())?;
    validate_directory_metadata(&path_after, "Science Core runtime directory")?;
    if !same_file(&directory_after, &path_after) {
        return Err("Science Core runtime directory changed".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        if directory_after.mode() & 0o777 != 0o700
            || directory_after.permissions().mode() & 0o777 != 0o700
        {
            return Err("Science Core runtime directory is not private".into());
        }
    }
    Ok(())
}

struct RuntimePaths {
    data: PathBuf,
    session: PathBuf,
    staging: PathBuf,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ModelConfig {
    #[serde(default = "default_model_provider_id")]
    provider_id: String,
    #[serde(default = "default_model_protocol")]
    protocol: String,
    api_base: String,
    llm_model: String,
    embedding_model: String,
    #[serde(default)]
    credential_endpoint_sha256: Option<String>,
}

impl Default for ModelConfig {
    fn default() -> Self {
        Self {
            provider_id: default_model_provider_id(),
            protocol: default_model_protocol(),
            api_base: String::new(),
            llm_model: String::new(),
            embedding_model: String::new(),
            credential_endpoint_sha256: None,
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ScienceModelConfigInput {
    provider_id: String,
    protocol: String,
    api_base: String,
    llm_model: String,
    embedding_model: String,
    api_key: Option<String>,
    clear_credential: bool,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ScienceModelConfigStatus {
    provider_id: String,
    protocol: String,
    api_base: String,
    llm_model: String,
    embedding_model: String,
    credential_stored: bool,
}

fn prepare_runtime_paths(app_data: &Path) -> Result<RuntimePaths, String> {
    fs::create_dir_all(app_data)
        .map_err(|_| "Could not create the app-private Science Core root".to_string())?;
    let app_data_metadata = fs::symlink_metadata(app_data)
        .map_err(|_| "Could not inspect the app-private Science Core root".to_string())?;
    validate_directory_metadata(&app_data_metadata, "Science Core app data directory")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(app_data, fs::Permissions::from_mode(0o700))
            .map_err(|_| "Could not protect the app-private Science Core root".to_string())?;
    }

    let root = app_data.join("science-core-runtime");
    ensure_private_dir(&root)?;
    ensure_owner_marker(&root)?;
    let session = root.join("session");
    let staging = root.join("staging");
    remove_stale_owned_directory(&session)?;
    remove_stale_owned_directory(&staging)?;
    let data = root.join("data");
    ensure_private_dir(&data)?;
    ensure_private_dir(&data.join("projects"))?;
    ensure_private_dir(&session)?;
    ensure_private_dir(&staging)?;
    Ok(RuntimePaths {
        data,
        session,
        staging,
    })
}

pub(crate) fn skill_mcp_descriptor_path(app_data: &Path) -> PathBuf {
    app_data
        .join("science-core-runtime")
        .join("session")
        .join(SKILL_MCP_DESCRIPTOR_FILE)
}

fn write_skill_mcp_descriptor(
    app_data: &Path,
    session_dir: &Path,
    data_dir: &Path,
    endpoint: &str,
    token: &str,
) -> Result<PathBuf, String> {
    let path = skill_mcp_descriptor_path(app_data);
    if path.parent() != Some(session_dir) {
        return Err("Science Skill MCP descriptor path is invalid".into());
    }
    let descriptor = crate::skill_mcp_bridge::SkillMcpDescriptor {
        version: 1,
        endpoint: endpoint.to_string(),
        token: token.to_string(),
        host_projects_root: data_dir.join("projects"),
    };
    let contents = serde_json::to_vec(&descriptor)
        .map_err(|_| "Could not serialize Science Skill MCP descriptor".to_string())?;
    let temporary = session_dir.join(format!(
        ".{SKILL_MCP_DESCRIPTOR_FILE}.{}.tmp",
        crate::runtime::random_hex(8)
    ));
    let result = (|| {
        let file = write_private_new(
            &temporary,
            &contents,
            "Science Skill MCP descriptor staging file",
        )?;
        drop(file);
        if let Ok(existing) = fs::symlink_metadata(&path) {
            validate_regular_metadata(&existing, "Science Skill MCP descriptor")?;
        }
        fs::rename(&temporary, &path)
            .map_err(|_| "Could not publish Science Skill MCP descriptor".to_string())?;
        let metadata = fs::symlink_metadata(&path)
            .map_err(|_| "Could not inspect Science Skill MCP descriptor".to_string())?;
        validate_regular_metadata(&metadata, "Science Skill MCP descriptor")?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            if metadata.mode() & 0o777 != 0o600 {
                return Err("Science Skill MCP descriptor is not private".into());
            }
        }
        Ok(path.clone())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn ensure_owner_marker(root: &Path) -> Result<(), String> {
    let marker = root.join(OWNER_MARKER);
    if marker.exists() || fs::symlink_metadata(&marker).is_ok() {
        let mut file = open_regular_file(&marker, "Science Core ownership marker")?;
        let mut value = Vec::new();
        (&mut file)
            .take(128)
            .read_to_end(&mut value)
            .map_err(|_| "Could not read Science Core ownership marker".to_string())?;
        if value != OWNER_MARKER_CONTENT {
            return Err("Science Core ownership marker is invalid".into());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            file.set_permissions(fs::Permissions::from_mode(0o600))
                .map_err(|_| "Could not protect Science Core ownership marker".to_string())?;
        }
        return Ok(());
    }
    write_private_new(
        &marker,
        OWNER_MARKER_CONTENT,
        "Science Core ownership marker",
    )?;
    Ok(())
}

fn remove_stale_owned_directory(path: &Path) -> Result<(), String> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            validate_directory_metadata(&metadata, "Science Core session directory")?;
            fs::remove_dir_all(path)
                .map_err(|_| "Could not remove a stale Science Core session".to_string())
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err("Could not inspect a stale Science Core session".into()),
    }
}

fn cleanup_runtime_paths(session: &Path, staging: &Path) {
    for path in [session, staging] {
        if fs::symlink_metadata(path)
            .map(|metadata| metadata.is_dir() && !metadata.file_type().is_symlink())
            .unwrap_or(false)
        {
            let _ = fs::remove_dir_all(path);
        }
    }
}

fn write_private_new(path: &Path, contents: &[u8], label: &str) -> Result<File, String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("Invalid {label} path"))?;
    let directory = open_real_directory(parent, "Science Core private directory")?;
    let directory_before = directory
        .metadata()
        .map_err(|_| format!("Could not inspect {label} directory"))?;
    let mut options = OpenOptions::new();
    options.create_new(true).read(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let mut file = options
        .open(path)
        .map_err(|_| format!("Could not create {label}"))?;
    let initial = file
        .metadata()
        .map_err(|_| format!("Could not inspect {label}"))?;
    validate_regular_metadata(&initial, label)?;
    file.write_all(contents)
        .and_then(|_| file.sync_all())
        .map_err(|_| format!("Could not write {label}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        file.set_permissions(fs::Permissions::from_mode(0o600))
            .map_err(|_| format!("Could not protect {label}"))?;
        let protected = file
            .metadata()
            .map_err(|_| format!("Could not inspect {label}"))?;
        if protected.mode() & 0o777 != 0o600 || protected.nlink() != 1 {
            return Err(format!("{label} is not private"));
        }
    }
    let path_metadata =
        fs::symlink_metadata(path).map_err(|_| format!("{label} changed during creation"))?;
    validate_regular_metadata(&path_metadata, label)?;
    if !same_file(&initial, &path_metadata) {
        return Err(format!("{label} changed during creation"));
    }
    let directory_after = directory
        .metadata()
        .map_err(|_| format!("Could not inspect {label} directory"))?;
    if !same_file(&directory_before, &directory_after) {
        return Err(format!("{label} directory changed during creation"));
    }
    file.rewind()
        .map_err(|_| format!("Could not rewind {label}"))?;
    Ok(file)
}

fn snapshot_verified_compose(
    mut source: File,
    expected_sha256: &str,
    session_dir: &Path,
) -> Result<PathBuf, String> {
    let path = session_dir.join(COMPOSE_FILE);
    let mut snapshot = write_private_new(&path, &[], "Science Core Compose snapshot")?;
    let metadata = snapshot
        .metadata()
        .map_err(|_| "Could not inspect Science Core Compose snapshot".to_string())?;
    let result = (|| {
        let mut digest = Sha256::new();
        let mut buffer = [0_u8; 64 * 1024];
        loop {
            let count = source
                .read(&mut buffer)
                .map_err(|_| "Could not read verified Science Core Compose".to_string())?;
            if count == 0 {
                break;
            }
            digest.update(&buffer[..count]);
            snapshot
                .write_all(&buffer[..count])
                .map_err(|_| "Could not write Science Core Compose snapshot".to_string())?;
        }
        if format!("{:x}", digest.finalize()) != expected_sha256 {
            return Err("Science Core Compose failed snapshot SHA-256 verification".into());
        }
        snapshot
            .sync_all()
            .map_err(|_| "Could not finalize Science Core Compose snapshot".to_string())?;
        let path_metadata = fs::symlink_metadata(&path)
            .map_err(|_| "Science Core Compose snapshot changed".to_string())?;
        validate_regular_metadata(&path_metadata, "Science Core Compose snapshot")?;
        if !same_file(&metadata, &path_metadata) {
            return Err("Science Core Compose snapshot changed".into());
        }
        Ok(())
    })();
    if let Err(error) = result {
        remove_if_same_file(&path, &metadata);
        return Err(error);
    }
    Ok(path)
}

fn model_config_from_environment() -> Result<ModelConfig, String> {
    Ok(ModelConfig {
        provider_id: default_model_provider_id(),
        protocol: validate_model_protocol(
            &std::env::var("SPARK_AGENT_MODEL_PROTOCOL")
                .unwrap_or_else(|_| default_model_protocol()),
        )?,
        api_base: validate_model_value("OPENAI_API_BASE", 2048, true)?,
        llm_model: validate_model_value("SPARK_AGENT_LLM_MODEL", 200, false)?,
        embedding_model: validate_model_value("SPARK_AGENT_EMBEDDING_MODEL", 200, false)?,
        credential_endpoint_sha256: None,
    })
}

fn validated_model_config(config: ModelConfig) -> Result<ModelConfig, String> {
    if config
        .credential_endpoint_sha256
        .as_deref()
        .is_some_and(|value| !is_sha256(value))
    {
        return Err("Science Core model configuration is invalid".into());
    }
    Ok(ModelConfig {
        provider_id: validate_model_provider_id(&config.provider_id)?,
        protocol: validate_model_protocol(&config.protocol)?,
        api_base: validate_model_text(&config.api_base, 2048, true)?,
        llm_model: validate_model_text(&config.llm_model, 200, false)?,
        embedding_model: validate_model_text(&config.embedding_model, 200, false)?,
        credential_endpoint_sha256: config.credential_endpoint_sha256,
    })
}

fn default_model_provider_id() -> String {
    "custom".into()
}

fn default_model_protocol() -> String {
    "openai-compatible".into()
}

fn validate_model_provider_id(value: &str) -> Result<String, String> {
    let value = value.trim();
    if value.is_empty()
        || value.len() > 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
    {
        return Err("Science Core model provider is invalid".into());
    }
    Ok(value.into())
}

fn validate_model_protocol(value: &str) -> Result<String, String> {
    match value.trim() {
        "openai-compatible" => Ok("openai-compatible".into()),
        "anthropic" => Ok("anthropic".into()),
        _ => Err("Science Core model protocol is invalid".into()),
    }
}

fn model_endpoint_sha256(api_base: &str, protocol: &str) -> Option<String> {
    let parsed = reqwest::Url::parse(api_base).ok()?;
    let host = parsed.host_str()?;
    let canonical_host = if host.contains(':') {
        format!("[{host}]")
    } else {
        host.to_ascii_lowercase()
    };
    let default_port = if parsed.scheme() == "https" { 443 } else { 80 };
    let authority = match parsed.port() {
        Some(port) if port != default_port => format!("{canonical_host}:{port}"),
        _ => canonical_host,
    };
    let base_path = parsed.path().trim_end_matches('/');
    let request_path = match protocol {
        "anthropic" => "/messages",
        "openai-compatible" => "/chat/completions",
        _ => return None,
    };
    let canonical = format!(
        "{}://{}{}{}",
        parsed.scheme().to_ascii_lowercase(),
        authority,
        base_path,
        request_path
    );
    Some(format!("{:x}", Sha256::digest(canonical.as_bytes())))
}

fn model_credential_account(config: &ModelConfig) -> Option<String> {
    let identity = model_endpoint_sha256(&config.api_base, &config.protocol)?;
    (config.credential_endpoint_sha256.as_deref() == Some(identity.as_str()))
        .then(|| format!("{KEYCHAIN_ACCOUNT}:{identity}"))
}

fn validate_model_text(value: &str, maximum: usize, url: bool) -> Result<String, String> {
    let normalized = value.trim();
    if normalized.is_empty() {
        return Ok(String::new());
    }
    if normalized.len() > maximum || normalized.chars().any(char::is_control) {
        return Err("Science Core model configuration is invalid".into());
    }
    if url {
        let parsed = reqwest::Url::parse(normalized)
            .map_err(|_| "Science Core model configuration is invalid".to_string())?;
        let loopback = parsed.host_str().is_some_and(|host| {
            host == "localhost" || host.parse::<IpAddr>().is_ok_and(|ip| ip.is_loopback())
        });
        if parsed.username() != ""
            || parsed.password().is_some()
            || parsed.query().is_some()
            || parsed.fragment().is_some()
            || !matches!(parsed.scheme(), "https" | "http")
            || (parsed.scheme() == "http" && !loopback)
        {
            return Err("Science Core model configuration is invalid".into());
        }
    }
    Ok(normalized.to_string())
}

fn validate_model_value(name: &str, maximum: usize, url: bool) -> Result<String, String> {
    let value = std::env::var(name).unwrap_or_default();
    validate_model_text(&value, maximum, url)
}

fn model_config_path(app_data: &Path) -> PathBuf {
    app_data
        .join("science-core-runtime")
        .join(MODEL_CONFIG_FILE)
}

fn read_model_config(app_data: &Path) -> Result<Option<ModelConfig>, String> {
    let path = model_config_path(app_data);
    match fs::symlink_metadata(&path) {
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("Could not inspect Science Core model configuration".into()),
    }
    let mut file = open_regular_file(&path, "Science Core model configuration")?;
    let metadata = file
        .metadata()
        .map_err(|_| "Could not inspect Science Core model configuration".to_string())?;
    if metadata.len() > MAX_MODEL_CONFIG_BYTES {
        return Err("Science Core model configuration is too large".into());
    }
    let mut bytes = Vec::new();
    (&mut file)
        .take(MAX_MODEL_CONFIG_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "Could not read Science Core model configuration".to_string())?;
    if bytes.len() as u64 > MAX_MODEL_CONFIG_BYTES {
        return Err("Science Core model configuration is too large".into());
    }
    let config = serde_json::from_slice::<ModelConfig>(&bytes)
        .map_err(|_| "Science Core model configuration is invalid".to_string())?;
    validated_model_config(config).map(Some)
}

fn model_config_for_runtime(app_data: &Path) -> Result<ModelConfig, String> {
    read_model_config(app_data)?.map_or_else(model_config_from_environment, Ok)
}

fn write_model_config(app_data: &Path, config: &ModelConfig) -> Result<(), String> {
    fs::create_dir_all(app_data)
        .map_err(|_| "Could not create the app-private Science Core root".to_string())?;
    let app_data_metadata = fs::symlink_metadata(app_data)
        .map_err(|_| "Could not inspect the app-private Science Core root".to_string())?;
    validate_directory_metadata(&app_data_metadata, "Science Core app data directory")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(app_data, fs::Permissions::from_mode(0o700))
            .map_err(|_| "Could not protect the app-private Science Core root".to_string())?;
    }
    let root = app_data.join("science-core-runtime");
    ensure_private_dir(&root)?;
    ensure_owner_marker(&root)?;
    let path = model_config_path(app_data);
    if let Ok(metadata) = fs::symlink_metadata(&path) {
        validate_regular_metadata(&metadata, "Science Core model configuration")?;
    }
    let bytes = serde_json::to_vec(config)
        .map_err(|_| "Could not encode Science Core model configuration".to_string())?;
    let temporary = path.with_file_name(format!(
        ".{MODEL_CONFIG_FILE}.{}.tmp",
        crate::runtime::random_hex(8)
    ));
    write_private_new(
        &temporary,
        &bytes,
        "Science Core model configuration temporary file",
    )?;
    if let Err(error) = fs::rename(&temporary, &path) {
        let _ = fs::remove_file(&temporary);
        return Err(format!(
            "Could not save Science Core model configuration: {error}"
        ));
    }
    open_real_directory(&root, "Science Core runtime directory")?
        .sync_all()
        .map_err(|_| "Could not finalize Science Core model configuration".to_string())?;
    Ok(())
}

#[cfg(target_os = "macos")]
fn model_credential_stored(account: &str) -> bool {
    let status = CommandSpec {
        program: "/usr/bin/security",
        args: vec![
            "find-generic-password".into(),
            "-a".into(),
            account.into(),
            "-s".into(),
            KEYCHAIN_SERVICE.into(),
        ],
    };
    SystemRunner.run(&status, KEYCHAIN_TIMEOUT).is_ok()
}

#[cfg(not(target_os = "macos"))]
fn model_credential_stored(_account: &str) -> bool {
    false
}

#[cfg(target_os = "macos")]
fn save_model_credential(account: &str, value: &str) -> Result<(), String> {
    let normalized = normalize_secret(value.as_bytes())?;
    security_framework::passwords::set_generic_password(KEYCHAIN_SERVICE, account, &normalized)
        .map_err(|_| "Could not save the model credential in macOS Keychain".to_string())
}

#[cfg(not(target_os = "macos"))]
fn save_model_credential(_account: &str, _value: &str) -> Result<(), String> {
    Err("Model credentials require macOS Keychain".into())
}

#[cfg(target_os = "macos")]
fn clear_model_credential(account: &str) -> Result<(), String> {
    match security_framework::passwords::delete_generic_password(KEYCHAIN_SERVICE, account) {
        Ok(()) => Ok(()),
        Err(_) if !model_credential_stored(account) => Ok(()),
        Err(_) => Err("Could not remove the model credential from macOS Keychain".into()),
    }
}

#[cfg(not(target_os = "macos"))]
fn clear_model_credential(_account: &str) -> Result<(), String> {
    Ok(())
}

fn render_session_env(
    image_ids: &[String; 2],
    token: &str,
    data: &Path,
    secret: &Path,
    model: &ModelConfig,
) -> Result<Vec<u8>, String> {
    validate_session_token(token)?;
    let data = safe_env_path(data)?;
    let secret = safe_env_path(secret)?;
    for value in [
        &model.provider_id,
        &model.protocol,
        &model.api_base,
        &model.llm_model,
        &model.embedding_model,
    ] {
        if value.contains(['\n', '\r']) {
            return Err("Science Core model configuration is invalid".into());
        }
    }
    Ok(format!(
        "SPARK_AGENT_CORE_IMAGE_ID={}\nSPARK_AGENT_RUNTIME_IMAGE_ID={}\nSPARK_AGENT_CORE_TOKEN={}\nSPARK_AGENT_CORE_HOST_DATA_DIR={}\nSPARK_AGENT_OPENAI_API_KEY_FILE={}\nSPARK_AGENT_MODEL_PROVIDER={}\nSPARK_AGENT_MODEL_PROTOCOL={}\nOPENAI_API_BASE={}\nSPARK_AGENT_LLM_MODEL={}\nSPARK_AGENT_EMBEDDING_MODEL={}\n",
        image_ids[0],
        image_ids[1],
        token,
        data,
        secret,
        model.provider_id,
        model.protocol,
        model.api_base,
        model.llm_model,
        model.embedding_model,
    )
    .into_bytes())
}

fn safe_env_path(path: &Path) -> Result<String, String> {
    let value = path
        .to_str()
        .ok_or_else(|| "Science Core private path is invalid".to_string())?;
    if value.contains(['\n', '\r']) {
        return Err("Science Core private path is invalid".into());
    }
    Ok(value.to_string())
}

#[allow(dead_code)]
fn write_session_env(dir: &Path, token: &str) -> Result<PathBuf, String> {
    validate_session_token(token)?;
    let directory = open_real_directory(dir, "Science Core runtime directory")?;
    let directory_before = directory
        .metadata()
        .map_err(|_| "Invalid Science Core runtime directory".to_string())?;
    validate_directory_metadata(&directory_before, "Science Core runtime directory")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if directory_before.mode() & 0o777 != 0o700 {
            return Err("Science Core runtime directory is not private".into());
        }
    }

    let path = dir.join("session.env");
    let mut options = OpenOptions::new();
    options.create_new(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let mut file = options
        .open(&path)
        .map_err(|_| "Could not create Science Core session".to_string())?;
    let file_metadata = file
        .metadata()
        .map_err(|_| "Could not inspect Science Core session".to_string())?;
    validate_regular_metadata(&file_metadata, "Science Core session")?;

    let directory_after = directory
        .metadata()
        .map_err(|_| "Science Core runtime directory changed".to_string())?;
    validate_directory_metadata(&directory_after, "Science Core runtime directory")?;
    if !same_file(&directory_before, &directory_after) {
        return Err("Science Core runtime directory changed".into());
    }
    let directory_path_after = fs::symlink_metadata(dir)
        .map_err(|_| "Science Core runtime directory changed".to_string())?;
    validate_directory_metadata(&directory_path_after, "Science Core runtime directory")?;
    if !same_file(&directory_after, &directory_path_after) {
        return Err("Science Core runtime directory changed".into());
    }
    let path_metadata = fs::symlink_metadata(&path)
        .map_err(|_| "Science Core session changed during creation".to_string())?;
    validate_regular_metadata(&path_metadata, "Science Core session")?;
    if !same_file(&file_metadata, &path_metadata) {
        return Err("Science Core session changed during creation".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        file.set_permissions(fs::Permissions::from_mode(0o600))
            .map_err(|_| "Could not protect Science Core session".to_string())?;
        let protected = file
            .metadata()
            .map_err(|_| "Could not inspect Science Core session".to_string())?;
        if protected.mode() & 0o777 != 0o600 || protected.nlink() != 1 {
            return Err("Science Core session is not private".into());
        }
    }

    file.write_all(format!("SPARK_AGENT_CORE_TOKEN={token}\n").as_bytes())
        .and_then(|_| file.sync_all())
        .map_err(|_| "Could not write Science Core session".to_string())?;
    Ok(path)
}

fn snapshot_bundled_image(
    mut image: BundledImage,
    staging_dir: &Path,
) -> Result<PreparedImage, String> {
    let staging_directory = open_real_directory(staging_dir, "Science Core staging directory")?;
    let directory_before = staging_directory
        .metadata()
        .map_err(|_| "Could not inspect Science Core staging directory".to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if directory_before.mode() & 0o777 != 0o700 {
            return Err("Science Core staging directory is not private".into());
        }
    }

    let archive_name = image
        .archive
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| "Science Core archive name is invalid".to_string())?;
    let staging_path = staging_dir.join(format!(".{archive_name}.snapshot"));
    let mut options = OpenOptions::new();
    options.create_new(true).read(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let mut snapshot = options
        .open(&staging_path)
        .map_err(|_| "Could not create Science Core image snapshot".to_string())?;
    let snapshot_metadata = snapshot
        .metadata()
        .map_err(|_| "Could not inspect Science Core image snapshot".to_string())?;
    if let Err(error) = validate_regular_metadata(&snapshot_metadata, "Science Core image snapshot")
    {
        remove_if_same_file(&staging_path, &snapshot_metadata);
        return Err(error);
    }

    let result = (|| {
        let mut digest = Sha256::new();
        let mut buffer = [0_u8; 64 * 1024];
        loop {
            let count = image
                .source
                .read(&mut buffer)
                .map_err(|_| "Could not read Science Core OCI archive".to_string())?;
            if count == 0 {
                break;
            }
            digest.update(&buffer[..count]);
            snapshot
                .write_all(&buffer[..count])
                .map_err(|_| "Could not write Science Core image snapshot".to_string())?;
        }
        if format!("{:x}", digest.finalize()) != image.archive_sha256 {
            return Err("Science Core OCI archive failed snapshot SHA-256 verification".into());
        }
        snapshot
            .sync_all()
            .map_err(|_| "Could not finalize Science Core image snapshot".to_string())?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::{MetadataExt, PermissionsExt};
            snapshot
                .set_permissions(fs::Permissions::from_mode(0o600))
                .map_err(|_| "Could not protect Science Core image snapshot".to_string())?;
            let protected = snapshot
                .metadata()
                .map_err(|_| "Could not inspect Science Core image snapshot".to_string())?;
            if protected.mode() & 0o777 != 0o600 || protected.nlink() != 1 {
                return Err("Science Core image snapshot is not private".into());
            }
        }
        let path_metadata = fs::symlink_metadata(&staging_path)
            .map_err(|_| "Science Core image snapshot changed".to_string())?;
        validate_regular_metadata(&path_metadata, "Science Core image snapshot")?;
        if !same_file(&snapshot_metadata, &path_metadata) {
            return Err("Science Core image snapshot changed".into());
        }
        let directory_after = staging_directory
            .metadata()
            .map_err(|_| "Science Core staging directory changed".to_string())?;
        let directory_path_after = fs::symlink_metadata(staging_dir)
            .map_err(|_| "Science Core staging directory changed".to_string())?;
        if !same_file(&directory_before, &directory_after)
            || !same_file(&directory_after, &directory_path_after)
        {
            return Err("Science Core staging directory changed".into());
        }
        snapshot
            .rewind()
            .map_err(|_| "Could not rewind Science Core image snapshot".to_string())?;
        Ok(())
    })();

    if let Err(error) = result {
        remove_if_same_file(&staging_path, &snapshot_metadata);
        return Err(error);
    }
    fs::remove_file(&staging_path)
        .map_err(|_| "Could not unlink Science Core image snapshot".to_string())?;
    if fs::symlink_metadata(&staging_path).is_ok() {
        return Err("Science Core image snapshot path still exists".into());
    }

    Ok(PreparedImage {
        image: image.image,
        image_id: image.image_id,
        snapshot,
    })
}

fn remove_if_same_file(path: &Path, expected: &Metadata) {
    if fs::symlink_metadata(path)
        .map(|metadata| same_file(&metadata, expected))
        .unwrap_or(false)
    {
        let _ = fs::remove_file(path);
    }
}

#[allow(dead_code)]
fn load_bundled_images_with<R: CommandRunner>(
    resources: &Path,
    staging_dir: &Path,
    runner: &R,
) -> Result<LoadedImages, String> {
    let bundled = validate_bundled_resources(resources)?;
    load_verified_images_with(bundled, staging_dir, runner)
}

fn load_verified_images_with<R: CommandRunner>(
    bundled: BundledResources,
    staging_dir: &Path,
    runner: &R,
) -> Result<LoadedImages, String> {
    load_verified_image_set_with(bundled.images, staging_dir, runner, None)
}

fn load_verified_image_set_with<R: CommandRunner>(
    images: [BundledImage; 2],
    staging_dir: &Path,
    runner: &R,
    cancelled: Option<&AtomicBool>,
) -> Result<LoadedImages, String> {
    let architecture = expected_docker_architecture()?;
    let prepared = images
        .into_iter()
        .map(|image| snapshot_bundled_image(image, staging_dir))
        .collect::<Result<Vec<_>, _>>()?;

    for spec in fixed_preflight_specs() {
        if cancelled.is_some_and(|cancelled| cancelled.load(Ordering::Acquire)) {
            return Err("Science Core startup was cancelled".into());
        }
        runner.run(&spec, DOCKER_PREFLIGHT_TIMEOUT).map_err(|_| {
            "Docker Desktop preflight failed before Science Core image loading".to_string()
        })?;
    }

    let mut verified_ids = Vec::with_capacity(2);
    for image in prepared {
        if cancelled.is_some_and(|cancelled| cancelled.load(Ordering::Acquire)) {
            return Err("Science Core startup was cancelled".into());
        }
        let load_spec = fixed_image_load_spec();
        let inspect_spec = fixed_image_inspect_spec(&image);
        let image_id = image.image_id;
        runner
            .run_with_stdin(&load_spec, image.snapshot, IMAGE_LOAD_TIMEOUT)
            .map_err(|_| {
                format!(
                    "Science Core image loading failed after {} of 2 images were verified; an image may remain in Docker",
                    verified_ids.len()
                )
            })?;
        let inspected = runner
            .run(&inspect_spec, IMAGE_INSPECT_TIMEOUT)
            .map_err(|_| {
                format!(
                    "Science Core image inspection failed after {} of 2 images were verified; a loaded image may remain in Docker",
                    verified_ids.len()
                )
            })?;
        validate_inspected_image(&inspected, &image_id, architecture).map_err(|_| {
            format!(
                "Science Core image verification failed after {} of 2 images were verified; a loaded image may remain in Docker",
                verified_ids.len()
            )
        })?;
        verified_ids.push(image_id);
        if cancelled.is_some_and(|cancelled| cancelled.load(Ordering::Acquire)) {
            return Err("Science Core startup was cancelled".into());
        }
    }

    let image_ids: [String; 2] = verified_ids
        .try_into()
        .map_err(|_| "Science Core image verification was incomplete".to_string())?;
    Ok(LoadedImages { image_ids })
}

#[cfg(test)]
fn start_lifecycle_with<R: CommandRunner, C: CredentialProvider, H: HealthProbe>(
    resources: &Path,
    app_data: &Path,
    runner: &R,
    credential: &C,
    health: &H,
) -> Result<ActiveRuntime, LifecycleFailure> {
    start_lifecycle_cancellable_with(
        resources,
        app_data,
        runner,
        credential,
        health,
        &AtomicBool::new(false),
        |_| {},
    )
}

fn start_lifecycle_cancellable_with<
    R: CommandRunner,
    C: CredentialProvider,
    H: HealthProbe,
    F: Fn(ActiveRuntime),
>(
    resources: &Path,
    app_data: &Path,
    runner: &R,
    credential: &C,
    health: &H,
    cancelled: &AtomicBool,
    on_context: F,
) -> Result<ActiveRuntime, LifecycleFailure> {
    let paths = prepare_runtime_paths(app_data).map_err(|message| LifecycleFailure {
        message,
        docker_ready: false,
        cleanup: None,
    })?;
    let mut docker_ready = false;
    let mut cleanup_context = None;
    let attempt = (|| {
        let bundled = validate_bundled_resources(resources)?;
        let compose = snapshot_verified_compose(
            bundled.compose_source,
            &bundled.compose_sha256,
            &paths.session,
        )?;
        let token = crate::runtime::random_hex(32);
        validate_session_token(&token)?;
        let model = model_config_for_runtime(app_data)?;
        let secret_path = paths.session.join("model-secret");
        let secret = credential.model_secret(&model)?.unwrap_or_default();
        write_private_new(&secret_path, &secret, "Science Core model credential")?;
        let image_ids = [
            bundled.images[0].image_id.clone(),
            bundled.images[1].image_id.clone(),
        ];
        let env_bytes = render_session_env(&image_ids, &token, &paths.data, &secret_path, &model)?;
        let env_file = paths.session.join("session.env");
        write_private_new(&env_file, &env_bytes, "Science Core session environment")?;
        let mut active = ActiveRuntime {
            compose,
            env_file,
            session_dir: paths.session.clone(),
            staging_dir: paths.staging.clone(),
            endpoint: String::new(),
            token,
        };
        cleanup_context = Some(active.clone());
        on_context(active.clone());
        if cancelled.load(Ordering::Acquire) {
            return Err("Science Core startup was cancelled".into());
        }

        load_verified_image_set_with(bundled.images, &paths.staging, runner, Some(cancelled))?;
        docker_ready = true;
        if cancelled.load(Ordering::Acquire) {
            return Err("Science Core startup was cancelled".into());
        }

        runner
            .run(
                &fixed_stop_spec(&active.compose, &active.env_file),
                COMPOSE_CLEANUP_TIMEOUT,
            )
            .map_err(|_| {
                "Could not clean up the owned Science Core project before startup".to_string()
            })?;
        if cancelled.load(Ordering::Acquire) {
            return Err("Science Core startup was cancelled".into());
        }
        let start_spec = fixed_start_spec(&active.compose, &active.env_file);
        if runner
            .run_cancellable(&start_spec, COMPOSE_COMMAND_TIMEOUT, cancelled)
            .is_err()
        {
            return Err("Docker Compose could not start the owned Science Core project".into());
        }
        if cancelled.load(Ordering::Acquire) {
            return Err("Science Core startup was cancelled".into());
        }
        let port = runner
            .run(
                &fixed_port_spec(&active.compose, &active.env_file),
                COMPOSE_PORT_TIMEOUT,
            )
            .map_err(|_| "Docker Compose did not report the Science Core port".to_string())?;
        active.endpoint = parse_loopback_endpoint(&port)?;
        if cancelled.load(Ordering::Acquire) {
            return Err("Science Core startup was cancelled".into());
        }
        health.wait_ready(&active.endpoint)?;
        if cancelled.load(Ordering::Acquire) {
            return Err("Science Core startup was cancelled".into());
        }
        write_skill_mcp_descriptor(
            app_data,
            &paths.session,
            &paths.data,
            &active.endpoint,
            &active.token,
        )?;
        cleanup_context = None;
        Ok(active)
    })();

    match attempt {
        Ok(active) => Ok(active),
        Err(message) => {
            if let Some(active) = cleanup_context {
                match stop_active_with(&active, runner) {
                    Ok(()) => Err(LifecycleFailure {
                        docker_ready,
                        message,
                        cleanup: None,
                    }),
                    Err(_) => Err(LifecycleFailure {
                        docker_ready,
                        message: format!("{message}; Science Core cleanup is pending"),
                        cleanup: Some(Box::new(active)),
                    }),
                }
            } else {
                cleanup_runtime_paths(&paths.session, &paths.staging);
                Err(LifecycleFailure {
                    docker_ready,
                    message,
                    cleanup: None,
                })
            }
        }
    }
}

fn stop_active_with<R: CommandRunner>(active: &ActiveRuntime, runner: &R) -> Result<(), String> {
    // Revoke the ephemeral bridge credential even when Docker cleanup fails.
    // The remaining session files stay available for the bounded cleanup retry.
    let _ = fs::remove_file(active.session_dir.join(SKILL_MCP_DESCRIPTOR_FILE));
    let result = runner
        .run(
            &fixed_stop_spec(&active.compose, &active.env_file),
            COMPOSE_CLEANUP_TIMEOUT,
        )
        .map(|_| ())
        .map_err(|_| "Could not stop the owned Science Core project".to_string());
    if result.is_ok() {
        cleanup_runtime_paths(&active.session_dir, &active.staging_dir);
    }
    result
}

impl ScienceCoreState {
    fn status(&self) -> ScienceCoreStatus {
        self.inner.lock().unwrap().status.clone()
    }

    fn begin_start(&self) -> Option<(u64, Arc<AtomicBool>, Arc<WorkerCompletion>)> {
        let mut lifecycle = self.inner.lock().unwrap();
        if lifecycle.active.is_some()
            || matches!(lifecycle.status.state, "starting" | "ready" | "stopping")
        {
            return None;
        }
        lifecycle.generation = lifecycle.generation.wrapping_add(1);
        lifecycle.status = ScienceCoreStatus::starting();
        let cancellation = Arc::new(AtomicBool::new(false));
        let worker = Arc::new(WorkerCompletion::default());
        lifecycle.start_cancel = Some(cancellation.clone());
        lifecycle.start_worker = Some(worker.clone());
        Some((lifecycle.generation, cancellation, worker))
    }

    fn register_start_context(&self, generation: u64, active: ActiveRuntime) {
        let mut lifecycle = self.inner.lock().unwrap();
        if lifecycle.generation == generation
            && matches!(lifecycle.status.state, "starting" | "stopping")
        {
            lifecycle.active = Some(active);
        }
    }

    fn finish_start(
        &self,
        generation: u64,
        result: Result<ActiveRuntime, LifecycleFailure>,
    ) -> Option<ActiveRuntime> {
        let mut lifecycle = self.inner.lock().unwrap();
        if lifecycle.generation != generation {
            return match result {
                Ok(active) => Some(active),
                Err(failure) => failure.cleanup.map(|active| *active),
            };
        }
        lifecycle.start_cancel = None;
        let cancelled = lifecycle.status.state == "stopping";
        match result {
            Ok(active) => {
                lifecycle.active = Some(active.clone());
                if cancelled {
                    Some(active)
                } else {
                    lifecycle.status = ScienceCoreStatus::ready(active.endpoint.clone());
                    None
                }
            }
            Err(failure) => {
                let cleanup = failure.cleanup.map(|active| *active);
                lifecycle.active = cleanup.clone();
                if cancelled {
                    if let Some(active) = cleanup {
                        Some(active)
                    } else {
                        lifecycle.active = None;
                        lifecycle.status = ScienceCoreStatus::stopped();
                        None
                    }
                } else {
                    lifecycle.status =
                        ScienceCoreStatus::failed(failure.message, failure.docker_ready);
                    None
                }
            }
        }
    }

    fn mark_unavailable(&self, message: &str) {
        let worker = {
            let mut lifecycle = self.inner.lock().unwrap();
            if lifecycle.status.state == "ready" {
                return;
            }
            lifecycle.start_cancel = None;
            lifecycle.active = None;
            lifecycle.status = ScienceCoreStatus {
                state: "unavailable",
                endpoint: None,
                docker_ready: false,
                compose_ready: false,
                message: Some(sanitize_message(message)),
            };
            lifecycle.start_worker.take()
        };
        if let Some(worker) = worker {
            worker.finish();
        }
    }

    fn begin_stop(&self) -> Option<(u64, ActiveRuntime)> {
        let mut lifecycle = self.inner.lock().unwrap();
        if lifecycle.status.state == "starting" {
            if let Some(cancellation) = &lifecycle.start_cancel {
                cancellation.store(true, Ordering::Release);
            }
            lifecycle.status = ScienceCoreStatus::stopping();
            return None;
        }
        if lifecycle.status.state == "stopping" {
            return None;
        }
        let active = lifecycle.active.clone()?;
        lifecycle.generation = lifecycle.generation.wrapping_add(1);
        lifecycle.status = ScienceCoreStatus::stopping();
        Some((lifecycle.generation, active))
    }

    fn finish_stop(&self, generation: u64, result: Result<(), String>) {
        let mut lifecycle = self.inner.lock().unwrap();
        if lifecycle.generation != generation || lifecycle.status.state != "stopping" {
            return;
        }
        lifecycle.status = match result {
            Ok(()) => {
                lifecycle.active = None;
                lifecycle.start_cancel = None;
                ScienceCoreStatus::stopped()
            }
            Err(error) => ScienceCoreStatus::failed(error, true),
        };
    }

    fn begin_retry_cleanup(&self) -> Option<(u64, ActiveRuntime)> {
        let mut lifecycle = self.inner.lock().unwrap();
        if !matches!(lifecycle.status.state, "failed" | "ready") {
            return None;
        }
        let active = lifecycle.active.clone()?;
        lifecycle.generation = lifecycle.generation.wrapping_add(1);
        lifecycle.status = ScienceCoreStatus::stopping();
        Some((lifecycle.generation, active))
    }

    fn begin_exit_cleanup(&self) -> Option<(u64, ActiveRuntime)> {
        let mut lifecycle = self.inner.lock().unwrap();
        if let Some(cancellation) = &lifecycle.start_cancel {
            cancellation.store(true, Ordering::Release);
        }
        let active = lifecycle.active.clone()?;
        lifecycle.status = ScienceCoreStatus::stopping();
        Some((lifecycle.generation, active))
    }

    fn begin_exit_wait(&self) -> Option<Arc<WorkerCompletion>> {
        let mut lifecycle = self.inner.lock().unwrap();
        if let Some(cancellation) = &lifecycle.start_cancel {
            cancellation.store(true, Ordering::Release);
        }
        lifecycle.status = ScienceCoreStatus::stopping();
        lifecycle.start_worker.clone()
    }

    fn finish_start_worker(&self, worker: &Arc<WorkerCompletion>) {
        worker.finish();
        let mut lifecycle = self.inner.lock().unwrap();
        if lifecycle
            .start_worker
            .as_ref()
            .is_some_and(|current| Arc::ptr_eq(current, worker))
        {
            lifecycle.start_worker = None;
        }
    }

    fn connection(&self) -> Result<ScienceCoreConnection, String> {
        let lifecycle = self.inner.lock().unwrap();
        let active = lifecycle
            .active
            .as_ref()
            .filter(|_| lifecycle.status.state == "ready")
            .ok_or_else(|| "Science Core is not ready".to_string())?;
        Ok(ScienceCoreConnection {
            endpoint: active.endpoint.clone(),
            token: active.token.clone(),
        })
    }
}

fn schedule_start(app: tauri::AppHandle) {
    use tauri::Manager;

    let state = app.state::<ScienceCoreState>().inner().clone();
    #[cfg(any(not(target_os = "macos"), debug_assertions))]
    {
        state.mark_unavailable("Science Core packaged runtime is unavailable in this build");
        let _ = app;
    }
    #[cfg(all(target_os = "macos", not(debug_assertions)))]
    {
        if let Some((generation, active)) = state.begin_retry_cleanup() {
            let retry_app = app.clone();
            let retry_state = state.clone();
            tauri::async_runtime::spawn(async move {
                let result = tauri::async_runtime::spawn_blocking(move || {
                    stop_active_with(&active, &SystemRunner)
                })
                .await
                .unwrap_or_else(|_| Err("Science Core cleanup task failed".into()));
                let retry = result.is_ok();
                retry_state.finish_stop(generation, result);
                if retry {
                    schedule_start(retry_app);
                }
            });
            return;
        }
        let Some((generation, cancellation, worker)) = state.begin_start() else {
            return;
        };
        let resources = match app.path().resource_dir() {
            Ok(path) if path.join(RESOURCE_DIRECTORY).is_dir() => path,
            _ => {
                state.mark_unavailable(UNAVAILABLE_MESSAGE);
                return;
            }
        };
        let app_data = match app.path().app_data_dir() {
            Ok(path) => path,
            Err(_) => {
                let _ = state.finish_start(
                    generation,
                    Err(LifecycleFailure {
                        message: "Science Core app data directory is unavailable".into(),
                        docker_ready: false,
                        cleanup: None,
                    }),
                );
                state.finish_start_worker(&worker);
                return;
            }
        };
        tauri::async_runtime::spawn(async move {
            let context_state = state.clone();
            let result = tauri::async_runtime::spawn_blocking(move || {
                start_lifecycle_cancellable_with(
                    &resources,
                    &app_data,
                    &SystemRunner,
                    &MacKeychainCredential,
                    &SystemHealthProbe,
                    &cancellation,
                    |active| context_state.register_start_context(generation, active),
                )
            })
            .await
            .unwrap_or_else(|_| {
                Err(LifecycleFailure {
                    message: "Science Core startup task failed".into(),
                    docker_ready: false,
                    cleanup: None,
                })
            });
            if let Some(active) = state.finish_start(generation, result) {
                let cleanup = tauri::async_runtime::spawn_blocking(move || {
                    stop_active_with(&active, &SystemRunner)
                })
                .await
                .unwrap_or_else(|_| Err("Science Core cleanup task failed".into()));
                state.finish_stop(generation, cleanup);
            }
            state.finish_start_worker(&worker);
        });
    }
}

fn schedule_stop(state: ScienceCoreState) {
    let Some((generation, active)) = state.begin_stop() else {
        return;
    };
    tauri::async_runtime::spawn(async move {
        let result =
            tauri::async_runtime::spawn_blocking(move || stop_active_with(&active, &SystemRunner))
                .await
                .unwrap_or_else(|_| Err("Science Core cleanup task failed".into()));
        state.finish_stop(generation, result);
    });
}

pub fn bootstrap(app: tauri::AppHandle) {
    schedule_start(app);
}

fn stop_for_exit_with<R: CommandRunner>(
    state: &ScienceCoreState,
    runner: &R,
    wait_timeout: Duration,
) -> bool {
    if let Some(worker) = state.begin_exit_wait() {
        if !worker.wait(wait_timeout) {
            return false;
        }
    }
    if let Some((generation, active)) = state.begin_exit_cleanup() {
        let result = stop_active_with(&active, runner);
        state.finish_stop(generation, result);
    }
    true
}

pub fn stop_for_exit(state: &ScienceCoreState) {
    let _ = stop_for_exit_with(state, &SystemRunner, EXIT_WORKER_WAIT_TIMEOUT);
}

#[tauri::command]
pub fn science_core_status(state: tauri::State<'_, ScienceCoreState>) -> ScienceCoreStatus {
    state.status()
}

#[tauri::command]
pub fn science_core_connection(
    state: tauri::State<'_, ScienceCoreState>,
) -> Result<ScienceCoreConnection, String> {
    state.connection()
}

pub(crate) fn is_canonical_project_uuid(project_id: &str) -> bool {
    let id_bytes = project_id.as_bytes();
    id_bytes.len() == 36
        && id_bytes.iter().enumerate().all(|(index, byte)| {
            if matches!(index, 8 | 13 | 18 | 23) {
                *byte == b'-'
            } else {
                byte.is_ascii_digit() || matches!(byte, b'a'..=b'f')
            }
        })
}

fn derive_science_project_host_path(app_data: &Path, project_id: &str) -> Result<PathBuf, String> {
    if !is_canonical_project_uuid(project_id) {
        return Err("Science project id must be a canonical UUID".into());
    }

    let runtime_root = app_data.join("science-core-runtime");
    let data_root = runtime_root.join("data");
    let projects_root = data_root.join("projects");
    let project_path = projects_root.join(project_id);
    for (path, label) in [
        (app_data, "Science Core app data directory"),
        (runtime_root.as_path(), "Science Core runtime directory"),
        (data_root.as_path(), "Science Core data directory"),
        (projects_root.as_path(), "Science Core projects directory"),
        (project_path.as_path(), "Science project directory"),
    ] {
        open_real_directory(path, label)?;
    }
    Ok(project_path)
}

#[tauri::command]
pub fn science_project_host_path(
    app: tauri::AppHandle,
    project_id: String,
) -> Result<String, String> {
    use tauri::Manager;

    let app_data = app
        .path()
        .app_data_dir()
        .map_err(|_| "Science Core app data directory is unavailable".to_string())?;
    derive_science_project_host_path(&app_data, &project_id)
        .map(|path| path.to_string_lossy().into_owned())
}

#[tauri::command]
pub fn science_core_retry(
    app: tauri::AppHandle,
    state: tauri::State<'_, ScienceCoreState>,
) -> ScienceCoreStatus {
    schedule_start(app);
    state.status()
}

#[tauri::command]
pub fn science_core_stop(state: tauri::State<'_, ScienceCoreState>) -> ScienceCoreStatus {
    let owned = state.inner().clone();
    schedule_stop(owned.clone());
    owned.status()
}

#[tauri::command]
pub fn science_model_config(app: tauri::AppHandle) -> Result<ScienceModelConfigStatus, String> {
    use tauri::Manager;

    let app_data = app
        .path()
        .app_data_dir()
        .map_err(|_| "Science Core app data directory is unavailable".to_string())?;
    let config = match read_model_config(&app_data)? {
        Some(config) => config,
        None => model_config_from_environment()?,
    };
    let credential_stored =
        model_credential_account(&config).is_some_and(|account| model_credential_stored(&account));
    Ok(ScienceModelConfigStatus {
        provider_id: config.provider_id,
        protocol: config.protocol,
        api_base: config.api_base,
        llm_model: config.llm_model,
        embedding_model: config.embedding_model,
        credential_stored,
    })
}

#[tauri::command]
pub fn science_model_config_save(
    app: tauri::AppHandle,
    input: ScienceModelConfigInput,
) -> Result<ScienceModelConfigStatus, String> {
    use tauri::Manager;

    let mut config = validated_model_config(ModelConfig {
        provider_id: input.provider_id,
        protocol: input.protocol,
        api_base: input.api_base,
        llm_model: input.llm_model,
        embedding_model: input.embedding_model,
        credential_endpoint_sha256: None,
    })?;
    if config.api_base.is_empty() != config.llm_model.is_empty() {
        return Err("A model endpoint and model name must be configured together".into());
    }
    if input.clear_credential && input.api_key.is_some() {
        return Err("Choose either a new model credential or credential removal".into());
    }
    let app_data = app
        .path()
        .app_data_dir()
        .map_err(|_| "Science Core app data directory is unavailable".to_string())?;
    let previous = read_model_config(&app_data)?;
    let endpoint_identity = model_endpoint_sha256(&config.api_base, &config.protocol);
    let endpoint_account = endpoint_identity
        .as_ref()
        .map(|identity| format!("{KEYCHAIN_ACCOUNT}:{identity}"));
    if let Some(api_key) = input.api_key.as_deref() {
        let account = endpoint_account
            .as_deref()
            .ok_or_else(|| "A model endpoint is required before saving a credential".to_string())?;
        save_model_credential(account, api_key)?;
        config.credential_endpoint_sha256 = endpoint_identity;
    } else if input.clear_credential {
        let clear_account = previous
            .as_ref()
            .and_then(model_credential_account)
            .or(endpoint_account);
        if let Some(account) = clear_account.as_deref() {
            clear_model_credential(account)?;
        }
        config.credential_endpoint_sha256 = None;
    } else if let (Some(identity), Some(account)) = (endpoint_identity, endpoint_account.as_deref())
    {
        if model_credential_stored(account) {
            config.credential_endpoint_sha256 = Some(identity);
        }
    }
    write_model_config(&app_data, &config)?;
    let credential_stored =
        model_credential_account(&config).is_some_and(|account| model_credential_stored(&account));
    Ok(ScienceModelConfigStatus {
        provider_id: config.provider_id,
        protocol: config.protocol,
        api_base: config.api_base,
        llm_model: config.llm_model,
        embedding_model: config.embedding_model,
        credential_stored,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;
    use std::sync::Mutex;

    struct Fixture {
        outer: PathBuf,
        resources: PathBuf,
        root: PathBuf,
        staging: PathBuf,
    }

    impl Fixture {
        fn new() -> Self {
            let outer = std::env::temp_dir().join(format!(
                "spark-science-core-test-{}",
                crate::runtime::random_hex(12)
            ));
            let resources = outer.join("resources");
            let root = resources.join(RESOURCE_DIRECTORY);
            fs::create_dir_all(&root).unwrap();
            fs::write(root.join(COMPOSE_FILE), b"services: {}\n").unwrap();
            fs::write(root.join(CORE_ARCHIVE), b"core OCI fixture\n").unwrap();
            fs::write(root.join(RUNTIME_ARCHIVE), b"runtime OCI fixture\n").unwrap();
            let staging = outer.join("staging");
            ensure_private_dir(&staging).unwrap();
            let fixture = Self {
                outer,
                resources,
                root,
                staging,
            };
            fixture.write_manifest(None);
            fixture
        }

        fn write_manifest(&self, replacement: Option<serde_json::Value>) {
            let manifest = replacement.unwrap_or_else(|| {
                serde_json::json!({
                    "schemaVersion": MANIFEST_SCHEMA_VERSION,
                    "composeSha256": digest_path(&self.root.join(COMPOSE_FILE)),
                    "images": [
                        {
                            "archive": CORE_ARCHIVE,
                            "image": CORE_IMAGE,
                            "imageId": format!("sha256:{}", "1".repeat(64)),
                            "sha256": digest_path(&self.root.join(CORE_ARCHIVE)),
                        },
                        {
                            "archive": RUNTIME_ARCHIVE,
                            "image": RUNTIME_IMAGE,
                            "imageId": format!("sha256:{}", "2".repeat(64)),
                            "sha256": digest_path(&self.root.join(RUNTIME_ARCHIVE)),
                        }
                    ]
                })
            });
            fs::write(
                self.root.join(MANIFEST_FILE),
                serde_json::to_vec(&manifest).unwrap(),
            )
            .unwrap();
        }
    }

    #[test]
    fn derives_only_an_existing_app_private_science_project() {
        let outer = std::env::temp_dir().join(format!(
            "spark-science-project-path-test-{}",
            crate::runtime::random_hex(12)
        ));
        let app_data = outer.join("app-data");
        let project_id = "2d45e34b-c26b-45eb-bb70-894b32ae5f7f";
        let project_path = app_data
            .join("science-core-runtime/data/projects")
            .join(project_id);
        fs::create_dir_all(&project_path).unwrap();

        assert_eq!(
            derive_science_project_host_path(&app_data, project_id).unwrap(),
            project_path
        );
        assert!(project_path.starts_with(&app_data));
        fs::remove_dir_all(outer).unwrap();
    }

    #[test]
    fn rejects_invalid_or_missing_science_project_paths() {
        let outer = std::env::temp_dir().join(format!(
            "spark-science-project-path-test-{}",
            crate::runtime::random_hex(12)
        ));
        let app_data = outer.join("app-data");
        fs::create_dir_all(app_data.join("science-core-runtime/data/projects")).unwrap();

        for invalid in [
            "../2d45e34b-c26b-45eb-bb70-894b32ae5f7f",
            "2D45E34B-C26B-45EB-BB70-894B32AE5F7F",
            "project-1",
            "",
        ] {
            assert!(derive_science_project_host_path(&app_data, invalid).is_err());
        }
        assert!(derive_science_project_host_path(
            &app_data,
            "2d45e34b-c26b-45eb-bb70-894b32ae5f7f"
        )
        .is_err());
        fs::remove_dir_all(outer).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn rejects_symlinked_science_project_paths() {
        use std::os::unix::fs::symlink;

        let outer = std::env::temp_dir().join(format!(
            "spark-science-project-path-test-{}",
            crate::runtime::random_hex(12)
        ));
        let app_data = outer.join("app-data");
        let projects = app_data.join("science-core-runtime/data/projects");
        let outside = outer.join("outside");
        let project_id = "2d45e34b-c26b-45eb-bb70-894b32ae5f7f";
        fs::create_dir_all(&projects).unwrap();
        fs::create_dir_all(&outside).unwrap();
        symlink(&outside, projects.join(project_id)).unwrap();

        assert!(derive_science_project_host_path(&app_data, project_id).is_err());
        fs::remove_dir_all(outer).unwrap();
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.outer);
        }
    }

    fn digest_path(path: &Path) -> String {
        let mut file = File::open(path).unwrap();
        let mut digest = Sha256::new();
        let mut buffer = [0_u8; 17];
        loop {
            let count = file.read(&mut buffer).unwrap();
            if count == 0 {
                break;
            }
            digest.update(&buffer[..count]);
        }
        format!("{:x}", digest.finalize())
    }

    #[test]
    fn validates_complete_real_resource_fixture() {
        let fixture = Fixture::new();
        let bundled = validate_bundled_resources(&fixture.resources).unwrap();
        assert_eq!(bundled.root, fixture.root);
        assert_eq!(bundled.compose, fixture.root.join(COMPOSE_FILE));
        assert_eq!(bundled.images[0].archive, fixture.root.join(CORE_ARCHIVE));
        assert_eq!(bundled.images[0].image, CORE_IMAGE);
        assert_eq!(
            bundled.images[0].image_id,
            format!("sha256:{}", "1".repeat(64))
        );
        assert_eq!(
            bundled.images[0].archive_sha256,
            digest_path(&fixture.root.join(CORE_ARCHIVE))
        );
        assert_eq!(
            bundled.images[1].archive,
            fixture.root.join(RUNTIME_ARCHIVE)
        );
        assert_eq!(bundled.images[1].image, RUNTIME_IMAGE);
    }

    #[test]
    fn missing_resource_files_fail_closed() {
        for missing in [MANIFEST_FILE, COMPOSE_FILE, CORE_ARCHIVE, RUNTIME_ARCHIVE] {
            let fixture = Fixture::new();
            fs::remove_file(fixture.root.join(missing)).unwrap();
            assert!(
                validate_bundled_resources(&fixture.resources).is_err(),
                "{missing}"
            );
        }
    }

    #[test]
    fn modified_resource_hash_fails_closed() {
        let fixture = Fixture::new();
        fs::write(fixture.root.join(CORE_ARCHIVE), b"modified archive").unwrap();
        let error = validate_bundled_resources(&fixture.resources).unwrap_err();
        assert!(error.contains("SHA-256 verification"));
    }

    #[test]
    fn extra_archive_fails_closed() {
        let fixture = Fixture::new();
        fs::write(fixture.root.join("unexpected.oci.tar"), b"extra").unwrap();
        assert!(validate_bundled_resources(&fixture.resources).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn symlinked_resource_fails_closed() {
        use std::os::unix::fs::symlink;

        let fixture = Fixture::new();
        let target = fixture.outer.join("outside-compose.yaml");
        fs::write(&target, b"services: {}\n").unwrap();
        fs::remove_file(fixture.root.join(COMPOSE_FILE)).unwrap();
        symlink(target, fixture.root.join(COMPOSE_FILE)).unwrap();
        assert!(validate_bundled_resources(&fixture.resources).is_err());
    }

    #[test]
    fn strict_manifest_rejects_unknown_fields_bad_hashes_and_wrong_images() {
        let cases = [
            serde_json::json!({
                "schemaVersion": 1,
                "composeSha256": "0".repeat(64),
                "images": [],
                "unexpected": true,
            }),
            serde_json::json!({
                "schemaVersion": 1,
                "composeSha256": "NOT-A-SHA",
                "images": [
                    {"archive": CORE_ARCHIVE, "image": CORE_IMAGE, "imageId": format!("sha256:{}", "1".repeat(64)), "sha256": "0".repeat(64)},
                    {"archive": RUNTIME_ARCHIVE, "image": RUNTIME_IMAGE, "imageId": format!("sha256:{}", "2".repeat(64)), "sha256": "0".repeat(64)}
                ]
            }),
            serde_json::json!({
                "schemaVersion": 1,
                "composeSha256": "0".repeat(64),
                "images": [
                    {"archive": CORE_ARCHIVE, "image": "attacker/latest:latest", "imageId": format!("sha256:{}", "1".repeat(64)), "sha256": "0".repeat(64)},
                    {"archive": RUNTIME_ARCHIVE, "image": RUNTIME_IMAGE, "imageId": format!("sha256:{}", "2".repeat(64)), "sha256": "0".repeat(64)}
                ]
            }),
        ];
        for manifest in cases {
            let fixture = Fixture::new();
            fixture.write_manifest(Some(manifest));
            assert!(validate_bundled_resources(&fixture.resources).is_err());
        }
    }

    #[test]
    fn strict_manifest_rejects_missing_noncanonical_and_duplicate_image_ids() {
        let fixture = Fixture::new();
        let valid = serde_json::json!({
            "schemaVersion": MANIFEST_SCHEMA_VERSION,
            "composeSha256": digest_path(&fixture.root.join(COMPOSE_FILE)),
            "images": [
                {
                    "archive": CORE_ARCHIVE,
                    "image": CORE_IMAGE,
                    "imageId": format!("sha256:{}", "1".repeat(64)),
                    "sha256": digest_path(&fixture.root.join(CORE_ARCHIVE)),
                },
                {
                    "archive": RUNTIME_ARCHIVE,
                    "image": RUNTIME_IMAGE,
                    "imageId": format!("sha256:{}", "2".repeat(64)),
                    "sha256": digest_path(&fixture.root.join(RUNTIME_ARCHIVE)),
                }
            ]
        });

        let mut missing = valid.clone();
        missing["images"][0]
            .as_object_mut()
            .unwrap()
            .remove("imageId");
        let mut uppercase = valid.clone();
        uppercase["images"][0]["imageId"] =
            serde_json::Value::String(format!("sha256:{}", "A".repeat(64)));
        let mut bad_prefix = valid.clone();
        bad_prefix["images"][0]["imageId"] =
            serde_json::Value::String(format!("sha512:{}", "1".repeat(64)));
        let mut duplicate = valid.clone();
        duplicate["images"][1]["imageId"] = duplicate["images"][0]["imageId"].clone();

        for manifest in [missing, uppercase, bad_prefix, duplicate] {
            fixture.write_manifest(Some(manifest));
            assert!(validate_bundled_resources(&fixture.resources).is_err());
        }
    }

    struct ScriptedRunner {
        calls: Mutex<Vec<(CommandSpec, Duration)>>,
        responses: Mutex<VecDeque<Result<String, String>>>,
        stdin_payloads: Mutex<Vec<Vec<u8>>>,
        staging_dir: PathBuf,
        replace_on_first_call: Mutex<Option<(PathBuf, Vec<u8>)>>,
    }

    impl ScriptedRunner {
        fn new(staging_dir: &Path, responses: Vec<Result<String, String>>) -> Self {
            Self {
                calls: Mutex::new(Vec::new()),
                responses: Mutex::new(responses.into()),
                stdin_payloads: Mutex::new(Vec::new()),
                staging_dir: staging_dir.to_path_buf(),
                replace_on_first_call: Mutex::new(None),
            }
        }

        fn replace_on_first_call(self, path: PathBuf, replacement: Vec<u8>) -> Self {
            *self.replace_on_first_call.lock().unwrap() = Some((path, replacement));
            self
        }

        fn next_response(&self, spec: &CommandSpec, timeout: Duration) -> Result<String, String> {
            self.calls.lock().unwrap().push((spec.clone(), timeout));
            if let Some((path, replacement)) = self.replace_on_first_call.lock().unwrap().take() {
                fs::remove_file(&path).unwrap();
                fs::write(path, replacement).unwrap();
            }
            self.responses
                .lock()
                .unwrap()
                .pop_front()
                .unwrap_or_else(|| Err("unexpected runner call".into()))
        }
    }

    impl CommandRunner for ScriptedRunner {
        fn run(&self, spec: &CommandSpec, timeout: Duration) -> Result<String, String> {
            self.next_response(spec, timeout)
        }

        fn run_with_stdin(
            &self,
            spec: &CommandSpec,
            mut stdin: File,
            timeout: Duration,
        ) -> Result<String, String> {
            assert!(fs::read_dir(&self.staging_dir).unwrap().next().is_none());
            let mut payload = Vec::new();
            stdin.read_to_end(&mut payload).unwrap();
            self.stdin_payloads.lock().unwrap().push(payload);
            self.next_response(spec, timeout)
        }
    }

    struct FixedCredential(Option<Vec<u8>>);

    impl CredentialProvider for FixedCredential {
        fn model_secret(&self, _model: &ModelConfig) -> Result<Option<Vec<u8>>, String> {
            Ok(self.0.clone())
        }
    }

    struct FixedHealth {
        result: Result<(), String>,
        endpoints: Mutex<Vec<String>>,
    }

    #[derive(Clone)]
    struct EventRunner {
        events: Arc<Mutex<Vec<&'static str>>>,
    }

    impl CommandRunner for EventRunner {
        fn run(&self, spec: &CommandSpec, _timeout: Duration) -> Result<String, String> {
            assert_eq!(spec.args[0..3], ["compose", "--project-name", PROJECT_NAME]);
            assert_eq!(spec.args[7], "down");
            self.events.lock().unwrap().push("down");
            Ok(String::new())
        }

        fn run_with_stdin(
            &self,
            spec: &CommandSpec,
            _stdin: File,
            timeout: Duration,
        ) -> Result<String, String> {
            self.run(spec, timeout)
        }
    }

    impl FixedHealth {
        fn ready() -> Self {
            Self {
                result: Ok(()),
                endpoints: Mutex::new(Vec::new()),
            }
        }

        fn failing(message: &str) -> Self {
            Self {
                result: Err(message.into()),
                endpoints: Mutex::new(Vec::new()),
            }
        }
    }

    impl HealthProbe for FixedHealth {
        fn wait_ready(&self, endpoint: &str) -> Result<(), String> {
            self.endpoints.lock().unwrap().push(endpoint.into());
            self.result.clone()
        }
    }

    fn successful_load_responses() -> Vec<Result<String, String>> {
        let architecture = expected_docker_architecture().unwrap();
        vec![
            Ok(String::new()),
            Ok(String::new()),
            Ok(String::new()),
            Ok("Loaded image\n".into()),
            Ok(format!(
                "sha256:{}\tlinux\t{architecture}\n",
                "1".repeat(64)
            )),
            Ok("Loaded image\n".into()),
            Ok(format!(
                "sha256:{}\tlinux\t{architecture}\n",
                "2".repeat(64)
            )),
        ]
    }

    fn test_active(root: &Path, endpoint: &str) -> ActiveRuntime {
        ensure_private_dir(root).unwrap();
        let session = root.join("session");
        let staging = root.join("staging");
        ensure_private_dir(&session).unwrap();
        ensure_private_dir(&staging).unwrap();
        let compose = session.join(COMPOSE_FILE);
        let env_file = session.join("session.env");
        write_private_new(&compose, b"services: {}\n", "test compose").unwrap();
        write_private_new(&env_file, b"TEST=1\n", "test env").unwrap();
        ActiveRuntime {
            compose,
            env_file,
            session_dir: session,
            staging_dir: staging,
            endpoint: endpoint.into(),
            token: "f".repeat(64),
        }
    }

    #[test]
    fn lifecycle_uses_private_files_fixed_project_and_retains_only_data() {
        let fixture = Fixture::new();
        let app_data = fixture.outer.join("app-data");
        let staging = app_data.join("science-core-runtime/staging");
        let mut responses = successful_load_responses();
        responses.extend([
            Ok(String::new()),
            Ok(String::new()),
            Ok("127.0.0.1:49152\n".into()),
            Ok(String::new()),
        ]);
        let runner = ScriptedRunner::new(&staging, responses).replace_on_first_call(
            fixture.root.join(COMPOSE_FILE),
            b"replaced resource after snapshot\n".to_vec(),
        );
        let health = FixedHealth::ready();
        let active = start_lifecycle_with(
            &fixture.resources,
            &app_data,
            &runner,
            &FixedCredential(Some(b"super-secret-model-key".to_vec())),
            &health,
        )
        .unwrap();

        assert_eq!(active.endpoint, "http://127.0.0.1:49152");
        assert_eq!(
            fs::read(&active.compose).unwrap(),
            b"services: {}\n",
            "Docker must use the verified private snapshot"
        );
        validate_session_token(&active.token).unwrap();
        assert_eq!(
            health.endpoints.into_inner().unwrap(),
            ["http://127.0.0.1:49152"]
        );
        let env = fs::read_to_string(&active.env_file).unwrap();
        assert_eq!(env.lines().count(), 10);
        assert!(env.contains(&format!(
            "SPARK_AGENT_CORE_IMAGE_ID=sha256:{}",
            "1".repeat(64)
        )));
        assert!(env.contains(&format!(
            "SPARK_AGENT_RUNTIME_IMAGE_ID=sha256:{}",
            "2".repeat(64)
        )));
        assert!(env.contains(&format!("SPARK_AGENT_CORE_TOKEN={}", active.token)));
        assert!(env.contains("SPARK_AGENT_MODEL_PROVIDER=custom"));
        assert!(env.contains("SPARK_AGENT_MODEL_PROTOCOL=openai-compatible"));
        assert!(!env.contains("super-secret-model-key"));
        assert_eq!(
            fs::read(active.session_dir.join("model-secret")).unwrap(),
            b"super-secret-model-key"
        );
        let descriptor_path = skill_mcp_descriptor_path(&app_data);
        let descriptor: crate::skill_mcp_bridge::SkillMcpDescriptor =
            serde_json::from_slice(&fs::read(&descriptor_path).unwrap()).unwrap();
        assert_eq!(descriptor.version, 1);
        assert_eq!(descriptor.endpoint, active.endpoint);
        assert_eq!(descriptor.token, active.token);
        assert_eq!(
            descriptor.host_projects_root,
            app_data.join("science-core-runtime/data/projects")
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::{MetadataExt, PermissionsExt};
            assert_eq!(
                fs::metadata(&active.session_dir)
                    .unwrap()
                    .permissions()
                    .mode()
                    & 0o777,
                0o700
            );
            assert_eq!(
                fs::metadata(&active.env_file).unwrap().permissions().mode() & 0o777,
                0o600
            );
            let compose_metadata = fs::metadata(&active.compose).unwrap();
            assert_eq!(compose_metadata.permissions().mode() & 0o777, 0o600);
            assert_eq!(compose_metadata.nlink(), 1);
            assert_eq!(
                fs::metadata(&descriptor_path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }

        let data = app_data.join("science-core-runtime/data");
        fs::write(data.join("persistent"), b"keep").unwrap();
        stop_active_with(&active, &runner).unwrap();
        assert_eq!(fs::read(data.join("persistent")).unwrap(), b"keep");
        assert!(!descriptor_path.exists());
        assert!(!app_data.join("science-core-runtime/session").exists());
        assert!(!staging.exists());

        let calls = runner.calls.into_inner().unwrap();
        assert_eq!(calls.len(), 11);
        let compose = app_data.join("science-core-runtime/session/compose.yaml");
        let env_file = app_data.join("science-core-runtime/session/session.env");
        assert_eq!(calls[7].0, fixed_stop_spec(&compose, &env_file));
        assert_eq!(calls[8].0, fixed_start_spec(&compose, &env_file));
        assert_eq!(calls[9].0, fixed_port_spec(&compose, &env_file));
        assert_eq!(calls[10].0, fixed_stop_spec(&compose, &env_file));
        assert!(calls.iter().all(|(call, _)| !call
            .args
            .iter()
            .any(|argument| argument.contains("other-project"))));
    }

    #[test]
    fn empty_keychain_secret_keeps_local_core_startable() {
        let fixture = Fixture::new();
        let app_data = fixture.outer.join("app-data");
        let staging = app_data.join("science-core-runtime/staging");
        let mut responses = successful_load_responses();
        responses.extend([
            Ok(String::new()),
            Ok(String::new()),
            Ok("127.0.0.1:49153\n".into()),
        ]);
        let runner = ScriptedRunner::new(&staging, responses);
        let active = start_lifecycle_with(
            &fixture.resources,
            &app_data,
            &runner,
            &FixedCredential(None),
            &FixedHealth::ready(),
        )
        .unwrap();
        assert!(fs::read(active.session_dir.join("model-secret"))
            .unwrap()
            .is_empty());
    }

    #[test]
    fn lifecycle_failures_are_bounded_redacted_and_clean_private_state() {
        let fixture = Fixture::new();
        let app_data = fixture.outer.join("missing-docker-data");
        let staging = app_data.join("science-core-runtime/staging");
        let runner = ScriptedRunner::new(
            &staging,
            vec![Err("raw docker secret".into()), Ok(String::new())],
        );
        let error = start_lifecycle_with(
            &fixture.resources,
            &app_data,
            &runner,
            &FixedCredential(None),
            &FixedHealth::ready(),
        )
        .unwrap_err();
        assert!(error.message.contains("preflight failed"));
        assert!(!error.message.contains("raw docker secret"));
        assert!(!staging.exists());
        assert_eq!(runner.calls.into_inner().unwrap().len(), 2);

        let app_data = fixture.outer.join("health-failure-data");
        let staging = app_data.join("science-core-runtime/staging");
        let mut responses = successful_load_responses();
        responses.extend([
            Ok(String::new()),
            Ok(String::new()),
            Ok("127.0.0.1:49154\n".into()),
            Ok(String::new()),
        ]);
        let runner = ScriptedRunner::new(&staging, responses);
        let error = start_lifecycle_with(
            &fixture.resources,
            &app_data,
            &runner,
            &FixedCredential(None),
            &FixedHealth::failing("health contract rejected"),
        )
        .unwrap_err();
        assert_eq!(error.message, "health contract rejected");
        assert!(error.docker_ready);
        assert!(!staging.exists());
        let calls = runner.calls.into_inner().unwrap();
        assert_eq!(calls.len(), 11);
        assert_eq!(calls[7].0, calls[10].0);
    }

    #[test]
    fn lifecycle_state_retry_stop_and_connection_are_idempotent_and_secret_free() {
        let state = ScienceCoreState::default();
        let (generation, _, _) = state.begin_start().unwrap();
        assert!(state.begin_start().is_none());
        let _ = state.finish_start(
            generation,
            Err(LifecycleFailure {
                message: "failed".into(),
                docker_ready: false,
                cleanup: None,
            }),
        );
        assert_eq!(state.status().state, "failed");
        assert!(state.connection().is_err());
        assert!(state.begin_start().is_some());

        let state = ScienceCoreState::default();
        let (generation, _, _) = state.begin_start().unwrap();
        let _ = state.finish_start(
            generation,
            Ok(ActiveRuntime {
                compose: PathBuf::from("/fixed/compose.yaml"),
                env_file: PathBuf::from("/private/session.env"),
                session_dir: PathBuf::from("/private/session"),
                staging_dir: PathBuf::from("/private/staging"),
                endpoint: "http://127.0.0.1:49155".into(),
                token: "f".repeat(64),
            }),
        );
        assert_eq!(state.connection().unwrap().token, "f".repeat(64));
        let serialized = serde_json::to_string(&state.status()).unwrap();
        assert!(!serialized.contains(&"f".repeat(64)));
        let (restart_generation, restart_active) = state.begin_retry_cleanup().unwrap();
        assert_eq!(restart_active.endpoint, "http://127.0.0.1:49155");
        assert_eq!(state.status().state, "stopping");
        state.finish_stop(restart_generation, Ok(()));
        assert_eq!(state.status().state, "stopped");
        assert!(state.begin_start().is_some());
    }

    #[test]
    fn starting_stop_cancels_and_late_start_is_bounded_to_stopped() {
        let fixture = Fixture::new();
        let state = ScienceCoreState::default();
        let (generation, cancellation, _) = state.begin_start().unwrap();
        let active = test_active(&fixture.outer.join("race"), "http://127.0.0.1:49156");
        state.register_start_context(generation, active.clone());

        assert!(state.begin_stop().is_none());
        assert!(cancellation.load(Ordering::Acquire));
        assert_eq!(state.status().state, "stopping");
        let cleanup = state.finish_start(generation, Ok(active)).unwrap();
        let runner = ScriptedRunner::new(&cleanup.staging_dir, vec![Ok(String::new())]);
        stop_active_with(&cleanup, &runner).unwrap();
        state.finish_stop(generation, Ok(()));

        assert_eq!(state.status().state, "stopped");
        assert_eq!(
            state.status().message.as_deref(),
            Some("Science Core was stopped by the user")
        );
        assert!(state.connection().is_err());
        assert!(!cleanup.session_dir.exists());
    }

    #[test]
    fn failed_down_preserves_context_for_retry_and_exit_during_stopping() {
        let fixture = Fixture::new();
        let state = ScienceCoreState::default();
        let (generation, _, _) = state.begin_start().unwrap();
        let active = test_active(&fixture.outer.join("retry"), "http://127.0.0.1:49157");
        let _ = state.finish_start(generation, Ok(active.clone()));
        let (stop_generation, cleanup) = state.begin_stop().unwrap();
        let descriptor_path = cleanup.session_dir.join(SKILL_MCP_DESCRIPTOR_FILE);
        write_private_new(&descriptor_path, b"ephemeral", "test descriptor").unwrap();
        let failing = ScriptedRunner::new(&cleanup.staging_dir, vec![Err("private".into())]);
        let error = stop_active_with(&cleanup, &failing).unwrap_err();
        state.finish_stop(stop_generation, Err(error));
        assert_eq!(state.status().state, "failed");
        assert!(cleanup.session_dir.exists());
        assert!(!descriptor_path.exists());

        let (retry_generation, retry_cleanup) = state.begin_retry_cleanup().unwrap();
        let successful = ScriptedRunner::new(&retry_cleanup.staging_dir, vec![Ok(String::new())]);
        stop_active_with(&retry_cleanup, &successful).unwrap();
        state.finish_stop(retry_generation, Ok(()));
        assert_eq!(state.status().state, "stopped");
        assert!(!retry_cleanup.session_dir.exists());

        let state = ScienceCoreState::default();
        let (generation, _, _) = state.begin_start().unwrap();
        let active = test_active(&fixture.outer.join("exit"), "http://127.0.0.1:49158");
        let _ = state.finish_start(generation, Ok(active));
        let _ = state.begin_stop().unwrap();
        let (exit_generation, exit_cleanup) = state.begin_exit_cleanup().unwrap();
        let runner = ScriptedRunner::new(&exit_cleanup.staging_dir, vec![Ok(String::new())]);
        stop_active_with(&exit_cleanup, &runner).unwrap();
        state.finish_stop(exit_generation, Ok(()));
        assert_eq!(state.status().state, "stopped");
    }

    #[test]
    fn cancelled_start_always_runs_fixed_cleanup_without_loading_images() {
        let fixture = Fixture::new();
        let app_data = fixture.outer.join("cancelled-data");
        let staging = app_data.join("science-core-runtime/staging");
        let runner = ScriptedRunner::new(&staging, vec![Ok(String::new())]);
        let cancelled = AtomicBool::new(true);
        let error = start_lifecycle_cancellable_with(
            &fixture.resources,
            &app_data,
            &runner,
            &FixedCredential(None),
            &FixedHealth::ready(),
            &cancelled,
            |_| {},
        )
        .unwrap_err();
        assert_eq!(error.message, "Science Core startup was cancelled");
        assert!(error.cleanup.is_none());
        assert!(!staging.exists());
        let calls = runner.calls.into_inner().unwrap();
        assert_eq!(calls.len(), 1);
        assert_eq!(
            calls[0].0.args[0..3],
            ["compose", "--project-name", PROJECT_NAME]
        );
        assert_eq!(
            &calls[0].0.args[7..],
            ["down", "--timeout", "10", "--volumes", "--remove-orphans"]
        );
    }

    #[test]
    fn exit_waits_for_up_completion_before_the_final_owned_down() {
        let fixture = Fixture::new();
        let state = ScienceCoreState::default();
        let (generation, cancellation, worker) = state.begin_start().unwrap();
        let active = test_active(&fixture.outer.join("exit-up-race"), "");
        state.register_start_context(generation, active.clone());
        let events = Arc::new(Mutex::new(Vec::new()));
        let runner = EventRunner {
            events: events.clone(),
        };
        let worker_state = state.clone();
        let worker_runner = runner.clone();
        let worker_thread = thread::spawn(move || {
            while !cancellation.load(Ordering::Acquire) {
                thread::sleep(Duration::from_millis(1));
            }
            events.lock().unwrap().push("up-complete");
            stop_active_with(&active, &worker_runner).unwrap();
            let _ = worker_state.finish_start(
                generation,
                Err(LifecycleFailure {
                    message: "Science Core startup was cancelled".into(),
                    docker_ready: true,
                    cleanup: None,
                }),
            );
            worker_state.finish_start_worker(&worker);
        });

        assert!(stop_for_exit_with(&state, &runner, Duration::from_secs(2)));
        worker_thread.join().unwrap();
        assert_eq!(
            runner.events.lock().unwrap().as_slice(),
            ["up-complete", "down"]
        );
        assert_eq!(state.status().state, "stopped");
    }

    #[test]
    fn offline_loader_uses_exact_preflight_load_and_inspect_sequence() {
        let fixture = Fixture::new();
        let architecture = expected_docker_architecture().unwrap();
        let core_id = format!("sha256:{}", "1".repeat(64));
        let runtime_id = format!("sha256:{}", "2".repeat(64));
        let runner = ScriptedRunner::new(
            &fixture.staging,
            vec![
                Ok(String::new()),
                Ok(String::new()),
                Ok(String::new()),
                Ok("Loaded image\n".into()),
                Ok(format!("{core_id}\tlinux\t{architecture}\n")),
                Ok("Loaded image\n".into()),
                Ok(format!("{runtime_id}\tlinux\t{architecture}\n")),
            ],
        );

        let loaded =
            load_bundled_images_with(&fixture.resources, &fixture.staging, &runner).unwrap();
        assert_eq!(loaded.image_ids, [core_id, runtime_id]);

        let calls = runner.calls.into_inner().unwrap();
        assert_eq!(calls.len(), 7);
        assert_eq!(calls[0].0, fixed_preflight_specs()[0]);
        assert_eq!(calls[1].0, fixed_preflight_specs()[1]);
        assert_eq!(calls[2].0, fixed_preflight_specs()[2]);
        assert!(calls[..3]
            .iter()
            .all(|(_, timeout)| *timeout == DOCKER_PREFLIGHT_TIMEOUT));
        assert_eq!(calls[3].0.args, ["image", "load"]);
        assert_eq!(calls[3].1, IMAGE_LOAD_TIMEOUT);
        assert_eq!(
            calls[4].0.args,
            [
                "image",
                "inspect",
                "--format",
                "{{.Id}}\t{{.Os}}\t{{.Architecture}}",
                CORE_IMAGE
            ]
        );
        assert_eq!(calls[4].1, IMAGE_INSPECT_TIMEOUT);
        assert_eq!(calls[5].0.args, ["image", "load"]);
        assert_eq!(
            calls[6].0.args.last().map(String::as_str),
            Some(RUNTIME_IMAGE)
        );
        assert!(!calls.iter().any(|(call, _)| call
            .args
            .iter()
            .any(|argument| argument == "pull" || argument == "build")));
        assert_eq!(
            runner.stdin_payloads.into_inner().unwrap(),
            [
                b"core OCI fixture\n".to_vec(),
                b"runtime OCI fixture\n".to_vec()
            ]
        );
    }

    #[test]
    fn invalid_resources_also_make_zero_docker_calls() {
        let fixture = Fixture::new();
        fs::remove_file(fixture.root.join(CORE_ARCHIVE)).unwrap();
        let runner = ScriptedRunner::new(&fixture.staging, Vec::new());
        assert!(load_bundled_images_with(&fixture.resources, &fixture.staging, &runner).is_err());
        assert!(runner.calls.into_inner().unwrap().is_empty());
    }

    #[test]
    fn bad_archive_hash_makes_zero_docker_calls() {
        for archive in [CORE_ARCHIVE, RUNTIME_ARCHIVE] {
            let fixture = Fixture::new();
            fs::write(fixture.root.join(archive), b"tampered archive").unwrap();
            let runner = ScriptedRunner::new(&fixture.staging, Vec::new());
            assert!(
                load_bundled_images_with(&fixture.resources, &fixture.staging, &runner).is_err(),
                "{archive}"
            );
            assert!(runner.calls.into_inner().unwrap().is_empty(), "{archive}");
            assert!(
                fs::read_dir(&fixture.staging).unwrap().next().is_none(),
                "{archive}"
            );
        }
    }

    #[test]
    fn replacing_original_paths_after_snapshot_does_not_change_loader_stdin() {
        let fixture = Fixture::new();
        let architecture = expected_docker_architecture().unwrap();
        let core_id = format!("sha256:{}", "1".repeat(64));
        let runtime_id = format!("sha256:{}", "2".repeat(64));
        let runner = ScriptedRunner::new(
            &fixture.staging,
            vec![
                Ok(String::new()),
                Ok(String::new()),
                Ok(String::new()),
                Ok(String::new()),
                Ok(format!("{core_id}\tlinux\t{architecture}\n")),
                Ok(String::new()),
                Ok(format!("{runtime_id}\tlinux\t{architecture}\n")),
            ],
        )
        .replace_on_first_call(
            fixture.root.join(CORE_ARCHIVE),
            b"replacement after snapshot".to_vec(),
        );

        load_bundled_images_with(&fixture.resources, &fixture.staging, &runner).unwrap();
        assert_eq!(
            runner.stdin_payloads.into_inner().unwrap(),
            [
                b"core OCI fixture\n".to_vec(),
                b"runtime OCI fixture\n".to_vec()
            ]
        );
        assert!(fs::read_dir(&fixture.staging).unwrap().next().is_none());
    }

    #[test]
    fn load_errors_report_that_an_image_may_remain_without_echoing_runner_output() {
        let fixture = Fixture::new();
        let runner = ScriptedRunner::new(
            &fixture.staging,
            vec![
                Ok(String::new()),
                Ok(String::new()),
                Ok(String::new()),
                Err("secret raw docker output".into()),
            ],
        );
        let error =
            load_bundled_images_with(&fixture.resources, &fixture.staging, &runner).unwrap_err();
        assert!(error.contains("an image may remain in Docker"));
        assert!(!error.contains("secret raw docker output"));
    }

    #[test]
    fn offline_loader_rejects_wrong_id_os_and_architecture() {
        let fixture = Fixture::new();
        let architecture = expected_docker_architecture().unwrap();
        let wrong_values = [
            format!("sha256:{}\tlinux\t{architecture}\n", "f".repeat(64)),
            format!("sha256:{}\tdarwin\t{architecture}\n", "1".repeat(64)),
            format!("sha256:{}\tlinux\ts390x\n", "1".repeat(64)),
        ];
        for inspected in wrong_values {
            let runner = ScriptedRunner::new(
                &fixture.staging,
                vec![
                    Ok(String::new()),
                    Ok(String::new()),
                    Ok(String::new()),
                    Ok("Loaded image\n".into()),
                    Ok(inspected),
                ],
            );
            let error = load_bundled_images_with(&fixture.resources, &fixture.staging, &runner)
                .unwrap_err();
            assert!(error.contains("0 of 2 images were verified"));
            assert!(error.contains("may remain in Docker"));
            assert_eq!(runner.calls.into_inner().unwrap().len(), 5);
        }
    }

    #[test]
    fn accepts_only_dynamic_ipv4_loopback() {
        assert_eq!(
            parse_loopback_endpoint("127.0.0.1:49152\n").unwrap(),
            "http://127.0.0.1:49152"
        );
        for value in [
            "0.0.0.0:8765",
            "[::1]:8765",
            "127.0.0.1:0",
            "127.0.0.1:8765\nnope",
        ] {
            assert!(parse_loopback_endpoint(value).is_err(), "{value}");
        }
    }

    #[test]
    fn token_validator_accepts_only_canonical_256_bit_hex() {
        let token = crate::runtime::random_hex(32);
        assert!(validate_session_token(&token).is_ok());
        for value in [
            "0",
            "A123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "g123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        ] {
            assert!(validate_session_token(value).is_err(), "{value}");
        }
    }

    #[test]
    fn command_specs_are_fixed_scoped_and_offline() {
        let compose = Path::new("/fixed/compose.yaml");
        let env = Path::new("/private/session.env");
        let preflight = fixed_preflight_specs();
        assert_eq!(
            preflight[0].args,
            ["version", "--format", "{{.Server.Version}}"]
        );
        assert_eq!(preflight[1].args, ["compose", "version", "--short"]);
        assert_eq!(
            preflight[2].args,
            ["info", "--format", "{{.ServerVersion}}"]
        );

        let start = fixed_start_spec(compose, env);
        assert_eq!(start.program, "docker");
        assert_eq!(
            start.args[0..3],
            ["compose", "--project-name", PROJECT_NAME]
        );
        assert!(start
            .args
            .windows(2)
            .any(|pair| pair == ["--pull", "never"]));
        assert_eq!(
            &start.args[7..],
            [
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                "--wait-timeout",
                COMPOSE_WAIT_TIMEOUT_SECONDS,
            ]
        );
        assert!(!start.args.iter().any(|arg| arg == "build"));

        let stop = fixed_stop_spec(compose, env);
        assert_eq!(
            &stop.args[7..],
            ["down", "--timeout", "10", "--volumes", "--remove-orphans"]
        );

        let port = fixed_port_spec(compose, env);
        assert_eq!(&port.args[7..], ["port", SERVICE_NAME, "8765"]);
    }

    #[test]
    fn private_session_file_is_create_new_and_permission_bounded() {
        let fixture = Fixture::new();
        let runtime = fixture.outer.join("runtime");
        ensure_private_dir(&runtime).unwrap();
        let token = "a".repeat(64);
        let env = write_session_env(&runtime, &token).unwrap();
        assert_eq!(
            fs::read_to_string(&env).unwrap(),
            format!("SPARK_AGENT_CORE_TOKEN={token}\n")
        );
        assert!(write_session_env(&runtime, &token).is_err());
        #[cfg(unix)]
        {
            use std::os::unix::fs::{MetadataExt, PermissionsExt};
            assert_eq!(
                fs::metadata(&runtime).unwrap().permissions().mode() & 0o777,
                0o700
            );
            let metadata = fs::metadata(env).unwrap();
            assert_eq!(metadata.permissions().mode() & 0o777, 0o600);
            assert_eq!(metadata.nlink(), 1);
        }
    }

    #[test]
    fn status_is_non_panicking_unavailable_and_secret_free() {
        let status = ScienceCoreState::default().status();
        assert_eq!(status.state, "unavailable");
        assert_eq!(status.endpoint, None);
        assert!(!status.docker_ready);
        assert!(!status.compose_ready);
        let serialized = serde_json::to_value(status).unwrap();
        assert!(serialized.get("token").is_none());
        assert_eq!(serialized["message"], UNAVAILABLE_MESSAGE);
    }

    #[test]
    fn model_config_round_trip_is_private_and_excludes_credentials() {
        let root = std::env::temp_dir().join(format!(
            "spark-model-config-test-{}",
            crate::runtime::random_hex(12)
        ));
        let config = validated_model_config(ModelConfig {
            provider_id: "openai".into(),
            protocol: "openai-compatible".into(),
            api_base: " https://api.example.com/v1 ".into(),
            llm_model: " research-model ".into(),
            embedding_model: String::new(),
            credential_endpoint_sha256: None,
        })
        .unwrap();
        write_model_config(&root, &config).unwrap();
        let active_session = root.join("science-core-runtime/session");
        ensure_private_dir(&active_session).unwrap();
        fs::write(active_session.join("active"), b"keep").unwrap();
        write_model_config(&root, &config).unwrap();
        assert_eq!(fs::read(active_session.join("active")).unwrap(), b"keep");
        let stored = read_model_config(&root).unwrap().unwrap();
        assert_eq!(stored.provider_id, "openai");
        assert_eq!(stored.protocol, "openai-compatible");
        assert_eq!(stored.api_base, "https://api.example.com/v1");
        assert_eq!(stored.llm_model, "research-model");
        let bytes = fs::read(model_config_path(&root)).unwrap();
        assert!(!String::from_utf8_lossy(&bytes).contains("apiKey"));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(model_config_path(&root))
                    .unwrap()
                    .permissions()
                    .mode()
                    & 0o777,
                0o600
            );
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn model_config_migrates_legacy_fields_and_rejects_unknown_protocols() {
        let legacy: ModelConfig = serde_json::from_value(serde_json::json!({
            "apiBase": "https://api.example.com/v1",
            "llmModel": "research-model",
            "embeddingModel": ""
        }))
        .unwrap();
        assert_eq!(legacy.provider_id, "custom");
        assert_eq!(legacy.protocol, "openai-compatible");
        assert!(validated_model_config(ModelConfig {
            protocol: "future-protocol".into(),
            ..legacy.clone()
        })
        .is_err());
        assert!(validated_model_config(ModelConfig {
            provider_id: "Invalid Provider".into(),
            ..legacy
        })
        .is_err());

        let status = ScienceModelConfigStatus {
            provider_id: "anthropic".into(),
            protocol: "anthropic".into(),
            api_base: "https://api.anthropic.com/v1".into(),
            llm_model: "claude-test".into(),
            embedding_model: String::new(),
            credential_stored: true,
        };
        let serialized = serde_json::to_value(status).unwrap();
        assert_eq!(serialized["providerId"], "anthropic");
        assert_eq!(serialized["protocol"], "anthropic");
        assert!(serialized.get("apiKey").is_none());
    }

    #[test]
    fn model_config_rejects_insecure_remote_http_and_embedded_credentials() {
        for endpoint in [
            "http://api.example.com/v1",
            "https://user:pass@example.com/v1",
        ] {
            assert!(validate_model_text(endpoint, 2048, true).is_err());
        }
        assert_eq!(
            validate_model_text("http://127.0.0.1:11434/v1", 2048, true).unwrap(),
            "http://127.0.0.1:11434/v1"
        );
    }

    #[test]
    fn model_credentials_are_bound_to_one_canonical_endpoint() {
        let endpoint = "https://api.example.com/v1";
        let identity = model_endpoint_sha256(endpoint, "openai-compatible").unwrap();
        assert_eq!(
            identity,
            model_endpoint_sha256("https://API.EXAMPLE.COM:443/v1/", "openai-compatible").unwrap()
        );
        assert_ne!(
            identity,
            model_endpoint_sha256(endpoint, "anthropic").unwrap()
        );
        let config = ModelConfig {
            provider_id: "openai".into(),
            protocol: "openai-compatible".into(),
            api_base: endpoint.into(),
            llm_model: "research-model".into(),
            embedding_model: String::new(),
            credential_endpoint_sha256: Some(identity.clone()),
        };
        assert_eq!(
            model_credential_account(&config).unwrap(),
            format!("{KEYCHAIN_ACCOUNT}:{identity}")
        );
        assert!(model_credential_account(&ModelConfig {
            api_base: "https://other.example.com/v1".into(),
            ..config
        })
        .is_none());
    }

    #[cfg(unix)]
    #[test]
    fn unreadable_model_config_fails_closed_instead_of_using_environment() {
        use std::os::unix::fs::PermissionsExt;

        let root = std::env::temp_dir().join(format!(
            "spark-model-config-unreadable-{}",
            crate::runtime::random_hex(12)
        ));
        let config = ModelConfig::default();
        write_model_config(&root, &config).unwrap();
        let path = model_config_path(&root);
        fs::set_permissions(&path, fs::Permissions::from_mode(0o000)).unwrap();
        assert!(read_model_config(&root).is_err());
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn docker_program_resolution_uses_the_first_existing_candidate() {
        use std::os::unix::fs::PermissionsExt;

        let root = std::env::temp_dir().join(format!(
            "spark-docker-resolution-{}",
            crate::runtime::random_hex(12)
        ));
        fs::create_dir_all(&root).unwrap();
        let missing = root.join("missing-docker");
        let not_executable = root.join("not-executable-docker");
        let bundled = root.join("docker");
        fs::write(&not_executable, b"test").unwrap();
        fs::write(&bundled, b"test").unwrap();
        fs::set_permissions(&bundled, fs::Permissions::from_mode(0o700)).unwrap();
        assert_eq!(
            first_existing_program([missing, not_executable, bundled.clone()]),
            Some(bundled),
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn system_runner_drains_large_stderr_without_exposing_it() {
        let spec = CommandSpec {
            program: "/bin/sh",
            args: vec![
                "-c".into(),
                "dd if=/dev/zero bs=131072 count=1 2>/dev/null 1>&2; printf ok".into(),
            ],
        };
        assert_eq!(
            SystemRunner.run(&spec, Duration::from_secs(5)).unwrap(),
            "ok"
        );
    }

    #[cfg(unix)]
    #[test]
    fn system_runner_cancellation_kills_and_waits_for_the_inflight_process() {
        let spec = CommandSpec {
            program: "/bin/sh",
            args: vec!["-c".into(), "while :; do :; done".into()],
        };
        let cancelled = Arc::new(AtomicBool::new(false));
        let signal = cancelled.clone();
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(30));
            signal.store(true, Ordering::Release);
        });
        let started = Instant::now();
        assert!(SystemRunner
            .run_cancellable(&spec, Duration::from_secs(5), &cancelled)
            .is_err());
        assert!(started.elapsed() < Duration::from_secs(1));
    }

    #[cfg(unix)]
    #[test]
    fn system_runner_bounds_large_output_timeout_and_redacts_output() {
        let spec = CommandSpec {
            program: "/bin/sh",
            args: vec![
                "-c".into(),
                "while :; do printf 'secret-output-that-must-not-leak'; done".into(),
            ],
        };
        let started = Instant::now();
        let error = SystemRunner
            .run(&spec, Duration::from_millis(100))
            .unwrap_err();
        assert!(started.elapsed() < Duration::from_secs(2));
        assert_eq!(error, "Docker command timed out");
        assert!(!error.contains("secret-output"));
    }
}
