"""Credential-free live infrastructure smoke for the Spark Agent Foundation.

The smoke starts the pinned OpenCode sidecar in fully temporary app-private XDG
directories plus a deterministic loopback OpenAI-compatible test double. The
Pinned runtime executes a real selected-agent loop: skill, apply_patch behind
its real `edit` approval, bash approval, history, artifacts, and persistence across a process
restart. It never contacts an external model provider or inherits user
credentials.
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import platform
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "runtime" / "opencode-profile"
SKILLS = ROOT / "runtime" / "skills" / "core"
FIXTURE = Path(__file__).with_name("create_artifacts.py")
PINNED_VERSION = "1.17.13"
PASSWORD = "foundation-local-smoke"
MODEL_ID = "gpt-5-foundation"

EXPECTED_AGENTS = {
    "research",
    "plan",
    "literature-review",
    "critique",
    "write",
    "explore",
    "task",
}
EXPECTED_SKILLS = {
    "literature-review",
    "citation-management",
    "hypothesis-generation",
    "scientific-critical-thinking",
    "exploratory-data-analysis",
    "statistical-analysis",
    "scientific-writing",
    "matplotlib",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def sidecar_path() -> Path:
    machine = platform.machine().lower()
    system = platform.system().lower()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        name = "opencode-aarch64-apple-darwin"
    elif system == "darwin" and machine in {"x86_64", "amd64"}:
        name = "opencode-x86_64-apple-darwin"
    elif system == "linux" and machine in {"arm64", "aarch64"}:
        name = "opencode-aarch64-unknown-linux-gnu"
    elif system == "linux" and machine in {"x86_64", "amd64"}:
        name = "opencode-x86_64-unknown-linux-gnu"
    else:
        fail(f"live Foundation smoke has no trusted sidecar mapping for {system}/{machine}")
    path = ROOT / "apps" / "desktop" / "src-tauri" / "binaries" / name
    if not path.is_file() or not os.access(path, os.X_OK):
        fail(
            f"pinned OpenCode sidecar is missing or not executable: {path}; "
            "fetch the trusted host asset with scripts/dev/fetch-opencode.sh"
        )
    return path


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class Api:
    def __init__(self, port: int) -> None:
        self.base_url = f"http://127.0.0.1:{port}"
        credential = base64.b64encode(f"opencode:{PASSWORD}".encode()).decode()
        self.headers = {"Authorization": f"Basic {credential}"}

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 20,
    ) -> Any:
        body = None if payload is None else json.dumps(payload).encode()
        headers = dict(self.headers)
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read(2048).decode("utf-8", errors="replace")
            fail(f"{method} {path} returned HTTP {error.code}: {detail}")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace")


class PermissionEventCapture:
    """One scoped SSE reader used for apply_patch's rich permission metadata."""

    def __init__(self, api: Api, workspace: Path) -> None:
        self.api = api
        self.workspace = workspace
        self.ready = threading.Event()
        self.found = threading.Event()
        self.request: dict[str, Any] | None = None
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        if not self.ready.wait(timeout=5):
            fail("permission SSE reader did not connect")

    def _run(self) -> None:
        request = urllib.request.Request(
            self.api.base_url + f"/event?{directory_query(self.workspace)}",
            headers=self.api.headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                self.ready.set()
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line.removeprefix("data:").strip())
                    if event.get("type") not in {"permission.asked", "permission.v2.asked"}:
                        continue
                    properties = event.get("properties")
                    if isinstance(properties, dict):
                        self.request = properties
                        self.found.set()
                        return
        except BaseException as error:
            self.error = error
            self.ready.set()
            self.found.set()

    def wait(self, timeout: float = 30) -> dict[str, Any]:
        if not self.found.wait(timeout=timeout):
            fail("permission SSE reader did not receive an event")
        if self.error is not None:
            raise self.error
        if self.request is None:
            fail("permission SSE reader returned no request")
        return self.request


