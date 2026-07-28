from __future__ import annotations

import os
import signal
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

import open_science_core.workflow.mcp_stdio_broker as broker_module
from open_science_core.workflow.discovery_adapter import KnownMcpToolFailure
from open_science_core.workflow.mcp_stdio_broker import (
    DISABLED_PROVIDER_TOOLS,
    McpStdioTimeouts,
    McpTransportError,
    StdioMcpToolBroker,
)


def _server_command(mode: str, *, secret_name: str = "") -> tuple[str, ...]:
    script = r"""
import json, os, sys, time
mode = sys.argv[1]
secret_name = sys.argv[2]

def read():
    return json.loads(sys.stdin.buffer.readline())

def write(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()

initialize = read()
if mode == "oversize-initialize":
    sys.stdout.write('{"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"' + "x" * 5000)
    sys.stdout.flush()
    time.sleep(2)
    raise SystemExit
write({
    "jsonrpc": "2.0",
    "id": initialize["id"],
    "result": {
        "protocolVersion": "2025-06-18",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "fake-paper-search", "version": "0.1.4"},
    },
})
read()
call = read()
if mode == "stderr":
    sys.stderr.write("SENSITIVE-STDERR\n")
    sys.stderr.flush()
if mode == "secret-check":
    value = os.environ.get(secret_name)
    payload = [{"title": "clean" if value is None else value, "authors": []}]
elif mode == "timeout":
    time.sleep(2)
    raise SystemExit
elif mode == "eof":
    raise SystemExit
elif mode == "oversize-response":
    sys.stdout.write(
        '{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"'
        + "x" * 5000
    )
    sys.stdout.flush()
    time.sleep(2)
    raise SystemExit
elif mode == "multiple-small-frames":
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"value": "x" * 600}}, separators=(",", ":")) + "\n")
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": call["id"], "result": {"content": [{"type": "text", "text": json.dumps([{ "title": "many frames", "authors": [] }])}]}}, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    raise SystemExit
elif mode == "ignore-tool-write":
    time.sleep(2)
    raise SystemExit
elif mode == "rpc-error":
    write({"jsonrpc": "2.0", "id": call["id"], "error": {"code": -32000, "message": "secret"}})
    raise SystemExit
elif mode == "tool-error":
    write({
        "jsonrpc": "2.0",
        "id": call["id"],
        "result": {"isError": True, "content": [{"type": "text", "text": "secret"}]},
    })
    raise SystemExit
elif mode == "provider-rate-limited":
    write({
        "jsonrpc": "2.0",
        "id": call["id"],
        "result": {
            "isError": True,
            "content": [{
                "type": "text",
                "text": "Error executing tool: PAPER_SEARCH_MCP_ERROR:rate-limited",
            }],
        },
    })
    raise SystemExit
elif mode == "spoofed-provider-error":
    write({
        "jsonrpc": "2.0",
        "id": call["id"],
        "result": {
            "isError": True,
            "content": [{
                "type": "text",
                "text": "PAPER_SEARCH_MCP_ERROR:rate-limited trailing text",
            }],
        },
    })
    raise SystemExit
elif mode == "invalid-json":
    sys.stdout.write('not-json\n')
    sys.stdout.flush()
    raise SystemExit
elif mode == "structured":
    write({
        "jsonrpc": "2.0",
        "id": call["id"],
        "result": {"structuredContent": {"result": [{"title": "structured", "authors": []}]}},
    })
elif mode == "multi-content-structured":
    write({
        "jsonrpc": "2.0",
        "id": call["id"],
        "result": {
            "content": [
                {"type": "text", "text": "paper one"},
                {"type": "text", "text": "paper two"},
            ],
            "structuredContent": {"result": [{"title": "one"}, {"title": "two"}]},
        },
    })
    raise SystemExit
elif mode == "single-content-structured":
    write({
        "jsonrpc": "2.0",
        "id": call["id"],
        "result": {
            "content": [{"type": "text", "text": json.dumps({"title": "single"})}],
            "structuredContent": {"result": [{"title": "single"}]},
        },
    })
    raise SystemExit
else:
    payload = [{"title": "text", "authors": []}]
write({
    "jsonrpc": "2.0",
    "id": call["id"],
    "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
})
"""
    return (sys.executable, "-c", script, mode, secret_name)


