"""Restricted stdio transport for the pinned paper-search MCP server.

The broker is intentionally small: it starts one connector process per approved
operation, performs the MCP initialize handshake, sends exactly one allowlisted
tool call, and then closes the process.  Science Core persists the operation as
sent-authorized before entering this transport, so every interruption after the
tool request is written is reported as an unknown outcome rather than a safe
retry.
"""

from __future__ import annotations

import contextlib
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import BinaryIO, cast

from .discovery_adapter import (
    PAPER_SEARCH_ALLOWED_TOOLS,
    KnownMcpToolFailure,
    McpToolBroker,
)

MCP_PROTOCOL_VERSION = "2025-06-18"
MAX_MCP_FRAME_BYTES = 2 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_MCP_MESSAGES = 128
DISABLED_PROVIDER_TOOLS = frozenset(
    {"search_arxiv", "search_pubmed"}
)
_PROVIDER_ERROR_MARKER = "PAPER_SEARCH_MCP_ERROR:"
_PROVIDER_ERROR_CODES = frozenset(
    {
        "rate-limited",
        "connector-unavailable",
        "provider-rejected",
        "provider-response-invalid",
    }
)


class McpTransportError(RuntimeError):
    """An MCP transport interruption whose remote outcome may be unknown."""


@dataclass(frozen=True, slots=True)
class McpStdioTimeouts:
    start_seconds: float = 10.0
    response_seconds: float = 45.0
    total_seconds: float = 60.0

    def __post_init__(self) -> None:
        if (
            self.start_seconds <= 0
            or self.response_seconds <= 0
            or self.total_seconds <= 0
            or self.start_seconds > self.total_seconds
            or self.response_seconds > self.total_seconds
        ):
            raise ValueError("MCP timeouts must be positive and bounded by total_seconds")


