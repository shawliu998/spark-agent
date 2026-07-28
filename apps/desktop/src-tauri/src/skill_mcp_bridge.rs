use std::ffi::OsStr;
use std::fs::{self, OpenOptions};
use std::io::{BufRead, Read, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

const FLAG: &str = "--spark-skill-mcp";
const DESCRIPTOR_ENV: &str = "SPARK_SKILL_MCP_DESCRIPTOR";
const PROTOCOL_VERSION: &str = "2025-11-25";
const TOOL_NAME: &str = "remember_verified_evidence";
const MAX_FRAME_BYTES: usize = 64 * 1024;
const MAX_DESCRIPTOR_BYTES: u64 = 8 * 1024;
const HTTP_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct SkillMcpDescriptor {
    pub(crate) version: u32,
    pub(crate) endpoint: String,
    pub(crate) token: String,
    pub(crate) host_projects_root: PathBuf,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RememberVerifiedEvidenceInput {
    evidence_id: String,
    expected_source_content_hash: String,
    expected_quote_hash: String,
}

pub fn is_skill_mcp_invocation<I, S>(args_after_program: I) -> bool
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let mut args = args_after_program.into_iter();
    args.next().is_some_and(|arg| arg.as_ref() == FLAG) && args.next().is_none()
}

pub fn run_skill_mcp_stdio() -> i32 {
    let stdin = std::io::stdin();
    let stdout = std::io::stdout();
    match serve(stdin.lock(), stdout.lock()) {
        Ok(()) => 0,
        Err(()) => 1,
    }
}

fn serve<R: BufRead, W: Write>(mut reader: R, mut writer: W) -> Result<(), ()> {
    let mut initialization_state = 0_u8;
    loop {
        let mut frame = Vec::new();
        let read = reader
            .by_ref()
            .take((MAX_FRAME_BYTES + 1) as u64)
            .read_until(b'\n', &mut frame)
            .map_err(|_| ())?;
        if read == 0 {
            return Ok(());
        }
        if frame.len() > MAX_FRAME_BYTES {
            write_frame(
                &mut writer,
                &rpc_error(Value::Null, -32_700, "Invalid protocol frame"),
            )?;
            return Err(());
        }
        while matches!(frame.last(), Some(b'\n' | b'\r')) {
            frame.pop();
        }
        let request = match serde_json::from_slice::<Value>(&frame) {
            Ok(request) => request,
            Err(_) => {
                write_frame(
                    &mut writer,
                    &rpc_error(Value::Null, -32_700, "Invalid JSON-RPC frame"),
                )?;
                continue;
            }
        };
        let Some(object) = request.as_object() else {
            write_frame(
                &mut writer,
                &rpc_error(Value::Null, -32_600, "Invalid JSON-RPC request"),
            )?;
            continue;
        };
        if object.get("jsonrpc").and_then(Value::as_str) != Some("2.0") {
            write_frame(
                &mut writer,
                &rpc_error(
                    object.get("id").cloned().unwrap_or(Value::Null),
                    -32_600,
                    "Invalid JSON-RPC request",
                ),
            )?;
            continue;
        }
        let id = object.get("id").cloned();
        let Some(method) = object.get("method").and_then(Value::as_str) else {
            write_frame(
                &mut writer,
                &rpc_error(
                    id.unwrap_or(Value::Null),
                    -32_600,
                    "Invalid JSON-RPC request",
                ),
            )?;
            continue;
        };
        let params = object.get("params").cloned().unwrap_or_else(|| json!({}));
        let response = match method {
            "initialize" if valid_initialize_params(&params) => {
                initialization_state = 1;
                id.map(|id| {
                    rpc_result(
                        id,
                        json!({
                            "protocolVersion": PROTOCOL_VERSION,
                            "capabilities": {"tools": {"listChanged": false}},
                            "serverInfo": {"name": "spark-skill-mcp", "version": "1"}
                        }),
                    )
                })
            }
            "initialize" => id.map(|id| {
                rpc_error(id, -32_602, "Unsupported MCP client or protocol version")
            }),
            "notifications/initialized" if initialization_state == 1 => {
                initialization_state = 2;
                None
            }
            "notifications/initialized" => None,
            "ping" => id.map(|id| rpc_result(id, json!({}))),
            "tools/list" if initialization_state == 2 => id.map(|id| {
                rpc_result(
                    id,
                    json!({
                        "tools": [{
                            "name": TOOL_NAME,
                            "description": "Propose one verified evidence span as a reviewable project Research Memory candidate.",
                            "inputSchema": tool_input_schema()
                        }]
                    }),
                )
            }),
            "tools/call" if initialization_state == 2 => id.map(|id| {
                rpc_result(
                    id,
                    match call_tool(params) {
                        Ok(result) => result,
                        Err(message) => tool_error(message),
                    },
                )
            }),
            "tools/list" | "tools/call" => id.map(|id| {
                rpc_error(id, -32_002, "MCP server has not been initialized")
            }),
            _ => id.map(|id| rpc_error(id, -32_601, "Method not found")),
        };
        if let Some(response) = response {
            write_frame(&mut writer, &response)?;
        }
    }
}

fn valid_initialize_params(params: &Value) -> bool {
    params.get("protocolVersion").and_then(Value::as_str) == Some(PROTOCOL_VERSION)
        && params
            .get("clientInfo")
            .and_then(|info| info.get("name"))
            .and_then(Value::as_str)
            == Some("opencode")
        && params
            .get("clientInfo")
            .and_then(|info| info.get("version"))
            .and_then(Value::as_str)
            == Some("1.17.13")
}

fn write_frame(writer: &mut impl Write, value: &Value) -> Result<(), ()> {
    serde_json::to_writer(&mut *writer, value).map_err(|_| ())?;
    writer.write_all(b"\n").map_err(|_| ())?;
    writer.flush().map_err(|_| ())
}

fn rpc_result(id: Value, result: Value) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "result": result})
}