class Sidecar:
    def __init__(self, binary: Path, runtime: Path, workspace: Path) -> None:
        self.binary = binary
        self.runtime = runtime
        self.workspace = workspace
        self.process: subprocess.Popen[str] | None = None
        self.log = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        self.port = 0

    def start(self) -> Api:
        self.port = free_port()
        # A minimal allowlist avoids inheriting API keys or a user's OpenCode
        # login. All state lives below the temporary runtime root.
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.runtime / "home"),
            "TMPDIR": str(self.runtime / "tmp"),
            "XDG_CONFIG_HOME": str(self.runtime / "xdg-config"),
            "XDG_DATA_HOME": str(self.runtime / "xdg-data"),
            "XDG_CACHE_HOME": str(self.runtime / "xdg-cache"),
            "XDG_STATE_HOME": str(self.runtime / "xdg-state"),
            "OPENCODE_SERVER_PASSWORD": PASSWORD,
            "OPENCODE_PERMISSION": json.dumps(
                json.loads((PROFILE / "opencode.json").read_text(encoding="utf-8"))["permission"],
                separators=(",", ":"),
            ),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        for directory in (
            environment["HOME"],
            environment["TMPDIR"],
            environment["XDG_CONFIG_HOME"],
            environment["XDG_DATA_HOME"],
            environment["XDG_CACHE_HOME"],
            environment["XDG_STATE_HOME"],
        ):
            Path(directory).mkdir(parents=True, exist_ok=True)

        self.process = subprocess.Popen(
            [
                str(self.binary),
                "serve",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=self.workspace,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        api = Api(self.port)
        deadline = time.monotonic() + 20
        last_error: BaseException | None = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                fail(f"OpenCode exited during startup (status {self.process.returncode})\n{self.logs()}")
            try:
                health = api.request("GET", "/global/health", timeout=1)
                if health == {"healthy": True, "version": PINNED_VERSION}:
                    return api
                last_error = AssertionError(f"unexpected health payload: {health!r}")
            except (OSError, AssertionError) as error:
                last_error = error
            time.sleep(0.1)
        fail(f"OpenCode did not become ready: {last_error}\n{self.logs()}")

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)

    def logs(self) -> str:
        self.log.flush()
        self.log.seek(0)
        return "".join(self.log.readlines()[-80:])

    def close(self) -> None:
        self.stop()
        self.log.close()


class MockModel:
    """Deterministic local OpenAI-compatible model that drives real tools."""

    def __init__(self) -> None:
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                owner.handle(self)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.requests: list[dict[str, Any]] = []
        self.selected_tools: list[str] = []
        self.errors: list[str] = []

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def handle(self, handler: http.server.BaseHTTPRequestHandler) -> None:
        try:
            if handler.path.rstrip("/") != "/v1/chat/completions":
                handler.send_error(404)
                return
            length = int(handler.headers.get("Content-Length", "0"))
            payload = json.loads(handler.rfile.read(length))
            if not isinstance(payload, dict):
                raise AssertionError("model request was not an object")
            self.requests.append(payload)
            messages = payload.get("messages", [])
            tool_results = sum(
                1 for message in messages
                if isinstance(message, dict) and message.get("role") == "tool"
            )
            tool_names = set()
            for item in payload.get("tools", []):
                if not isinstance(item, dict):
                    continue
                function = item.get("function")
                name = (
                    function.get("name")
                    if isinstance(function, dict)
                    else item.get("name")
                )
                if isinstance(name, str):
                    tool_names.add(name)
            # OpenCode may issue an auxiliary title/summary request through the
            # same provider. Those intentionally expose no tools and must not
            # advance the research turn's deterministic tool sequence.
            if not tool_names:
                self.respond(
                    handler,
                    payload,
                    {"role": "assistant", "content": "Foundation research analysis"},
                )
                return
            if tool_results == 0:
                response = self.tool_call("skill", {"name": "matplotlib"}, tool_names)
            elif tool_results == 1:
                source = FIXTURE.read_text(encoding="utf-8")
                response = self.tool_call(
                    "apply_patch",
                    {
                        "patchText": (
                            "*** Begin Patch\n"
                            "*** Add File: scripts/foundation_analysis.py\n"
                            + "".join(f"+{line}\n" for line in source.splitlines())
                            + "*** End Patch"
                        ),
                    },
                    tool_names,
                )
            elif tool_results == 2:
                response = self.tool_call(
                    "bash",
                    {
                        "command": (
                            f"{shlex.quote(sys.executable)} "
                            "scripts/foundation_analysis.py"
                        ),
                        "description": "Run deterministic Foundation analysis",
                    },
                    tool_names,
                )
            else:
                response = {
                    "role": "assistant",
                    "content": (
                        "Loaded the matplotlib skill, wrote and ran the analysis, "
                        "and produced outputs/summary.csv and outputs/figure.png."
                    ),
                }
            self.respond(handler, payload, response)
        except BaseException as error:  # make the sidecar failure diagnosable
            self.errors.append(f"{type(error).__name__}: {error}")
            handler.send_error(500, str(error))

    def tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        available: set[str],
    ) -> dict[str, Any]:
        if name not in available:
            raise AssertionError(
                f"selected Research Agent did not expose {name!r}; "
                f"available={sorted(available)} raw_tools={self.requests[-1].get('tools')!r}"
            )
        self.selected_tools.append(name)
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"foundation-{len(self.selected_tools)}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments),
                    },
                }
            ],
        }

    @staticmethod
    def respond(
        handler: http.server.BaseHTTPRequestHandler,
        request: dict[str, Any],
        message: dict[str, Any],
    ) -> None:
        finish_reason = "tool_calls" if message.get("tool_calls") else "stop"
        if request.get("stream"):
            chunks = [
                {
                    "id": "chatcmpl-foundation",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": MODEL_ID,
                    "choices": [
                        {"index": 0, "delta": message, "finish_reason": None}
                    ],
                },
                {
                    "id": "chatcmpl-foundation",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": MODEL_ID,
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": finish_reason}
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 10,
                        "total_tokens": 20,
                    },
                },
            ]
            body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
            body += "data: [DONE]\n\n"
            encoded = body.encode()
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Content-Length", str(len(encoded)))
            handler.end_headers()
            handler.wfile.write(encoded)
            return
        body = json.dumps(
            {
                "id": "chatcmpl-foundation",
                "object": "chat.completion",
                "created": 1,
                "model": MODEL_ID,
                "choices": [
                    {"index": 0, "message": message, "finish_reason": finish_reason}
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 10,
                    "total_tokens": 20,
                },
            }
        ).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)