class StdioMcpToolBroker(McpToolBroker):
    """Call one approved tool through a fresh, bounded MCP stdio process."""

    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        timeouts: McpStdioTimeouts | None = None,
        max_frame_bytes: int = MAX_MCP_FRAME_BYTES,
    ) -> None:
        resolved = tuple(command or (sys.executable, "-m", "paper_search_mcp.server"))
        if not resolved or not resolved[0] or any("\x00" in item for item in resolved):
            raise ValueError("MCP command must contain a valid executable and arguments")
        if max_frame_bytes < 1024 or max_frame_bytes > MAX_MCP_FRAME_BYTES:
            raise ValueError("MCP frame limit is outside the supported range")
        self._command = resolved
        self._timeouts = timeouts or McpStdioTimeouts()
        self._max_frame_bytes = max_frame_bytes

    def call_tool(self, *, tool_name: str, arguments: Mapping[str, object]) -> object:
        if tool_name not in PAPER_SEARCH_ALLOWED_TOOLS:
            raise KnownMcpToolFailure(
                "tool-not-allowed",
                "The requested MCP tool is not allowlisted.",
                safe_to_retry=False,
            )
        if tool_name in DISABLED_PROVIDER_TOOLS:
            # The pinned paper-search-mcp 0.1.4 release does not preserve a
            # reliable failure distinction for these providers (and arXiv also
            # has plaintext/stdout defects). A returned empty list could be a
            # hidden transport failure, so do not spawn them as evidence input.
            raise KnownMcpToolFailure(
                "provider-disabled",
                "This paper-search provider is disabled in the pinned connector release.",
                safe_to_retry=False,
            )
        started = time.monotonic()
        process = self._start_process()
        stderr_seen = bytearray()
        try:
            try:
                self._write_message(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": MCP_PROTOCOL_VERSION,
                            "capabilities": {},
                            "clientInfo": {"name": "spark-agent-core", "version": "1"},
                        },
                    },
                    phase_deadline=min(
                        started + self._timeouts.start_seconds,
                        started + self._timeouts.total_seconds,
                    ),
                )
                initialized = self._read_response(
                    process,
                    expected_id=1,
                    phase_timeout=self._timeouts.start_seconds,
                    total_started=started,
                    stderr_seen=stderr_seen,
                )
                self._validate_initialize(initialized)
                self._write_message(
                    process,
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    phase_deadline=min(
                        started + self._timeouts.start_seconds,
                        started + self._timeouts.total_seconds,
                    ),
                )
            except McpTransportError as error:
                # No tools/call write has been attempted, so this is the only
                # transport phase that is provably safe to retry.
                raise KnownMcpToolFailure(
                    "connector-unavailable",
                    "The paper-search connector did not become ready.",
                    safe_to_retry=True,
                ) from error

            # Once this frame is written the provider may have received the
            # request.  Any later transport failure must remain outcome-unknown.
            self._write_message(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": dict(arguments)},
                },
                phase_deadline=min(
                    time.monotonic() + self._timeouts.response_seconds,
                    started + self._timeouts.total_seconds,
                ),
            )
            response = self._read_response(
                process,
                expected_id=2,
                phase_timeout=self._timeouts.response_seconds,
                total_started=started,
                stderr_seen=stderr_seen,
            )
            return self._decode_tool_result(response)
        finally:
            self._close_process(process)

    def _start_process(self) -> subprocess.Popen[bytes]:
        try:
            return subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_connector_environment(),
                shell=False,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as error:
            raise KnownMcpToolFailure(
                "connector-unavailable",
                "The paper-search connector could not be started.",
                safe_to_retry=True,
            ) from error

    def _write_message(
        self,
        process: subprocess.Popen[bytes],
        message: Mapping[str, object],
        *,
        phase_deadline: float,
    ) -> None:
        stdin = process.stdin
        if stdin is None:
            raise McpTransportError("MCP connector stdin is unavailable")
        payload = _canonical_json(message) + b"\n"
        if len(payload) > self._max_frame_bytes:
            raise KnownMcpToolFailure(
                "request-too-large",
                "The MCP request exceeds the configured frame limit.",
                safe_to_retry=False,
            )
        descriptor = stdin.fileno()
        try:
            was_blocking = os.get_blocking(descriptor)
            os.set_blocking(descriptor, False)
        except OSError as error:
            raise McpTransportError("MCP connector stdin is unavailable") from error
        offset = 0
        selector = selectors.DefaultSelector()
        try:
            selector.register(descriptor, selectors.EVENT_WRITE)
            while offset < len(payload):
                remaining = phase_deadline - time.monotonic()
                if remaining <= 0:
                    raise McpTransportError("MCP connector request timed out before it was written")
                if not selector.select(remaining):
                    raise McpTransportError("MCP connector request timed out before it was written")
                try:
                    written = os.write(descriptor, payload[offset:])
                except BlockingIOError:
                    continue
                except (BrokenPipeError, OSError) as error:
                    raise McpTransportError(
                        "MCP connector closed before accepting the request"
                    ) from error
                if written <= 0:
                    raise McpTransportError("MCP connector closed before accepting the request")
                offset += written
        finally:
            selector.close()
            with contextlib.suppress(OSError):
                os.set_blocking(descriptor, was_blocking)

    def _read_response(
        self,
        process: subprocess.Popen[bytes],
        *,
        expected_id: int,
        phase_timeout: float,
        total_started: float,
        stderr_seen: bytearray,
    ) -> dict[str, object]:
        stdout = process.stdout
        stderr = process.stderr
        if stdout is None or stderr is None:
            raise McpTransportError("MCP connector pipes are unavailable")
        selector = selectors.DefaultSelector()
        selector.register(stdout, selectors.EVENT_READ, "stdout")
        selector.register(stderr, selectors.EVENT_READ, "stderr")
        buffer = bytearray()
        message_count = 0
        phase_deadline = time.monotonic() + phase_timeout
        total_deadline = total_started + self._timeouts.total_seconds
        try:
            while True:
                now = time.monotonic()
                remaining = min(phase_deadline, total_deadline) - now
                if remaining <= 0:
                    raise McpTransportError("MCP connector response timed out")
                events = selector.select(remaining)
                if not events:
                    raise McpTransportError("MCP connector response timed out")
                for key, _ in events:
                    stream = cast(BinaryIO, key.fileobj)
                    chunk = os.read(stream.fileno(), 8192)
                    if key.data == "stderr":
                        if not chunk:
                            selector.unregister(stream)
                        elif len(stderr_seen) < MAX_STDERR_BYTES:
                            remaining_stderr = MAX_STDERR_BYTES - len(stderr_seen)
                            stderr_seen.extend(chunk[:remaining_stderr])
                        continue
                    if not chunk:
                        raise McpTransportError("MCP connector closed before a response")
                    buffer.extend(chunk)
                    while b"\n" in buffer:
                        raw_line, _, remainder = buffer.partition(b"\n")
                        buffer = bytearray(remainder)
                        if not raw_line.strip():
                            continue
                        if len(raw_line) > self._max_frame_bytes:
                            raise McpTransportError("MCP connector frame exceeds the byte limit")
                        message_count += 1
                        if message_count > MAX_MCP_MESSAGES:
                            raise McpTransportError("MCP connector emitted too many messages")
                        message = _decode_frame(bytes(raw_line))
                        if message.get("id") == expected_id:
                            return message
                        # Notifications and responses for other request IDs are
                        # ignored, but each was independently byte-bounded.
                    if len(buffer) > self._max_frame_bytes:
                        raise McpTransportError("MCP connector frame exceeds the byte limit")
        finally:
            selector.close()

    @staticmethod
    def _validate_initialize(message: Mapping[str, object]) -> None:
        if "error" in message:
            raise KnownMcpToolFailure(
                "initialize-failed",
                "The paper-search connector rejected initialization.",
                safe_to_retry=False,
            )
        result = message.get("result")
        if not isinstance(result, Mapping):
            raise McpTransportError("MCP initialize response is malformed")
        typed_result = cast(Mapping[str, object], result)
        if not isinstance(typed_result.get("serverInfo"), Mapping):
            raise McpTransportError("MCP initialize response is malformed")

    @staticmethod
    def _decode_tool_result(message: Mapping[str, object]) -> object:
        if "error" in message:
            raise KnownMcpToolFailure(
                "connector-failure",
                "The paper-search connector returned a terminal JSON-RPC error.",
                safe_to_retry=False,
            )
        result = message.get("result")
        if not isinstance(result, Mapping):
            raise McpTransportError("MCP tool response is malformed")
        typed_result = cast(Mapping[str, object], result)
        if typed_result.get("isError") is True:
            provider_code = _provider_error_code(typed_result.get("content"))
            if provider_code is not None:
                raise KnownMcpToolFailure(
                    provider_code,
                    "The paper-search provider returned a classified failure.",
                    safe_to_retry=provider_code in {"rate-limited", "connector-unavailable"},
                )
            raise KnownMcpToolFailure(
                "tool-failure",
                "The paper-search connector returned a terminal tool error.",
                safe_to_retry=False,
            )
        # MCP SDK 1.28.1 returns one content item per paper and the
        # authoritative list in structuredContent.result.  Prefer that typed
        # payload; concatenating or selecting content would change the result
        # shape and could silently drop candidates.
        structured = typed_result.get("structuredContent")
        if isinstance(structured, Mapping):
            typed_structured = cast(Mapping[str, object], structured)
            structured_result = typed_structured.get("result")
            if isinstance(structured_result, list):
                return cast(list[object], structured_result)
        content = typed_result.get("content")
        if isinstance(content, list):
            typed_content = cast(list[object], content)
            if len(typed_content) == 1:
                item = typed_content[0]
                if (
                    isinstance(item, Mapping)
                    and cast(Mapping[str, object], item).get("type") == "text"
                    and isinstance(cast(Mapping[str, object], item).get("text"), str)
                ):
                    text = cast(str, cast(Mapping[str, object], item).get("text"))
                    try:
                        decoded = json.loads(text)
                    except json.JSONDecodeError as error:
                        raise McpTransportError("MCP tool response text is not JSON") from error
                    if isinstance(decoded, list):
                        return cast(list[object], decoded)
                    raise McpTransportError("MCP tool response text must encode a result list")
        raise McpTransportError("MCP tool response must contain one structured value")

    @staticmethod
    def _close_process(process: subprocess.Popen[bytes]) -> None:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                process.kill()
            process.wait(timeout=1)