fn rpc_error(id: Value, code: i32, message: &str) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": {"code": code, "message": message}
    })
}

fn tool_error(message: &'static str) -> Value {
    json!({
        "content": [{"type": "text", "text": message}],
        "isError": true
    })
}

fn tool_input_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "evidenceId": {"type": "string", "minLength": 1, "maxLength": 200},
            "expectedSourceContentHash": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$"
            },
            "expectedQuoteHash": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$"
            }
        },
        "required": [
            "evidenceId",
            "expectedSourceContentHash",
            "expectedQuoteHash"
        ],
        "additionalProperties": false
    })
}

fn call_tool(params: Value) -> Result<Value, &'static str> {
    let input = parse_tool_input(&params)?;
    let descriptor_path = std::env::var_os(DESCRIPTOR_ENV)
        .map(PathBuf::from)
        .ok_or("The capability connection is unavailable.")?;
    let cwd = std::env::current_dir().map_err(|_| "The research project is unavailable.")?;
    call_tool_with_validated_input(input, &descriptor_path, &cwd)
}

#[cfg(test)]
fn call_tool_with_context(
    params: Value,
    descriptor_path: &Path,
    cwd: &Path,
) -> Result<Value, &'static str> {
    let input = parse_tool_input(&params)?;
    call_tool_with_validated_input(input, descriptor_path, cwd)
}

fn parse_tool_input(params: &Value) -> Result<RememberVerifiedEvidenceInput, &'static str> {
    let Some(params) = params.as_object() else {
        return Err("The capability request is invalid.");
    };
    if params.len() != 2
        || params.get("name").and_then(Value::as_str) != Some(TOOL_NAME)
        || !params.contains_key("arguments")
    {
        return Err("The capability request is invalid.");
    }
    let input: RememberVerifiedEvidenceInput = serde_json::from_value(params["arguments"].clone())
        .map_err(|_| "The capability input is invalid.")?;
    validate_tool_input(&input)?;
    Ok(input)
}