def deploy_test_profile(runtime: Path, workspace: Path, model_port: int) -> None:
    config_root = runtime / "xdg-config" / "opencode"
    config_root.mkdir(parents=True)
    config = json.loads((PROFILE / "opencode.json").read_text(encoding="utf-8"))
    config["model"] = f"foundation/{MODEL_ID}"
    config["provider"] = {
        "foundation": {
            "name": "Foundation deterministic model",
            "npm": "@ai-sdk/openai-compatible",
            "options": {
                "baseURL": f"http://127.0.0.1:{model_port}/v1",
                "apiKey": "temporary-test-only",
            },
            "models": {
                MODEL_ID: {
                    "name": "Foundation deterministic model",
                    "tool_call": True,
                    "limit": {"context": 128000, "output": 4096},
                }
            },
        }
    }
    (config_root / "opencode.json").write_text(json.dumps(config), encoding="utf-8")
    shutil.copytree(PROFILE / "agents", config_root / "agents")
    skills_root = config_root / "skills"
    skills_root.mkdir()
    for source in sorted(SKILLS.iterdir()):
        if source.is_dir() and (source / "SKILL.md").is_file():
            # Spark's deployment layer must avoid duplicate-name registration:
            # pinned OpenCode loads duplicates concurrently. Reserving a name
            # present in the active project makes project precedence deterministic.
            if any(
                (workspace / ".opencode" / root / source.name / "SKILL.md").is_file()
                for root in ("skill", "skills")
            ):
                continue
            shutil.copytree(source, skills_root / source.name)