def _connector_environment() -> dict[str, str]:
    """Return a minimal environment with no connector credentials or config."""

    environment: dict[str, str] = {}
    for name in ("PATH", "LANG", "LC_ALL", "PYTHONPATH", "PYTHONHOME"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    environment.update(
        {
            "HOME": "/tmp",
            "XDG_CONFIG_HOME": "/tmp/spark-paper-search-config",
            # Prevent python-dotenv in the connector from discovering a user
            # configuration file with unrelated secrets.
            "PAPER_SEARCH_MCP_ENV_FILE": "/dev/null",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _decode_frame(payload: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise McpTransportError("MCP connector emitted invalid JSON") from error
    if not isinstance(decoded, dict):
        raise McpTransportError("MCP connector emitted an invalid JSON-RPC frame")
    typed_decoded = cast(dict[str, object], decoded)
    if typed_decoded.get("jsonrpc") != "2.0":
        raise McpTransportError("MCP connector emitted an invalid JSON-RPC frame")
    return typed_decoded


def _provider_error_code(content: object) -> str | None:
    if not isinstance(content, list):
        return None
    typed_content = cast(list[object], content)
    if len(typed_content) != 1:
        return None
    item = typed_content[0]
    if not isinstance(item, Mapping):
        return None
    typed_item = cast(Mapping[str, object], item)
    text = typed_item.get("text")
    if (
        typed_item.get("type") != "text"
        or not isinstance(text, str)
        or len(text) > 512
        or text.count(_PROVIDER_ERROR_MARKER) != 1
    ):
        return None
    code = text.rpartition(_PROVIDER_ERROR_MARKER)[2]
    return code if code in _PROVIDER_ERROR_CODES else None


__all__ = [
    "MAX_MCP_FRAME_BYTES",
    "MCP_PROTOCOL_VERSION",
    "McpStdioTimeouts",
    "McpTransportError",
    "StdioMcpToolBroker",
]
