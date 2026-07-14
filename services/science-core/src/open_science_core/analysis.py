from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import settings


_ALLOWED_ARTIFACTS: dict[str, tuple[str, str]] = {
    ".ipynb": ("notebook", "application/x-ipynb+json"),
    ".png": ("figure", "image/png"),
    ".csv": ("dataset", "text/csv"),
    ".json": ("structured-data", "application/json"),
    ".txt": ("log", "text/plain"),
    ".log": ("log", "text/plain"),
}
_RESERVED_ARTIFACTS: dict[str, tuple[str, str]] = {
    "input.ipynb": ("notebook-input", "application/x-ipynb+json"),
    "executed.ipynb": ("notebook-executed", "application/x-ipynb+json"),
    "environment.json": ("environment", "application/json"),
    "stdout.txt": ("stdout", "text/plain"),
    "stderr.txt": ("stderr", "text/plain"),
    "execution.log": ("log", "text/plain"),
}
_REQUIRED_RUNTIME_FILES = {"executed.ipynb", "stdout.txt", "stderr.txt", "execution.log"}
_MAX_ARTIFACT_FILES = 200
_MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
_FORBIDDEN_MODULES = {"pty", "socket", "subprocess"}
_FORBIDDEN_IPYTHON_METHODS = {"getoutput", "system"}
_FORBIDDEN_IPYTHON_MAGICS = {"bash", "script", "sh", "sx", "system"}


class RuntimeServiceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeArtifactInfo:
    path: str
    mime_type: str
    content_hash: str
    size_bytes: int
    artifact_type: str


@dataclass(frozen=True, slots=True)
class RuntimeExecutionResult:
    status: str
    environment_hash: str
    stdout: str
    stderr: str
    log: str
    artifacts: list[RuntimeArtifactInfo]


@dataclass(frozen=True, slots=True)
class CollectedArtifact:
    absolute_path: Path
    project_relative_path: str
    artifact_type: str
    mime_type: str
    content_hash: str
    size_bytes: int