def directory_query(workspace: Path) -> str:
    return urllib.parse.urlencode({"directory": str(workspace)})


def names(items: Any) -> set[str]:
    if not isinstance(items, list):
        fail(f"expected a list, got {type(items).__name__}")
    return {str(item.get("name")) for item in items if isinstance(item, dict)}


def wildcard_matches(value: str, pattern: str) -> bool:
    """Match pinned OpenCode's `*`/`?` wildcard semantics."""
    value = value.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    if pattern.endswith(" *") and value == pattern[:-2]:
        return True
    previous = [True] + [False] * len(value)
    for token in pattern:
        current = [False] * (len(value) + 1)
        if token == "*":
            current[0] = previous[0]
        for index, character in enumerate(value, 1):
            if token == "*":
                current[index] = previous[index] or current[index - 1]
            elif token == "?":
                current[index] = previous[index - 1]
            else:
                current[index] = previous[index - 1] and character == token
        previous = current
    return previous[-1]


def resolved_action(agent: dict[str, Any], permission: str, pattern: str) -> str | None:
    rules = agent.get("permission")
    if not isinstance(rules, list):
        fail(f"agent has no resolved permission list: {agent!r}")
    for rule in reversed(rules):
        if not isinstance(rule, dict):
            continue
        if wildcard_matches(permission, str(rule.get("permission", ""))) and wildcard_matches(
            pattern, str(rule.get("pattern", ""))
        ):
            return str(rule.get("action"))
    return None


def assert_resolved_permission_floor(agent_payload: Any, truncate_glob: Path) -> None:
    if not isinstance(agent_payload, list) or not agent_payload:
        fail(f"expected resolved agents, got {agent_payload!r}")
    normalized_truncate = str(truncate_glob).replace("\\", "/")
    safe_allow = {"read", "glob", "grep", "list", "lsp", "question", "skill", "task"}
    for agent in agent_payload:
        if not isinstance(agent, dict):
            fail(f"invalid resolved agent: {agent!r}")
        rules = agent.get("permission")
        if not isinstance(rules, list):
            fail(f"agent has no permission rules: {agent!r}")
        floor = next(
            (
                index
                for index in range(len(rules) - 1, -1, -1)
                if rules[index].get("permission") == "*"
                and rules[index].get("pattern") == "*"
                and rules[index].get("action") == "ask"
            ),
            None,
        )
        if floor is None:
            fail(f"agent {agent.get('name')!r} has no wildcard ask floor")
        for rule in rules[floor + 1 :]:
            if rule.get("action") != "allow":
                continue
            permission = str(rule.get("permission", ""))
            pattern = str(rule.get("pattern", "")).replace("\\", "/")
            if permission in safe_allow:
                continue
            if permission == "external_directory" and pattern == normalized_truncate:
                continue
            fail(
                f"agent {agent.get('name')!r} appends unsafe allow "
                f"{permission!r} {pattern!r}"
            )
        for permission, pattern in (
            ("bash", "*"),
            ("edit", "*"),
            ("apply_patch", "*"),
            ("webfetch", "*"),
            ("websearch", "*"),
            ("spark-policy-unknown-tool", "*"),
            ("read", ".env"),
            ("read", "nested/.env.local"),
            ("read", "mcp:policy-check:*"),
        ):
            if resolved_action(agent, permission, pattern) == "allow":
                fail(
                    f"agent {agent.get('name')!r} resolves unsafe allow "
                    f"{permission!r} {pattern!r}"
                )
        if (
            resolved_action(
                agent,
                "external_directory",
                "/spark-agent-policy-check/outside",
            )
            != "deny"
        ):
            fail(f"agent {agent.get('name')!r} does not deny external workspace access")


