"""Opt-in real-provider smoke for Spark Agent autonomous data research.

This test is deliberately excluded from portable quality gates. It uses a
temporary OpenCode profile and project, never reads the user's OpenCode state,
and does not persist credentials or generated model output.
"""

from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import socket
import subprocess
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
PINNED_VERSION = "1.17.13"
PASSWORD = "autonomous-live-smoke"
REQUIRED_ARTIFACTS = (
    "scripts/analysis.py",
    "tables/summary.csv",
    "figures/analysis.png",
    "reports/data-analysis.md",
)


def fail(message: str) -> None:
    raise AssertionError(message)


def sidecar_path() -> Path:
    machine = platform.machine().lower()
    system = platform.system().lower()
    triples = {
        ("darwin", "arm64"): "aarch64-apple-darwin",
        ("darwin", "aarch64"): "aarch64-apple-darwin",
        ("darwin", "x86_64"): "x86_64-apple-darwin",
        ("darwin", "amd64"): "x86_64-apple-darwin",
        ("linux", "arm64"): "aarch64-unknown-linux-gnu",
        ("linux", "aarch64"): "aarch64-unknown-linux-gnu",
        ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
        ("linux", "amd64"): "x86_64-unknown-linux-gnu",
    }
    triple = triples.get((system, machine))
    if triple is None:
        fail(f"unsupported live-smoke host: {system}/{machine}")
    path = ROOT / "apps" / "desktop" / "src-tauri" / "binaries" / f"opencode-{triple}"
    if not path.is_file() or not os.access(path, os.X_OK):
        fail(f"pinned OpenCode sidecar is unavailable: {path}")
    return path


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def directory_query(workspace: Path) -> str:
    return urllib.parse.urlencode({"directory": str(workspace)})


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
        timeout: float = 30,
    ) -> Any:
        body = None if payload is None else json.dumps(payload).encode()
        headers = dict(self.headers)
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read(2048).decode("utf-8", errors="replace")
            fail(f"{method} {path} returned HTTP {error.code}: {detail}")
        return json.loads(raw) if raw else None


class IdleCapture:
    def __init__(self, api: Api, workspace: Path, session_id: str) -> None:
        self.api = api
        self.workspace = workspace
        self.session_id = session_id
        self.ready = threading.Event()
        self.idle = threading.Event()
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        if not self.ready.wait(timeout=5):
            fail("live-smoke event stream did not connect")

    def _run(self) -> None:
        request = urllib.request.Request(
            self.api.base_url + f"/event?{directory_query(self.workspace)}",
            headers=self.api.headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                self.ready.set()
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line.removeprefix("data:").strip())
                    properties = event.get("properties", {})
                    if (
                        event.get("type") == "session.idle"
                        and str(properties.get("sessionID")) == self.session_id
                    ):
                        self.idle.set()
                        return
        except BaseException as error:
            self.error = error
            self.ready.set()
            self.idle.set()

    def wait(self, timeout: float = 480) -> None:
        if not self.idle.wait(timeout=timeout):
            fail("real-provider Research Agent did not reach idle before timeout")
        if self.error is not None:
            raise self.error