def canonical_analysis_payload(
    dataset_source_id: str, objective: str, code: str
) -> tuple[bytes, str]:
    encoded = json.dumps(
        {
            "code": code,
            "datasetSourceId": dataset_source_id,
            "objective": objective,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return encoded, hashlib.sha256(encoded).hexdigest()


def validate_csv(content: bytes) -> None:
    if not content:
        raise ValueError("CSV is empty")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV must be UTF-8 encoded") from error
    if "\x00" in decoded:
        raise ValueError("CSV contains NUL bytes")
    try:
        reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
        first_row = next(reader, None)
        if first_row is None or not first_row or not any(cell.strip() for cell in first_row):
            raise ValueError("CSV has no usable first row")
        # Force parsing now so malformed quoting is rejected before persistence.
        for _row in reader:
            pass
    except csv.Error as error:
        raise ValueError(f"Malformed CSV: {error}") from error


def validate_python_code(code: str) -> None:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as error:
        location = f" on line {error.lineno}" if error.lineno is not None else ""
        raise ValueError(f"Python code has invalid syntax{location}: {error.msg}") from error
    _NoShellPolicy().visit(tree)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def execute_in_runtime(
    *,
    run_id: str,
    run_dir: Path,
    dataset_path: Path,
    objective: str,
    code: str,
    payload_sha256: str,
) -> RuntimeExecutionResult:
    timeout_seconds = settings.execution_timeout_seconds
    timeout = httpx.Timeout(timeout_seconds + 5, connect=5.0)
    transport = httpx.AsyncHTTPTransport(uds=str(settings.runtime_socket_path))
    try:
        async with httpx.AsyncClient(
            base_url="http://science-runtime",
            timeout=timeout,
            transport=transport,
            trust_env=False,
        ) as client:
            response = await client.post(
                "/v1/execute",
                json={
                    "runId": run_id,
                    "runDir": str(run_dir),
                    "datasetPath": str(dataset_path),
                    "objective": objective,
                    "code": code,
                    "timeoutSeconds": timeout_seconds,
                    "payloadSha256": payload_sha256,
                },
            )
    except httpx.TimeoutException as error:
        raise RuntimeServiceError(
            f"science-runtime exceeded the {timeout_seconds}-second execution limit"
        ) from error
    except httpx.HTTPError as error:
        raise RuntimeServiceError(f"science-runtime transport failed: {error}") from error

    if response.status_code != 200:
        detail = response.text[:1_000]
        raise RuntimeServiceError(
            f"science-runtime rejected execution ({response.status_code}): {detail}"
        )
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("response is not an object")
        execution_status = payload["status"]
        environment_hash = payload["environmentHash"]
        stdout = payload["stdout"]
        stderr = payload["stderr"]
        log = payload["log"]
        raw_artifacts = payload["artifacts"]
        if execution_status not in {"completed", "failed"}:
            raise ValueError("invalid status")
        if not isinstance(environment_hash, str) or len(environment_hash) != 64:
            raise ValueError("invalid environmentHash")
        if not all(isinstance(value, str) for value in (stdout, stderr, log)):
            raise TypeError("stdout, stderr, and log must be strings")
        if not isinstance(raw_artifacts, list):
            raise TypeError("artifacts must be an array")
        artifacts = [_parse_runtime_artifact(item) for item in raw_artifacts]
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeServiceError(f"science-runtime returned an invalid response: {error}") from error

    return RuntimeExecutionResult(
        status=execution_status,
        environment_hash=environment_hash,
        stdout=stdout,
        stderr=stderr,
        log=log,
        artifacts=artifacts,
    )


def collect_runtime_artifacts(
    *,
    runtime_result: RuntimeExecutionResult,
    exchange_run_dir: Path,
    final_run_dir: Path,
    project_dir: Path,
) -> list[CollectedArtifact]:
    exchange_run_dir = exchange_run_dir.resolve()
    final_run_dir = final_run_dir.resolve()
    project_dir = project_dir.resolve()
    exchange_root = settings.runtime_exchange_dir.resolve()
    _assert_beneath(exchange_root, exchange_run_dir)
    _assert_beneath(project_dir, final_run_dir)

    runtime_by_path: dict[Path, RuntimeArtifactInfo] = {}
    for item in runtime_result.artifacts:
        relative = Path(item.path)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeServiceError("science-runtime returned an unsafe artifact path")
        absolute = (exchange_root / relative).resolve()
        _assert_beneath(exchange_run_dir, absolute)
        if absolute in runtime_by_path:
            raise RuntimeServiceError("science-runtime returned a duplicate artifact path")
        runtime_by_path[absolute] = item

    files: list[Path] = []
    for root, directory_names, file_names in os.walk(exchange_run_dir, followlinks=False):
        root_path = Path(root)
        for directory_name in directory_names:
            if (root_path / directory_name).is_symlink():
                raise RuntimeServiceError("runtime output contains a symbolic-link directory")
        for file_name in file_names:
            path = root_path / file_name
            if path.is_symlink() or not path.is_file():
                raise RuntimeServiceError("runtime output contains a non-regular file")
            if path.name == "input.csv":
                continue
            if path.suffix.lower() in _ALLOWED_ARTIFACTS:
                files.append(path.resolve())

    if len(files) > _MAX_ARTIFACT_FILES:
        raise RuntimeServiceError("runtime produced too many artifact files")
    present_names = {path.name for path in files}
    missing = sorted(_REQUIRED_RUNTIME_FILES - present_names)
    if missing:
        raise RuntimeServiceError(f"runtime did not produce required files: {', '.join(missing)}")

    total_size = 0
    collected: list[CollectedArtifact] = []
    for path in sorted(files):
        _assert_beneath(exchange_run_dir, path)
        runtime_info = runtime_by_path.get(path)
        if runtime_info is None:
            raise RuntimeServiceError(
                "runtime omitted artifact integrity metadata for "
                f"{path.relative_to(exchange_run_dir)}"
            )
        if total_size + runtime_info.size_bytes > _MAX_ARTIFACT_BYTES:
            raise RuntimeServiceError("runtime artifact output exceeds the size limit")
        destination = final_run_dir / path.relative_to(exchange_run_dir)
        content_hash, size = _copy_and_hash_regular_file(path, destination)
        total_size += size
        if total_size > _MAX_ARTIFACT_BYTES:
            raise RuntimeServiceError("runtime artifact output exceeds the size limit")
        if runtime_info.content_hash != content_hash or runtime_info.size_bytes != size:
            destination.unlink(missing_ok=True)
            raise RuntimeServiceError(
                f"runtime artifact integrity mismatch for {path.relative_to(exchange_run_dir)}"
            )
        artifact_type, mime_type = _RESERVED_ARTIFACTS.get(
            path.name, _ALLOWED_ARTIFACTS[path.suffix.lower()]
        )
        collected.append(
            CollectedArtifact(
                absolute_path=destination,
                project_relative_path=destination.relative_to(project_dir).as_posix(),
                artifact_type=artifact_type,
                mime_type=mime_type,
                content_hash=content_hash,
                size_bytes=size,
            )
        )

    unexpected = set(runtime_by_path) - set(files)
    if unexpected:
        names = ", ".join(sorted(path.name for path in unexpected))
        raise RuntimeServiceError(f"runtime reported unexpected artifacts: {names}")
    return collected


def _copy_and_hash_regular_file(source: Path, destination: Path) -> tuple[str, int]:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    source_flags = os.O_RDONLY
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
        destination_flags |= os.O_NOFOLLOW

    source_descriptor = os.open(source, source_flags)
    destination_descriptor: int | None = None
    try:
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise RuntimeServiceError(f"runtime artifact is not a private regular file: {source}")
        destination_descriptor = os.open(destination, destination_flags, 0o400)
        digest = hashlib.sha256()
        copied = 0
        while chunk := os.read(source_descriptor, 1024 * 1024):
            digest.update(chunk)
            view = memoryview(chunk)
            written = 0
            while written < len(view):
                written += os.write(destination_descriptor, view[written:])
            copied += len(chunk)
        os.fsync(destination_descriptor)
        after = os.fstat(source_descriptor)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise RuntimeServiceError(f"runtime artifact changed while copying: {source}")
        if copied != after.st_size:
            raise RuntimeServiceError(f"runtime artifact copy was incomplete: {source}")
        return digest.hexdigest(), copied
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        os.close(source_descriptor)


def read_text_file(path: Path, *, max_bytes: int = 2 * 1024 * 1024) -> str:
    with path.open("rb") as handle:
        content = handle.read(max_bytes + 1)
    if len(content) > max_bytes:
        return content[:max_bytes].decode("utf-8", errors="replace") + "\n[truncated]"
    return content.decode("utf-8", errors="replace")


def _parse_runtime_artifact(value: Any) -> RuntimeArtifactInfo:
    if not isinstance(value, dict):
        raise TypeError("artifact is not an object")
    path = value.get("path")
    mime_type = value.get("mimeType")
    content_hash = value.get("contentHash")
    size_bytes = value.get("sizeBytes")
    artifact_type = value.get("artifactType")
    if not all(isinstance(item, str) for item in (path, mime_type, content_hash, artifact_type)):
        raise TypeError("artifact string fields are invalid")
    if not isinstance(size_bytes, int) or size_bytes < 0:
        raise TypeError("artifact sizeBytes is invalid")
    if len(content_hash) != 64:
        raise ValueError("artifact contentHash is invalid")
    return RuntimeArtifactInfo(
        path=path,
        mime_type=mime_type,
        content_hash=content_hash,
        size_bytes=size_bytes,
        artifact_type=artifact_type,
    )


def _assert_beneath(parent: Path, candidate: Path) -> None:
    try:
        candidate.relative_to(parent)
    except ValueError as error:
        raise RuntimeServiceError(f"path escapes allowed directory: {candidate}") from error


class _NoShellPolicy(ast.NodeVisitor):
    def __init__(self) -> None:
        self._os_aliases: set[str] = set()
        self._forbidden_os_call_aliases: set[str] = set()
        self._importlib_aliases: set[str] = set()
        self._import_module_aliases: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in _FORBIDDEN_MODULES:
                self._reject(node, f"importing {root} is not allowed")
            if alias.name == "os" or (alias.name.startswith("os.") and alias.asname is None):
                self._os_aliases.add(alias.asname or "os")
            if alias.name == "importlib" or (
                alias.name.startswith("importlib.") and alias.asname is None
            ):
                self._importlib_aliases.add(alias.asname or "importlib")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = (node.module or "").split(".", 1)[0]
        if root in _FORBIDDEN_MODULES:
            self._reject(node, f"importing from {root} is not allowed")
        if node.module == "os":
            for alias in node.names:
                if alias.name == "*":
                    self._reject(node, "wildcard import from os is not allowed")
                if _is_forbidden_os_method(alias.name):
                    self._forbidden_os_call_aliases.add(alias.asname or alias.name)
        if node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    self._import_module_aliases.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if isinstance(function, ast.Name):
            if function.id in self._forbidden_os_call_aliases:
                self._reject(node, f"calling {function.id} is not allowed")
            if function.id == "__import__" and _constant_module_root(node) in (
                _FORBIDDEN_MODULES | {"os"}
            ):
                self._reject(node, "dynamic import of shell-capable modules is not allowed")
            if function.id in self._import_module_aliases and _constant_module_root(node) in (
                _FORBIDDEN_MODULES | {"os"}
            ):
                self._reject(node, "dynamic import of shell-capable modules is not allowed")

        if isinstance(function, ast.Attribute):
            if (
                isinstance(function.value, ast.Name)
                and function.value.id in self._os_aliases
                and _is_forbidden_os_method(function.attr)
            ):
                self._reject(node, f"os.{function.attr} is not allowed")
            if (
                isinstance(function.value, ast.Name)
                and function.value.id in self._importlib_aliases
                and function.attr == "import_module"
                and _constant_module_root(node) in (_FORBIDDEN_MODULES | {"os"})
            ):
                self._reject(node, "dynamic import of shell-capable modules is not allowed")
            if _is_get_ipython_call(function.value):
                if function.attr in _FORBIDDEN_IPYTHON_METHODS:
                    self._reject(node, f"get_ipython().{function.attr} is not allowed")
                if function.attr in {"run_line_magic", "run_cell_magic"}:
                    magic = _constant_string(node.args[0]) if node.args else None
                    if magic in _FORBIDDEN_IPYTHON_MAGICS:
                        self._reject(node, f"IPython {magic} shell magic is not allowed")

        if _is_forbidden_getattr_call(function, self._os_aliases):
            self._reject(node, "dynamic access to an os shell method is not allowed")
        self.generic_visit(node)

    @staticmethod
    def _reject(node: ast.AST, message: str) -> None:
        line = getattr(node, "lineno", None)
        location = f" on line {line}" if line is not None else ""
        raise ValueError(f"Python code policy rejected {message}{location}")


def _is_forbidden_os_method(name: str) -> bool:
    return name in {"kill", "killpg", "popen", "system"} or name.startswith(
        ("spawn", "posix_spawn")
    )


def _constant_string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _constant_module_root(node: ast.Call) -> str | None:
    if not node.args:
        return None
    value = _constant_string(node.args[0])
    return value.split(".", 1)[0] if value is not None else None


def _is_get_ipython_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_ipython"
    )


def _is_forbidden_getattr_call(function: ast.AST, os_aliases: set[str]) -> bool:
    if not isinstance(function, ast.Call):
        return False
    if not isinstance(function.func, ast.Name) or function.func.id != "getattr":
        return False
    if len(function.args) < 2:
        return False
    target, attribute = function.args[:2]
    return (
        isinstance(target, ast.Name)
        and target.id in os_aliases
        and (name := _constant_string(attribute)) is not None
        and _is_forbidden_os_method(name)
    )