def assert_artifacts(workspace: Path) -> None:
    csv_path = workspace / "outputs" / "summary.csv"
    png_path = workspace / "outputs" / "figure.png"
    if csv_path.read_text(encoding="utf-8").splitlines() != [
        "group,value",
        "control,1.0",
        "treatment,1.5",
        "treatment,1.8",
    ]:
        fail("CSV artifact content is not deterministic")
    png = png_path.read_bytes()
    if not png.startswith(b"\x89PNG\r\n\x1a\n") or b"IHDR" not in png or b"IEND" not in png:
        fail("PNG artifact is not a valid PNG envelope")


def observe_and_approve_once(
    api: Api,
    workspace: Path,
    session_id: str,
    sidecar: Sidecar,
    model: MockModel,
    expected_permission: str,
    expected_resource: str,
    event_capture: PermissionEventCapture | None = None,
) -> str:
    """Wait for the real runtime permission, validate it, and grant it once."""
    query = directory_query(workspace)
    if event_capture is not None:
        requests = [event_capture.wait()]
        deadline = time.monotonic()
    else:
        requests = []
        deadline = time.monotonic() + 30
    while requests or time.monotonic() < deadline:
        if not requests:
            pending = api.request("GET", f"/permission?{query}", timeout=2)
            if not isinstance(pending, list):
                fail(f"unexpected pending-permission response: {pending!r}")
            requests = [
                item
                for item in pending
                if isinstance(item, dict) and str(item.get("sessionID")) == session_id
            ]
        if requests:
            if len(requests) != 1:
                fail(f"expected one pending permission, got {requests!r}")
            request = requests[0]
            action = str(request.get("permission") or request.get("action") or "")
            if action != expected_permission:
                fail(f"expected an {expected_permission} permission, got {request!r}")
            resources = request.get("patterns") or request.get("resources") or []
            if not isinstance(resources, list) or not any(
                expected_resource in str(resource) for resource in resources
            ):
                fail(f"permission did not name {expected_resource!r}: {request!r}")
            request_id = str(request.get("id") or "")
            if not request_id:
                fail(f"bash permission did not include an id: {request!r}")
            api.request(
                "POST",
                f"/permission/{urllib.parse.quote(request_id, safe='')}/reply?{query}",
                {"reply": "once"},
            )
            return request_id
        if expected_permission == "edit" and (workspace / "scripts/foundation_analysis.py").exists():
            fail("apply_patch executed before the required edit approval was observed")
        if expected_permission == "bash" and (workspace / "outputs/summary.csv").exists():
            fail("bash executed before the required manual approval was observed")
        if model.errors:
            fail(f"deterministic model failed before permission: {model.errors}")
        time.sleep(0.1)
    fail(
        f"Research Agent did not request {expected_permission} permission before timeout\n"
        f"model errors={model.errors}\nsidecar logs={sidecar.logs()}"
    )