def deploy_profile(runtime: Path, model: str, api_key: str, base_url: str) -> tuple[str, str]:
    provider_label, separator, model_id = model.partition("/")
    if not separator or not provider_label or not model_id:
        fail("SPARK_LIVE_MODEL must use provider/model form")
    provider_id = "spark-live"
    config_root = runtime / "xdg-config" / "opencode"
    config_root.mkdir(parents=True)
    config = json.loads((PROFILE / "opencode.json").read_text(encoding="utf-8"))
    # The packaged app installs this equivalent through the native `full`
    # compatibility preset. This bounded smoke needs only the ordinary
    # research-tool slice; destructive-policy fidelity is covered by Rust
    # preset tests and is intentionally not reimplemented in Python.
    permission = config["permission"]
    permission.update({
        "edit": "allow",
        "apply_patch": "allow",
        "bash": {
            "*": "allow",
            "rm *": "ask",
            "* rm *": "ask",
            "sudo *": "deny",
            "* sudo *": "deny",
            "git push *": "ask",
            "* git push *": "ask",
        },
        "webfetch": "allow",
        "websearch": "allow",
        "skill": "allow",
        "task": "allow",
    })
    config["model"] = f"{provider_id}/{model_id}"
    config["provider"] = {
        provider_id: {
            "name": f"Spark live {provider_label}",
            "npm": "@ai-sdk/openai-compatible",
            "options": {"baseURL": base_url, "apiKey": api_key},
            "models": {
                model_id: {
                    "name": model,
                    "tool_call": True,
                    "limit": {"context": 128000, "output": 8192},
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
            shutil.copytree(source, skills_root / source.name)
    return provider_id, model_id


def start_sidecar(binary: Path, runtime: Path, workspace: Path) -> tuple[subprocess.Popen[str], Api]:
    port = free_port()
    log_path = runtime / "opencode.log"
    log = log_path.open("w", encoding="utf-8")
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(runtime / "home"),
        "TMPDIR": str(runtime / "tmp"),
        "XDG_CONFIG_HOME": str(runtime / "xdg-config"),
        "XDG_DATA_HOME": str(runtime / "xdg-data"),
        "XDG_CACHE_HOME": str(runtime / "xdg-cache"),
        "XDG_STATE_HOME": str(runtime / "xdg-state"),
        "OPENCODE_SERVER_PASSWORD": PASSWORD,
        "OPENCODE_PURE": "true",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    for key in ("HOME", "TMPDIR", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        Path(environment[key]).mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [str(binary), "serve", "--hostname", "127.0.0.1", "--port", str(port)],
        cwd=workspace,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log.close()
    api = Api(port)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if process.poll() is not None:
            fail(f"OpenCode exited during startup; see {log_path}")
        try:
            if api.request("GET", "/global/health", timeout=1).get("healthy") is True:
                return process, api
        except (OSError, AssertionError):
            pass
        time.sleep(0.1)
    process.terminate()
    fail(f"OpenCode did not start; see {log_path}")


def validate_artifacts(workspace: Path) -> list[str]:
    missing = [path for path in REQUIRED_ARTIFACTS if not (workspace / path).is_file()]
    if missing:
        fail(f"real-provider smoke did not create required artifacts: {missing}")
    png = (workspace / "figures/analysis.png").read_bytes()
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        fail("real-provider figure is not a PNG")
    if len((workspace / "tables/summary.csv").read_text(encoding="utf-8").splitlines()) < 2:
        fail("real-provider summary CSV has no data rows")
    return list(REQUIRED_ARTIFACTS)


def run() -> None:
    model = os.environ.get("SPARK_LIVE_MODEL", "").strip()
    api_key = os.environ.get("SPARK_LIVE_API_KEY", "").strip()
    if not model or not api_key:
        print("SKIP: set SPARK_LIVE_MODEL and SPARK_LIVE_API_KEY to run the real-provider smoke")
        return
    base_url = os.environ.get("SPARK_LIVE_BASE_URL", "https://api.openai.com/v1").strip()
    binary = sidecar_path()
    version = subprocess.run(
        [str(binary), "--version"], check=True, capture_output=True, text=True, timeout=10
    ).stdout.strip()
    if version != PINNED_VERSION:
        fail(f"expected OpenCode {PINNED_VERSION}, got {version!r}")

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="spark-autonomous-live-") as temporary:
        root = Path(temporary)
        runtime = root / "runtime"
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "data.csv").write_text(
            "group,value,quality\ncontrol,1.0,ok\ncontrol,1.2,ok\ntreatment,1.8,ok\n"
            "treatment,2.1,ok\ntreatment,,missing\n",
            encoding="utf-8",
        )
        provider_id, model_id = deploy_profile(runtime, model, api_key, base_url)
        process, api = start_sidecar(binary, runtime, workspace)
        try:
            created = api.request("POST", f"/session?{directory_query(workspace)}", {})
            if not isinstance(created, dict) or not created.get("id"):
                fail(f"unexpected session create response: {created!r}")
            session_id = str(created["id"])
            idle = IdleCapture(api, workspace, session_id)
            idle.start()
            api.request(
                "POST",
                f"/session/{urllib.parse.quote(session_id, safe='')}/prompt_async",
                {
                    "agent": "research",
                    "model": {"providerID": provider_id, "modelID": model_id},
                    "parts": [{
                        "type": "text",
                        "text": (
                            "Work autonomously on data.csv. Load the exploratory-data-analysis "
                            "skill and delegate exactly one bounded schema and quality review to "
                            "the task agent. Then inspect the returned result, write and run "
                            "scripts/analysis.py, and create "
                            "tables/summary.csv, figures/analysis.png, and "
                            "reports/data-analysis.md with limitations. Verify every artifact. "
                            "Use only installed Python packages and do not ask questions."
                        ),
                    }],
                },
                timeout=30,
            )
            idle.wait()
            artifacts = validate_artifacts(workspace)
            messages = api.request(
                "GET", f"/session/{urllib.parse.quote(session_id, safe='')}/message"
            )
            tools = sorted({
                str(part.get("tool"))
                for message_item in messages if isinstance(message_item, dict)
                for part in message_item.get("parts", []) if isinstance(part, dict)
                and part.get("type") == "tool" and part.get("tool")
            }) if isinstance(messages, list) else []
            missing_tools = {"bash", "skill", "task"} - set(tools)
            if missing_tools:
                fail(
                    "real-provider Agent missed required native tools: "
                    f"missing={sorted(missing_tools)}, tools={tools}"
                )

            process.terminate()
            process.wait(timeout=8)
            process, restarted_api = start_sidecar(binary, runtime, workspace)
            sessions = restarted_api.request("GET", "/experimental/session")
            if not isinstance(sessions, list) or not any(
                isinstance(item, dict) and str(item.get("id")) == session_id
                for item in sessions
            ):
                fail("real-provider session did not survive an OpenCode restart")
            restarted_history = restarted_api.request(
                "GET", f"/session/{urllib.parse.quote(session_id, safe='')}/message"
            )
            if not isinstance(restarted_history, list) or not restarted_history:
                fail("real-provider message history did not survive an OpenCode restart")
            validate_artifacts(workspace)
            print(json.dumps({
                "status": "PASS",
                "model": model,
                "provider": model.partition("/")[0],
                "durationSeconds": round(time.monotonic() - started, 1),
                "tools": tools,
                "artifacts": artifacts,
            }, indent=2))
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    run()