def _broker(
    mode: str,
    *,
    timeout: float = 2.0,
    response_timeout: float | None = None,
    max_frame_bytes: int = 2 * 1024 * 1024,
    secret_name: str = "",
) -> StdioMcpToolBroker:
    return StdioMcpToolBroker(
        command=_server_command(mode, secret_name=secret_name),
        timeouts=McpStdioTimeouts(
            start_seconds=timeout,
            response_seconds=response_timeout if response_timeout is not None else timeout,
            total_seconds=max(3.0, timeout + (response_timeout if response_timeout is not None else timeout)),
        ),
        max_frame_bytes=max_frame_bytes,
    )


@pytest.fixture(autouse=True)
def enable_transport_only_for_protocol_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Protocol tests bypass production provider policy, not its transport code."""

    empty: frozenset[str] = frozenset()
    monkeypatch.setattr(broker_module, "DISABLED_PROVIDER_TOOLS", empty)


def _call(broker: StdioMcpToolBroker, tool_name: str = "search_pubmed") -> object:
    arguments: Mapping[str, object] = {"query": "bounded", "max_results": 2}
    return broker.call_tool(tool_name=tool_name, arguments=arguments)


def test_connector_starts_in_an_isolated_process_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    process = object()

    def fake_popen(command: object, **kwargs: object) -> object:
        captured["command"] = command
        captured.update(kwargs)
        return process

    monkeypatch.setattr(broker_module.subprocess, "Popen", fake_popen)
    start_process = cast(
        Callable[[], object],
        getattr(_broker("text"), "_start_process"),
    )
    started = start_process()

    assert started is process
    assert captured["shell"] is False
    assert captured["close_fds"] is True
    assert captured["start_new_session"] is True


def test_connector_shutdown_terminates_the_isolated_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[tuple[int, int]] = []

    class Process:
        stdin = None
        stdout = None
        stderr = None
        pid = 4321

        def poll(self) -> None:
            return None

        def wait(self, *, timeout: float) -> int:
            assert timeout == 1
            return 0

        def terminate(self) -> None:
            raise AssertionError("process-group termination should be used")

        def kill(self) -> None:
            raise AssertionError("process-group kill should not be needed")

    def fake_killpg(pid: int, sent_signal: int) -> None:
        signals.append((pid, sent_signal))

    monkeypatch.setattr(broker_module.os, "killpg", fake_killpg)
    close_process = cast(
        Callable[[Any], None],
        getattr(StdioMcpToolBroker, "_close_process"),
    )
    close_process(Process())

    assert signals == [(4321, signal.SIGTERM)]


@pytest.mark.parametrize("mode", ["text", "stderr"])
def test_decodes_single_json_text_result_without_exposing_stderr(mode: str) -> None:
    assert _call(_broker(mode)) == [{"title": "text", "authors": []}]


def test_accepts_structured_content() -> None:
    assert _call(_broker("structured")) == [{"title": "structured", "authors": []}]


def test_prefers_structured_result_list_over_multi_content() -> None:
    assert _call(_broker("multi-content-structured")) == [
        {"title": "one"},
        {"title": "two"},
    ]


def test_prefers_structured_result_list_over_single_content_object() -> None:
    assert _call(_broker("single-content-structured")) == [{"title": "single"}]


def test_rejects_non_allowlisted_tool_before_process_start() -> None:
    with pytest.raises(KnownMcpToolFailure) as error:
        _call(_broker("text"), "download_arxiv")
    assert error.value.code == "tool-not-allowed-permanent"


@pytest.mark.parametrize(
    "tool_name",
    ["search_arxiv", "search_pubmed"],
)
def test_unreliable_providers_are_disabled_before_any_connector_process_is_spawned(
    tmp_path: Path,
    tool_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DISABLED_PROVIDER_TOOLS == frozenset(
        {"search_arxiv", "search_pubmed"}
    )
    monkeypatch.setattr(broker_module, "DISABLED_PROVIDER_TOOLS", DISABLED_PROVIDER_TOOLS)
    broker = StdioMcpToolBroker(command=(os.fspath(tmp_path / "must-not-run"),))
    with pytest.raises(KnownMcpToolFailure) as error:
        _call(broker, tool_name)
    assert error.value.code == "provider-disabled"
    assert error.value.safe_to_retry is False


def test_openalex_is_not_disabled_and_spawns_the_allowlisted_connector() -> None:
    assert "search_openalex" not in DISABLED_PROVIDER_TOOLS
    assert _call(_broker("text"), "search_openalex") == [{"title": "text", "authors": []}]


@pytest.mark.parametrize("mode", ["eof", "timeout", "invalid-json"])
def test_post_send_transport_failures_remain_unknown(mode: str) -> None:
    with pytest.raises(McpTransportError) as error:
        _call(_broker(mode, timeout=2, response_timeout=0.1))
    assert "secret" not in str(error.value).lower()


@pytest.mark.parametrize("mode", ["rpc-error", "tool-error"])
def test_received_terminal_errors_are_known_and_not_retryable(mode: str) -> None:
    with pytest.raises(KnownMcpToolFailure) as error:
        _call(_broker(mode))
    assert error.value.safe_to_retry is False
    assert "secret" not in str(error.value).lower()


def test_classified_provider_error_grants_only_the_named_safe_retry() -> None:
    with pytest.raises(KnownMcpToolFailure) as error:
        _call(_broker("provider-rate-limited"), "search_crossref")
    assert error.value.code == "rate-limited"
    assert error.value.safe_to_retry is True


def test_provider_error_marker_with_untrusted_suffix_is_not_classified() -> None:
    with pytest.raises(KnownMcpToolFailure) as error:
        _call(_broker("spoofed-provider-error"), "search_crossref")
    assert error.value.code == "tool-failure-permanent"
    assert error.value.safe_to_retry is False


def test_frame_limit_is_enforced_before_json_decode() -> None:
    with pytest.raises(McpTransportError, match="byte limit"):
        _call(_broker("oversize-response", max_frame_bytes=1024))


def test_multiple_individually_bounded_frames_in_one_read_are_accepted() -> None:
    assert _call(_broker("multiple-small-frames", max_frame_bytes=1024)) == [
        {"title": "many frames", "authors": []}
    ]


def test_tool_write_is_nonblocking_and_deadline_bound() -> None:
    broker = _broker("ignore-tool-write", timeout=2, response_timeout=0.1)
    with pytest.raises(McpTransportError, match="timed out|closed"):
        broker.call_tool(
            tool_name="search_arxiv",
            arguments={"query": "x" * 1_500_000, "max_results": 2},
        )


def test_connector_environment_drops_prefixed_and_unrelated_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_name = "PAPER_SEARCH_MCP_TEST_SECRET"
    monkeypatch.setenv(secret_name, "must-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    result = _call(_broker("secret-check", secret_name=secret_name))
    assert result == [{"title": "clean", "authors": []}]


def test_invalid_timeout_and_frame_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        McpStdioTimeouts(start_seconds=0)
    with pytest.raises(ValueError):
        StdioMcpToolBroker(command=("python",), max_frame_bytes=512)


def test_missing_connector_is_safe_to_retry_before_any_send(tmp_path: Path) -> None:
    missing = os.fspath(tmp_path / "missing-python")
    broker = StdioMcpToolBroker(command=(missing,))
    with pytest.raises(KnownMcpToolFailure) as error:
        _call(broker)
    assert error.value.code == "connector-unavailable"
    assert error.value.safe_to_retry is True