def run() -> None:
    binary = sidecar_path()
    version = subprocess.run(
        [str(binary), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    if version != PINNED_VERSION:
        fail(f"expected OpenCode {PINNED_VERSION}, got {version!r}")

    with tempfile.TemporaryDirectory(prefix="spark-foundation-") as temporary:
        root = Path(temporary)
        runtime = root / "runtime"
        workspace = root / "workspace"
        workspace.mkdir()
        project_config = workspace / "opencode.json"
        project_config.write_text(
            json.dumps({"permission": {"*": "allow"}}),
            encoding="utf-8",
        )
        safe_agent = workspace / ".opencode" / "agents" / "safe-custom.md"
        safe_agent.parent.mkdir(parents=True)
        safe_agent.write_text(
            "---\n"
            "description: More restrictive project-local policy fixture.\n"
            "mode: subagent\n"
            "permission:\n"
            '  "*": deny\n'
            "---\n\nRead-only validation fixture.\n",
            encoding="utf-8",
        )
        unsafe_workspace = root / "unsafe-workspace"
        unsafe_agent = unsafe_workspace / ".opencode" / "agents" / "bypass.md"
        unsafe_agent.parent.mkdir(parents=True)
        unsafe_agent.write_text(
            "---\n"
            "description: Deliberately unsafe live-regression fixture.\n"
            "mode: subagent\n"
            "permission:\n"
            "  bash: allow\n"
            "  edit: allow\n"
            "  webfetch: allow\n"
            "  paper-search_search_crossref: allow\n"
            "---\n\nThis agent must be rejected by Spark's resolved-rule gate.\n",
            encoding="utf-8",
        )
        project_skill = workspace / ".opencode" / "skills" / "matplotlib"
        project_skill.mkdir(parents=True)
        (project_skill / "SKILL.md").write_text(
            "---\n"
            "name: matplotlib\n"
            "description: Foundation project-local matplotlib override.\n"
            "---\n\n"
            "PROJECT_OVERRIDE_MARKER: use the project-specific plotting policy.\n",
            encoding="utf-8",
        )
        model = MockModel()
        model.start()
        deploy_test_profile(runtime, workspace, model.port)

        first = Sidecar(binary, runtime, workspace)
        second: Sidecar | None = None
        try:
            api = first.start()
            agent_payload = api.request("GET", f"/agent?{directory_query(workspace)}")
            missing_agents = EXPECTED_AGENTS - names(agent_payload)
            if missing_agents:
                fail(f"live OpenCode did not discover agents: {sorted(missing_agents)}")
            assert_resolved_permission_floor(
                agent_payload,
                runtime / "xdg-data" / "opencode" / "tool-output" / "*",
            )
            if json.loads(project_config.read_text(encoding="utf-8"))["permission"] != {
                "*": "allow"
            }:
                fail("permission floor mutated the user project config at rest")

            unsafe_payload = api.request(
                "GET",
                f"/agent?{directory_query(unsafe_workspace)}",
            )
            try:
                assert_resolved_permission_floor(
                    unsafe_payload,
                    runtime / "xdg-data" / "opencode" / "tool-output" / "*",
                )
            except AssertionError as error:
                if "bypass" not in str(error) or "unsafe allow" not in str(error):
                    raise
            else:
                fail("unsafe project agent frontmatter bypassed the resolved-rule gate")

            # The pinned runtime's executable skill registry is the legacy
            # `/skill` service. The experimental V2 `/api/skill` route has a
            # separate registry and does not expose global bundled skills.
            skill_payload = api.request("GET", f"/skill?{directory_query(workspace)}")
            discovered_skills = names(skill_payload)
            missing_skills = EXPECTED_SKILLS - discovered_skills
            if missing_skills:
                fail(
                    "live OpenCode did not discover skills: "
                    f"missing={sorted(missing_skills)}, discovered={sorted(discovered_skills)}"
                )
            matplotlib = [
                item
                for item in skill_payload
                if isinstance(item, dict) and item.get("name") == "matplotlib"
            ]
            if len(matplotlib) != 1:
                fail(f"expected one deduplicated matplotlib skill, got {matplotlib!r}")
            location = str(matplotlib[0].get("location", "")).replace("\\", "/")
            if "/.opencode/skills/matplotlib/SKILL.md" not in location:
                fail(f"project-local skill did not override bundled skill: {matplotlib[0]!r}")
            if "project-local" not in str(matplotlib[0].get("description", "")):
                fail(f"project-local skill description was not returned: {matplotlib[0]!r}")

            command_payload = api.request("GET", f"/command?{directory_query(workspace)}")
            missing_commands = EXPECTED_SKILLS - names(command_payload)
            if missing_commands:
                fail(f"live OpenCode did not expose skill commands: {sorted(missing_commands)}")

            created = api.request(
                "POST",
                f"/session?{directory_query(workspace)}",
                {},
            )
            if not isinstance(created, dict) or not created.get("id"):
                fail(f"unexpected session create response: {created!r}")
            session_id = str(created["id"])
            edit_permission_event = PermissionEventCapture(api, workspace)
            edit_permission_event.start()

            api.request(
                "POST",
                f"/session/{urllib.parse.quote(session_id, safe='')}/prompt_async",
                {
                    "agent": "research",
                    "model": {
                        "providerID": "foundation",
                        "modelID": MODEL_ID,
                    },
                    "parts": [
                        {
                            "type": "text",
                            "text": (
                                "Load the matplotlib skill, write and run a Python "
                                "analysis, and create a CSV table plus PNG figure."
                            ),
                        }
                    ],
                },
                timeout=20,
            )
            edit_permission_id = observe_and_approve_once(
                api,
                workspace,
                session_id,
                first,
                model,
                "edit",
                "scripts/foundation_analysis.py",
                edit_permission_event,
            )
            bash_permission_id = observe_and_approve_once(
                api,
                workspace,
                session_id,
                first,
                model,
                "bash",
                "foundation_analysis.py",
            )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if (workspace / "outputs/summary.csv").is_file() and (
                    workspace / "outputs/figure.png"
                ).is_file():
                    break
                if model.errors:
                    fail(f"deterministic model failed: {model.errors}")
                time.sleep(0.1)
            else:
                fail(
                    "Research Agent did not create artifacts before timeout\n"
                    f"model errors={model.errors}\nsidecar logs={first.logs()}"
                )
            assert_artifacts(workspace)
            if not (workspace / "scripts/foundation_analysis.py").is_file():
                fail("Research Agent did not write the Python analysis script")
            if model.selected_tools != ["skill", "apply_patch", "bash"]:
                fail(f"unexpected Research Agent tool sequence: {model.selected_tools}")
            pending_after_approval = api.request(
                "GET",
                f"/permission?{directory_query(workspace)}",
            )
            if not isinstance(pending_after_approval, list):
                fail(
                    "unexpected pending-permission response after approval: "
                    f"{pending_after_approval!r}"
                )
            if any(
                isinstance(item, dict)
                and str(item.get("id")) in {edit_permission_id, bash_permission_id}
                for item in pending_after_approval
            ):
                fail("one-time bash permission remained pending after execution")
            research_requests = [request for request in model.requests if request.get("tools")]
            if not research_requests or "general scientific research collaborator" not in json.dumps(
                research_requests[0].get("messages", [])
            ):
                fail("selected research Agent prompt was not sent to the model")
            if "PROJECT_OVERRIDE_MARKER" not in json.dumps(research_requests):
                fail("the project-local matplotlib skill was not loaded into the agent turn")

            messages = api.request(
                "GET",
                f"/session/{urllib.parse.quote(session_id, safe='')}/message",
            )
            history_tools = {
                str(part.get("tool"))
                for message in messages
                if isinstance(message, dict)
                for part in message.get("parts", [])
                if isinstance(part, dict) and part.get("type") == "tool"
            } if isinstance(messages, list) else set()
            if not {"skill", "apply_patch", "bash"}.issubset(history_tools):
                fail(f"session history did not persist the agent tool loop: {history_tools}")

            first.stop()
            second = Sidecar(binary, runtime, workspace)
            restarted = second.start()
            sessions = restarted.request("GET", "/experimental/session")
            if not isinstance(sessions, list):
                fail(f"unexpected persisted session response: {sessions!r}")
            matched = [item for item in sessions if item.get("id") == session_id]
            if not matched:
                fail("session disappeared after an OpenCode server restart")
            if Path(str(matched[0].get("directory"))).resolve() != workspace.resolve():
                fail("persisted session lost its workspace directory")
            persisted_messages = restarted.request(
                "GET",
                f"/session/{urllib.parse.quote(session_id, safe='')}/message",
            )
            if not persisted_messages:
                fail("session message history disappeared after restart")
            assert_artifacts(workspace)
        finally:
            first.close()
            if second is not None:
                second.close()
            model.close()

    print(
        "Foundation live smoke passed: the pinned runtime resisted a project-global allow, "
        "rejected unsafe custom-agent rules, required one-time edit/apply_patch and bash "
        "approvals, preserved CSV/PNG artifacts, and restored the session after restart."
    )
    print(
        "No external provider credentials or network model endpoint were used; the model was a "
        "deterministic loopback test double and the OpenCode agent/tool loop was real."
    )


if __name__ == "__main__":
    run()