fn call_tool_with_validated_input(
    input: RememberVerifiedEvidenceInput,
    descriptor_path: &Path,
    cwd: &Path,
) -> Result<Value, &'static str> {
    let descriptor = load_descriptor(descriptor_path)?;
    let project_id = derive_project_id(&descriptor.host_projects_root, cwd)?;
    invoke_core(&descriptor, project_id, &input)?;
    Ok(json!({
        "content": [{
            "type": "text",
            "text": "The verified evidence Memory candidate was proposed."
        }],
        "isError": false
    }))
}

fn validate_tool_input(input: &RememberVerifiedEvidenceInput) -> Result<(), &'static str> {
    if input.evidence_id.is_empty()
        || input.evidence_id.len() > 200
        || input
            .evidence_id
            .bytes()
            .any(|byte| !byte.is_ascii_graphic())
        || !is_lower_sha256(&input.expected_source_content_hash)
        || !is_lower_sha256(&input.expected_quote_hash)
    {
        return Err("The capability input is invalid.");
    }
    Ok(())
}

fn is_lower_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
}

fn load_descriptor(path: &Path) -> Result<SkillMcpDescriptor, &'static str> {
    if !path.is_absolute() {
        return Err("The capability connection is unavailable.");
    }
    let parent = path
        .parent()
        .ok_or("The capability connection is unavailable.")?;
    let parent_metadata =
        fs::symlink_metadata(parent).map_err(|_| "The capability connection is unavailable.")?;
    if parent_metadata.file_type().is_symlink() || !parent_metadata.is_dir() {
        return Err("The capability connection is unavailable.");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if parent_metadata.mode() & 0o777 != 0o700
            || parent_metadata.uid() != unsafe { libc::geteuid() }
        {
            return Err("The capability connection is unavailable.");
        }
    }
    let path_metadata =
        fs::symlink_metadata(path).map_err(|_| "The capability connection is unavailable.")?;
    if path_metadata.file_type().is_symlink()
        || !path_metadata.is_file()
        || path_metadata.len() == 0
        || path_metadata.len() > MAX_DESCRIPTOR_BYTES
    {
        return Err("The capability connection is unavailable.");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if path_metadata.mode() & 0o777 != 0o600
            || path_metadata.uid() != unsafe { libc::geteuid() }
            || path_metadata.nlink() != 1
        {
            return Err("The capability connection is unavailable.");
        }
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let file = options
        .open(path)
        .map_err(|_| "The capability connection is unavailable.")?;
    let opened_metadata = file
        .metadata()
        .map_err(|_| "The capability connection is unavailable.")?;
    if !same_file(&path_metadata, &opened_metadata) {
        return Err("The capability connection is unavailable.");
    }
    let mut bytes = Vec::with_capacity(opened_metadata.len() as usize);
    file.take(MAX_DESCRIPTOR_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "The capability connection is unavailable.")?;
    if bytes.len() as u64 > MAX_DESCRIPTOR_BYTES {
        return Err("The capability connection is unavailable.");
    }
    let descriptor: SkillMcpDescriptor =
        serde_json::from_slice(&bytes).map_err(|_| "The capability connection is unavailable.")?;
    validate_descriptor(&descriptor)?;
    Ok(descriptor)
}

fn validate_descriptor(descriptor: &SkillMcpDescriptor) -> Result<(), &'static str> {
    if descriptor.version != 1 || !is_lower_sha256(&descriptor.token) {
        return Err("The capability connection is unavailable.");
    }
    let endpoint = reqwest::Url::parse(&descriptor.endpoint)
        .map_err(|_| "The capability connection is unavailable.")?;
    if endpoint.scheme() != "http"
        || endpoint.host_str() != Some("127.0.0.1")
        || endpoint.port().is_none()
        || endpoint.path() != "/"
        || endpoint.query().is_some()
        || endpoint.fragment().is_some()
        || !endpoint.username().is_empty()
        || endpoint.password().is_some()
    {
        return Err("The capability connection is unavailable.");
    }
    let root_metadata = fs::symlink_metadata(&descriptor.host_projects_root)
        .map_err(|_| "The capability connection is unavailable.")?;
    if !descriptor.host_projects_root.is_absolute()
        || root_metadata.file_type().is_symlink()
        || !root_metadata.is_dir()
        || fs::canonicalize(&descriptor.host_projects_root)
            .map_err(|_| "The capability connection is unavailable.")?
            != descriptor.host_projects_root
    {
        return Err("The capability connection is unavailable.");
    }
    Ok(())
}

fn derive_project_id<'a>(
    host_projects_root: &Path,
    cwd: &'a Path,
) -> Result<&'a str, &'static str> {
    let metadata = fs::symlink_metadata(cwd).map_err(|_| "The research project is unavailable.")?;
    if metadata.file_type().is_symlink()
        || !metadata.is_dir()
        || fs::canonicalize(cwd).map_err(|_| "The research project is unavailable.")? != cwd
        || cwd.parent() != Some(host_projects_root)
    {
        return Err("The research project is unavailable.");
    }
    let project_id = cwd
        .file_name()
        .and_then(OsStr::to_str)
        .ok_or("The research project is unavailable.")?;
    if !crate::science_core_runtime::is_canonical_project_uuid(project_id) {
        return Err("The research project is unavailable.");
    }
    Ok(project_id)
}

fn invoke_core(
    descriptor: &SkillMcpDescriptor,
    project_id: &str,
    input: &RememberVerifiedEvidenceInput,
) -> Result<(), &'static str> {
    let client = reqwest::blocking::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .no_proxy()
        .timeout(HTTP_TIMEOUT)
        .build()
        .map_err(|_| "The capability request could not be sent.")?;
    let endpoint = format!(
        "{}/v1/projects/{project_id}/active-skill-capabilities/remember-verified-evidence/invoke",
        descriptor.endpoint.trim_end_matches('/')
    );
    let body =
        serde_json::to_vec(input).map_err(|_| "The capability request could not be sent.")?;
    let response = client
        .post(endpoint)
        .bearer_auth(&descriptor.token)
        .header("Content-Type", "application/json")
        .header("Idempotency-Key", idempotency_key(project_id, input))
        .body(body)
        .send()
        .map_err(|_| "The capability request could not be sent.")?;
    match response.status().as_u16() {
        200..=299 => Ok(()),
        409 => Err("The capability is not active for this project."),
        _ => Err("The capability request was rejected."),
    }
}

fn idempotency_key(project_id: &str, input: &RememberVerifiedEvidenceInput) -> String {
    let material = json!({
        "action": "spark-skill-mcp-remember-verified-evidence-v1",
        "projectId": project_id,
        "evidenceId": input.evidence_id,
        "expectedSourceContentHash": input.expected_source_content_hash,
        "expectedQuoteHash": input.expected_quote_hash
    });
    let canonical = serde_json::to_vec(&material).expect("fixed idempotency material serializes");
    format!("spark-skill-mcp-{:x}", Sha256::digest(canonical))
}

#[cfg(unix)]
fn same_file(left: &fs::Metadata, right: &fs::Metadata) -> bool {
    use std::os::unix::fs::MetadataExt;
    left.dev() == right.dev() && left.ino() == right.ino()
}

#[cfg(not(unix))]
fn same_file(left: &fs::Metadata, right: &fs::Metadata) -> bool {
    left.is_file() == right.is_file() && left.len() == right.len()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufReader, Cursor};
    use std::net::TcpListener;
    use std::sync::mpsc;
    use std::thread;

    fn temp_root() -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "spark-skill-mcp-test-{}",
            crate::runtime::random_hex(12)
        ));
        fs::create_dir_all(&root).unwrap();
        fs::canonicalize(root).unwrap()
    }

    fn input() -> RememberVerifiedEvidenceInput {
        RememberVerifiedEvidenceInput {
            evidence_id: "evidence-1".into(),
            expected_source_content_hash: "a".repeat(64),
            expected_quote_hash: "b".repeat(64),
        }
    }

    #[test]
    fn dispatches_only_the_exact_sole_flag() {
        assert!(is_skill_mcp_invocation([FLAG]));
        assert!(!is_skill_mcp_invocation([] as [&str; 0]));
        assert!(!is_skill_mcp_invocation([FLAG, "extra"]));
        assert!(!is_skill_mcp_invocation(["--other"]));
    }

    #[test]
    fn handshake_and_tool_list_are_exact() {
        let frames = concat!(
            "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-11-25\",\"clientInfo\":{\"name\":\"opencode\",\"version\":\"1.17.13\"}}}\n",
            "{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\",\"params\":{}}\n",
            "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\",\"params\":{}}\n"
        );
        let mut output = Vec::new();
        serve(BufReader::new(Cursor::new(frames)), &mut output).unwrap();
        let responses = output
            .split(|byte| *byte == b'\n')
            .filter(|line| !line.is_empty())
            .map(|line| serde_json::from_slice::<Value>(line).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(responses.len(), 2);
        assert_eq!(responses[0]["result"]["protocolVersion"], PROTOCOL_VERSION);
        let tools = responses[1]["result"]["tools"].as_array().unwrap();
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0]["name"], TOOL_NAME);
        assert_eq!(
            tools[0]["inputSchema"]["required"],
            json!([
                "evidenceId",
                "expectedSourceContentHash",
                "expectedQuoteHash"
            ])
        );
        assert_eq!(
            tools[0]["inputSchema"]["additionalProperties"],
            Value::Bool(false)
        );
    }

    #[cfg(unix)]
    #[test]
    fn descriptor_and_project_context_fail_closed() {
        use std::os::unix::fs::{symlink, PermissionsExt};

        let root = temp_root();
        fs::set_permissions(&root, fs::Permissions::from_mode(0o700)).unwrap();
        let projects = root.join("projects");
        let project_id = "2d45e34b-c26b-45eb-bb70-894b32ae5f7f";
        let project = projects.join(project_id);
        fs::create_dir_all(&project).unwrap();
        let descriptor_path = root.join("descriptor.json");
        let descriptor = SkillMcpDescriptor {
            version: 1,
            endpoint: "http://127.0.0.1:8765".into(),
            token: "c".repeat(64),
            host_projects_root: projects.clone(),
        };
        fs::write(&descriptor_path, serde_json::to_vec(&descriptor).unwrap()).unwrap();
        fs::set_permissions(&descriptor_path, fs::Permissions::from_mode(0o600)).unwrap();

        assert_eq!(load_descriptor(&descriptor_path).unwrap(), descriptor);
        assert_eq!(derive_project_id(&projects, &project).unwrap(), project_id);
        assert!(derive_project_id(&projects, &projects).is_err());
        assert!(derive_project_id(&projects, &root.join("outside")).is_err());

        fs::set_permissions(&descriptor_path, fs::Permissions::from_mode(0o644)).unwrap();
        assert!(load_descriptor(&descriptor_path).is_err());
        fs::set_permissions(&descriptor_path, fs::Permissions::from_mode(0o600)).unwrap();
        let linked = root.join("descriptor-link.json");
        symlink(&descriptor_path, &linked).unwrap();
        assert!(load_descriptor(&linked).is_err());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn validates_exact_tool_input() {
        assert!(validate_tool_input(&input()).is_ok());
        let mut invalid = input();
        invalid.expected_quote_hash = "B".repeat(64);
        assert!(validate_tool_input(&invalid).is_err());
        let extra = json!({
            "name": TOOL_NAME,
            "arguments": {
                "evidenceId": "evidence-1",
                "expectedSourceContentHash": "a".repeat(64),
                "expectedQuoteHash": "b".repeat(64),
                "workflowId": "forbidden"
            }
        });
        assert_eq!(
            call_tool(extra).unwrap_err(),
            "The capability input is invalid."
        );
    }

    #[cfg(unix)]
    #[test]
    fn non_project_working_directory_never_reaches_core() {
        use std::os::unix::fs::PermissionsExt;

        let root = temp_root();
        fs::set_permissions(&root, fs::Permissions::from_mode(0o700)).unwrap();
        let projects = root.join("projects");
        fs::create_dir_all(&projects).unwrap();
        let descriptor_path = root.join("descriptor.json");
        let descriptor = SkillMcpDescriptor {
            version: 1,
            endpoint: "http://127.0.0.1:9".into(),
            token: "c".repeat(64),
            host_projects_root: projects,
        };
        fs::write(&descriptor_path, serde_json::to_vec(&descriptor).unwrap()).unwrap();
        fs::set_permissions(&descriptor_path, fs::Permissions::from_mode(0o600)).unwrap();
        let params = json!({
            "name": TOOL_NAME,
            "arguments": {
                "evidenceId": "evidence-1",
                "expectedSourceContentHash": "a".repeat(64),
                "expectedQuoteHash": "b".repeat(64)
            }
        });

        assert_eq!(
            call_tool_with_context(params, &descriptor_path, &root).unwrap_err(),
            "The research project is unavailable."
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn posts_exact_inactive_request_without_exposing_response_body() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let (sender, receiver) = mpsc::channel();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let mut request = Vec::new();
            let mut buffer = [0_u8; 4096];
            loop {
                let read = stream.read(&mut buffer).unwrap();
                request.extend_from_slice(&buffer[..read]);
                let header_end = request
                    .windows(4)
                    .position(|window| window == b"\r\n\r\n")
                    .map(|index| index + 4);
                if let Some(header_end) = header_end {
                    let headers = String::from_utf8_lossy(&request[..header_end]);
                    let content_length = headers
                        .lines()
                        .find_map(|line| {
                            line.to_ascii_lowercase()
                                .strip_prefix("content-length: ")
                                .and_then(|value| value.parse::<usize>().ok())
                        })
                        .unwrap_or(0);
                    if request.len() >= header_end + content_length {
                        break;
                    }
                }
            }
            sender.send(request).unwrap();
            stream
                .write_all(
                    b"HTTP/1.1 409 Conflict\r\nContent-Type: application/json\r\nContent-Length: 26\r\nConnection: close\r\n\r\n{\"secret\":\"must-not-leak\"}",
                )
                .unwrap();
        });
        let descriptor = SkillMcpDescriptor {
            version: 1,
            endpoint: format!("http://{address}"),
            token: "c".repeat(64),
            host_projects_root: PathBuf::from("/unused"),
        };
        let project_id = "2d45e34b-c26b-45eb-bb70-894b32ae5f7f";
        assert_eq!(
            invoke_core(&descriptor, project_id, &input()).unwrap_err(),
            "The capability is not active for this project."
        );
        server.join().unwrap();
        let request = String::from_utf8(receiver.recv().unwrap()).unwrap();
        assert!(request.starts_with(&format!(
            "POST /v1/projects/{project_id}/active-skill-capabilities/remember-verified-evidence/invoke HTTP/1.1\r\n"
        )));
        assert!(request.contains("authorization: Bearer "));
        assert!(request.contains("idempotency-key: spark-skill-mcp-"));
        let body = request.split("\r\n\r\n").nth(1).unwrap();
        assert_eq!(
            serde_json::from_str::<Value>(body).unwrap(),
            json!({
                "evidenceId": "evidence-1",
                "expectedSourceContentHash": "a".repeat(64),
                "expectedQuoteHash": "b".repeat(64)
            })
        );
    }
}
