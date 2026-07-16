from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

import httpx

from .config import settings
from .fixed_analysis_policy import (
    COMPILED_ANALYSIS_POLICY_ID,
    COMPILED_ANALYSIS_TEMPLATE,
    FIXED_ANALYSIS_POLICY_ID,
    GENERAL_ANALYSIS_POLICY_ID,
    AnalysisPolicyId,
    AnalysisPolicyTemplate,
    FixedAnalysisPolicyError,
    FixedAnalysisTemplate,
    validate_fixed_analysis_code,
)

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
_REQUIRED_RUNTIME_FILES = frozenset(_RESERVED_ARTIFACTS)
_CAPTURED_ATTESTATION_FILES = frozenset(
    {
        "input.ipynb",
        "executed.ipynb",
        "environment.json",
        "execution.log",
        "analysis-spec.json",
        "results.json",
    }
)
_MAX_POLICY_ATTESTATION_BYTES = 32 * 1024 * 1024
_MAX_RUNTIME_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_ARTIFACT_FILES = 200
_MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
_MAX_RUNTIME_ENTRIES = 1_000
_MAX_RUNTIME_DIRECTORIES = 200
_MAX_RUNTIME_DIRECTORY_DEPTH = 32
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
    attestation_bytes: bytes | None = None


@dataclass(frozen=True, slots=True)
class _StatIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int
    mode: int
    link_count: int


@dataclass(frozen=True, slots=True)
class RuntimeFile:
    relative_path: Path
    identity: _StatIdentity


@dataclass(slots=True)
class _RunDirectoryAnchor:
    exchange_root: Path
    component_names: tuple[str, ...]
    descriptors: list[int]
    identities: tuple[_StatIdentity, ...]

    @property
    def run_descriptor(self) -> int:
        return self.descriptors[-1]

    def close(self) -> None:
        for descriptor in reversed(self.descriptors):
            os.close(descriptor)


@dataclass(slots=True)
class _DestinationDirectoryAnchor:
    project_root: Path
    component_names: tuple[str, ...]
    descriptors: list[int]
    identities: tuple[_StatIdentity, ...]

    @property
    def final_run_descriptor(self) -> int:
        return self.descriptors[-1]

    def close(self) -> None:
        for descriptor in reversed(self.descriptors):
            os.close(descriptor)


@dataclass(slots=True)
class _DestinationParentAnchor:
    component_names: tuple[str, ...]
    descriptors: list[int]
    identities: tuple[_StatIdentity, ...]

    @property
    def parent_descriptor(self) -> int:
        return self.descriptors[-1]

    def close(self) -> None:
        for descriptor in reversed(self.descriptors):
            os.close(descriptor)


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


def validate_python_code(
    code: str,
    *,
    policy_profile_id: AnalysisPolicyId = GENERAL_ANALYSIS_POLICY_ID,
    policy_template: AnalysisPolicyTemplate | None = None,
    approved_code_sha256: str | None = None,
) -> None:
    if policy_profile_id == FIXED_ANALYSIS_POLICY_ID:
        if policy_template not in {"baseline", "repair-1", "repair-2"}:
            raise ValueError("Fixed analysis policy requires a template")
        if approved_code_sha256 is not None:
            raise ValueError("Fixed analysis policy does not accept an approved code hash")
        try:
            validate_fixed_analysis_code(
                code,
                template=cast(FixedAnalysisTemplate, policy_template),
            )
        except FixedAnalysisPolicyError as error:
            raise ValueError(f"Python code policy rejected {error}") from error
        return
    if policy_profile_id == COMPILED_ANALYSIS_POLICY_ID:
        if (
            policy_template != COMPILED_ANALYSIS_TEMPLATE
            or approved_code_sha256 is None
            or hashlib.sha256(code.encode("utf-8")).hexdigest()
            != approved_code_sha256
        ):
            raise ValueError("Compiled analysis code does not match its approval")
        _validate_general_python_code(code)
        return
    if (
        policy_profile_id != GENERAL_ANALYSIS_POLICY_ID
        or policy_template is not None
        or approved_code_sha256 is not None
    ):
        raise ValueError("Python code policy profile is invalid")
    _validate_general_python_code(code)


def _validate_general_python_code(code: str) -> None:
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
    policy_profile_id: AnalysisPolicyId,
    policy_template: AnalysisPolicyTemplate | None,
    analysis_spec_id: str | None = None,
    analysis_spec_sha256: str | None = None,
    dataset_profile_sha256: str | None = None,
    compiler_version: str | None = None,
    approved_code_sha256: str | None = None,
    timeout_seconds: int | None = None,
) -> RuntimeExecutionResult:
    effective_timeout_seconds = timeout_seconds or settings.execution_timeout_seconds
    compiled_provenance = (
        {
            "analysisSpecId": analysis_spec_id,
            "analysisSpecSha256": analysis_spec_sha256,
            "datasetProfileSha256": dataset_profile_sha256,
            "compilerVersion": compiler_version,
            "approvedCodeSha256": approved_code_sha256,
        }
        if analysis_spec_id is not None
        else {}
    )
    timeout = httpx.Timeout(effective_timeout_seconds + 5, connect=5.0)
    transport = httpx.AsyncHTTPTransport(uds=str(settings.runtime_socket_path))
    try:
        async with httpx.AsyncClient(
            base_url="http://science-runtime",
            timeout=timeout,
            transport=transport,
            trust_env=False,
        ) as client:
            async with client.stream(
                "POST",
                "/v1/execute",
                headers={"Accept-Encoding": "identity"},
                json={
                    "runId": run_id,
                    "runDir": str(run_dir),
                    "datasetPath": str(dataset_path),
                    "objective": objective,
                    "code": code,
                    "timeoutSeconds": effective_timeout_seconds,
                    "payloadSha256": payload_sha256,
                    "policyProfileId": policy_profile_id,
                    "policyTemplate": policy_template,
                    **compiled_provenance,
                },
            ) as response:
                if response.status_code != 200:
                    detail = (
                        await _read_runtime_response_prefix(response, max_bytes=1_000)
                    ).decode("utf-8", errors="replace")
                    raise RuntimeServiceError(
                        "science-runtime rejected execution "
                        f"({response.status_code}): {detail}"
                    )
                response_body = await _read_bounded_runtime_response(response)
    except httpx.TimeoutException as error:
        raise RuntimeServiceError(
            f"science-runtime exceeded the {effective_timeout_seconds}-second execution limit"
        ) from error
    except httpx.HTTPError as error:
        raise RuntimeServiceError(f"science-runtime transport failed: {error}") from error

    try:
        payload_object: object = json.loads(response_body)
        if not isinstance(payload_object, dict):
            raise TypeError("response is not an object")
        payload = cast(dict[str, Any], payload_object)
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
        artifacts = [
            _parse_runtime_artifact(item)
            for item in cast(list[Any], raw_artifacts)
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeServiceError(
            f"science-runtime returned an invalid response: {error}"
        ) from error

    return RuntimeExecutionResult(
        status=execution_status,
        environment_hash=environment_hash,
        stdout=stdout,
        stderr=stderr,
        log=log,
        artifacts=artifacts,
    )


async def _read_bounded_runtime_response(response: httpx.Response) -> bytes:
    content_encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if content_encoding not in {"", "identity"}:
        raise RuntimeServiceError(
            "science-runtime returned an invalid response: compressed responses are not allowed"
        )
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length, 10)
        except ValueError:
            raise RuntimeServiceError(
                "science-runtime returned an invalid response: invalid Content-Length"
            ) from None
        if declared_length < 0:
            raise RuntimeServiceError(
                "science-runtime returned an invalid response: invalid Content-Length"
            )
        if declared_length > _MAX_RUNTIME_RESPONSE_BYTES:
            raise RuntimeServiceError(
                "science-runtime returned an invalid response: response is larger than the size limit"
            )
    body = bytearray()
    chunk_size = min(64 * 1024, _MAX_RUNTIME_RESPONSE_BYTES + 1)
    async for chunk in response.aiter_raw(chunk_size=chunk_size):
        if len(body) + len(chunk) > _MAX_RUNTIME_RESPONSE_BYTES:
            raise RuntimeServiceError(
                "science-runtime returned an invalid response: response is larger than the size limit"
            )
        body.extend(chunk)
    return bytes(body)


async def _read_runtime_response_prefix(
    response: httpx.Response,
    *,
    max_bytes: int,
) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_raw(chunk_size=min(64 * 1024, max_bytes + 1)):
        remaining = max_bytes - len(body)
        if remaining <= 0:
            break
        body.extend(chunk[:remaining])
        if len(body) == max_bytes:
            break
    return bytes(body)


def collect_runtime_artifacts(
    *,
    runtime_result: RuntimeExecutionResult,
    exchange_run_dir: Path,
    final_run_dir: Path,
    project_dir: Path,
    expected_exchange_run_identity: tuple[int, int],
    expected_final_run_identity: tuple[int, int],
) -> list[CollectedArtifact]:
    exchange_root = Path(os.path.abspath(settings.runtime_exchange_dir))
    exchange_run_dir = Path(os.path.abspath(exchange_run_dir))
    final_run_dir = Path(os.path.abspath(final_run_dir))
    project_dir = Path(os.path.abspath(project_dir))
    _assert_beneath(exchange_root, exchange_run_dir)
    _assert_beneath(project_dir, final_run_dir)

    runtime_by_path: dict[Path, RuntimeArtifactInfo] = {}
    for item in runtime_result.artifacts:
        relative = Path(item.path)
        if not item.path or relative.is_absolute() or ".." in relative.parts:
            raise RuntimeServiceError("science-runtime returned an unsafe artifact path")
        absolute = exchange_root / relative
        try:
            run_relative = absolute.relative_to(exchange_run_dir)
        except ValueError as error:
            raise RuntimeServiceError("science-runtime returned an unsafe artifact path") from error
        if not run_relative.parts or run_relative == Path("."):
            raise RuntimeServiceError("science-runtime returned an unsafe artifact path")
        if run_relative in runtime_by_path:
            raise RuntimeServiceError("science-runtime returned a duplicate artifact path")
        runtime_by_path[run_relative] = item

    anchor = _open_run_directory_anchor(exchange_root, exchange_run_dir)
    try:
        if _descriptor_object_identity(anchor.run_descriptor) != expected_exchange_run_identity:
            raise RuntimeServiceError("runtime run directory identity changed before collection")
        _verify_run_directory_anchor(anchor)
        files, directory_identities = _scan_runtime_tree(anchor.run_descriptor)
        _verify_run_directory_anchor(anchor)

        if len(files) > _MAX_ARTIFACT_FILES:
            raise RuntimeServiceError("runtime produced too many artifact files")
        present_names = {item.relative_path.name for item in files}
        missing = sorted(_REQUIRED_RUNTIME_FILES - present_names)
        if missing:
            raise RuntimeServiceError(
                f"runtime did not produce required files: {', '.join(missing)}"
            )

        file_paths = {item.relative_path for item in files}
        misplaced_reserved_paths = {
            path
            for path in file_paths
            if path.name in _RESERVED_ARTIFACTS and path != Path(path.name)
        }
        if misplaced_reserved_paths:
            raise RuntimeServiceError(
                "runtime reserved artifacts must be produced at the run root"
            )
        environment_paths = {
            path for path in file_paths if path.name == "environment.json"
        }
        if environment_paths != {Path("environment.json")}:
            raise RuntimeServiceError(
                "runtime must produce exactly one root environment.json"
            )
        unexpected = set(runtime_by_path) - file_paths
        if unexpected:
            names = ", ".join(sorted(path.as_posix() for path in unexpected))
            raise RuntimeServiceError(f"runtime reported unexpected artifacts: {names}")

        scanned_size = 0
        for runtime_file in files:
            runtime_info = runtime_by_path.get(runtime_file.relative_path)
            if runtime_info is None:
                raise RuntimeServiceError(
                    "runtime omitted artifact integrity metadata for "
                    f"{runtime_file.relative_path.as_posix()}"
                )
            if runtime_file.identity.size != runtime_info.size_bytes:
                raise RuntimeServiceError(
                    "runtime artifact size metadata mismatch for "
                    f"{runtime_file.relative_path.as_posix()}"
                )
            scanned_size += runtime_file.identity.size
            if scanned_size > _MAX_ARTIFACT_BYTES:
                raise RuntimeServiceError("runtime artifact output exceeds the size limit")

        destination_anchor = _open_destination_directory_anchor(
            project_dir,
            final_run_dir,
            expected_final_run_identity,
        )
        try:
            total_size = 0
            collected: list[CollectedArtifact] = []
            for runtime_file in sorted(files, key=lambda item: item.relative_path.as_posix()):
                runtime_info = runtime_by_path.get(runtime_file.relative_path)
                if runtime_info is None:
                    raise RuntimeServiceError(
                        "runtime omitted artifact integrity metadata for "
                        f"{runtime_file.relative_path.as_posix()}"
                    )
                if total_size + runtime_info.size_bytes > _MAX_ARTIFACT_BYTES:
                    raise RuntimeServiceError("runtime artifact output exceeds the size limit")
                destination = final_run_dir / runtime_file.relative_path
                content_hash, size, attestation_bytes = _copy_runtime_file(
                    run_descriptor=anchor.run_descriptor,
                    runtime_file=runtime_file,
                    directory_identities=directory_identities,
                    destination_anchor=destination_anchor,
                    expected_content_hash=runtime_info.content_hash,
                    expected_size=runtime_info.size_bytes,
                    expected_environment_hash=(
                        runtime_result.environment_hash
                        if runtime_file.relative_path == Path("environment.json")
                        else None
                    ),
                    capture_attestation=(
                        runtime_file.relative_path == Path(runtime_file.relative_path.name)
                        and runtime_file.relative_path.name
                        in _CAPTURED_ATTESTATION_FILES
                    ),
                )
                total_size += size
                if total_size > _MAX_ARTIFACT_BYTES:
                    raise RuntimeServiceError("runtime artifact output exceeds the size limit")
                artifact_type, mime_type = _RESERVED_ARTIFACTS.get(
                    runtime_file.relative_path.name,
                    _ALLOWED_ARTIFACTS[runtime_file.relative_path.suffix.lower()],
                )
                collected.append(
                    CollectedArtifact(
                        absolute_path=destination,
                        project_relative_path=destination.relative_to(project_dir).as_posix(),
                        artifact_type=artifact_type,
                        mime_type=mime_type,
                        content_hash=content_hash,
                        size_bytes=size,
                        attestation_bytes=attestation_bytes,
                    )
                )

            _verify_runtime_directories(anchor.run_descriptor, directory_identities)
            _verify_run_directory_anchor(anchor)
            _verify_destination_directory_anchor(destination_anchor)
            return collected
        finally:
            destination_anchor.close()
    finally:
        anchor.close()


def _open_run_directory_anchor(
    exchange_root: Path,
    exchange_run_dir: Path,
) -> _RunDirectoryAnchor:
    try:
        relative_run_dir = exchange_run_dir.relative_to(exchange_root)
    except ValueError as error:
        raise RuntimeServiceError("runtime run directory escapes the exchange root") from error

    root_descriptor = _open_directory(exchange_root)
    descriptors = [root_descriptor]
    identities = [stat_identity(os.fstat(root_descriptor))]
    component_names: list[str] = []
    try:
        root_entry = os.stat(exchange_root, follow_symlinks=False)
        if _object_identity(root_entry) != _object_identity(os.fstat(root_descriptor)):
            raise RuntimeServiceError("runtime exchange root changed while opening")
        for component in relative_run_dir.parts:
            if component in {"", ".", ".."}:
                raise RuntimeServiceError("runtime run directory contains an unsafe component")
            parent_descriptor = descriptors[-1]
            entry = _stat_at(parent_descriptor, component)
            child_descriptor = _open_directory(component, directory_descriptor=parent_descriptor)
            opened = os.fstat(child_descriptor)
            if stat_identity(entry) != stat_identity(opened):
                os.close(child_descriptor)
                raise RuntimeServiceError("runtime run directory changed while opening")
            descriptors.append(child_descriptor)
            identities.append(stat_identity(opened))
            component_names.append(component)
        return _RunDirectoryAnchor(
            exchange_root=exchange_root,
            component_names=tuple(component_names),
            descriptors=descriptors,
            identities=tuple(identities),
        )
    except Exception:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def _verify_run_directory_anchor(anchor: _RunDirectoryAnchor) -> None:
    try:
        root_entry = os.stat(anchor.exchange_root, follow_symlinks=False)
    except OSError as error:
        raise RuntimeServiceError("runtime exchange root is no longer available") from error
    if _object_identity(root_entry) != _object_identity_from_snapshot(
        anchor.identities[0]
    ) or _object_identity(os.fstat(anchor.descriptors[0])) != _object_identity_from_snapshot(
        anchor.identities[0]
    ):
        raise RuntimeServiceError("runtime exchange root changed while collecting artifacts")

    for index, component in enumerate(anchor.component_names):
        parent_descriptor = anchor.descriptors[index]
        child_descriptor = anchor.descriptors[index + 1]
        expected = _object_identity_from_snapshot(anchor.identities[index + 1])
        entry = _stat_at(parent_descriptor, component)
        if (
            _object_identity(entry) != expected
            or _object_identity(os.fstat(child_descriptor)) != expected
        ):
            raise RuntimeServiceError("runtime run directory changed while collecting artifacts")


def _scan_runtime_tree(
    run_descriptor: int,
) -> tuple[list[RuntimeFile], dict[Path, _StatIdentity]]:
    files: list[RuntimeFile] = []
    directory_identities: dict[Path, _StatIdentity] = {}
    entry_count = 0
    directory_count = 1

    def scan(directory_descriptor: int, relative_directory: Path, depth: int) -> None:
        nonlocal directory_count, entry_count
        if depth > _MAX_RUNTIME_DIRECTORY_DEPTH:
            raise RuntimeServiceError("runtime output directory depth exceeds the limit")
        before = stat_identity(os.fstat(directory_descriptor))
        if not stat.S_ISDIR(before.mode):
            raise RuntimeServiceError("runtime output contains a non-directory path component")
        directory_identities[relative_directory] = before
        try:
            names: list[str] = []
            with os.scandir(directory_descriptor) as entries:
                for directory_entry in entries:
                    entry_count += 1
                    if entry_count > _MAX_RUNTIME_ENTRIES:
                        raise RuntimeServiceError("runtime output entry count exceeds the limit")
                    names.append(directory_entry.name)
            names.sort()
        except OSError as error:
            raise RuntimeServiceError(
                "runtime output directory could not be inspected safely"
            ) from error

        for name in names:
            entry = _stat_at(directory_descriptor, name)
            relative_path = relative_directory / name
            if stat.S_ISDIR(entry.st_mode):
                directory_count += 1
                if directory_count > _MAX_RUNTIME_DIRECTORIES:
                    raise RuntimeServiceError("runtime output directory count exceeds the limit")
                child_descriptor = _open_directory(name, directory_descriptor=directory_descriptor)
                try:
                    opened = os.fstat(child_descriptor)
                    if stat_identity(entry) != stat_identity(opened):
                        raise RuntimeServiceError(
                            "runtime output directory changed while being inspected"
                        )
                    scan(child_descriptor, relative_path, depth + 1)
                    if stat_identity(os.fstat(child_descriptor)) != stat_identity(
                        opened
                    ) or stat_identity(_stat_at(directory_descriptor, name)) != stat_identity(
                        opened
                    ):
                        raise RuntimeServiceError(
                            "runtime output directory changed while being inspected"
                        )
                finally:
                    os.close(child_descriptor)
                continue
            if not stat.S_ISREG(entry.st_mode):
                raise RuntimeServiceError("runtime output contains a non-regular file")
            if name == "input.csv":
                continue
            if relative_path.suffix.lower() in _ALLOWED_ARTIFACTS:
                files.append(
                    RuntimeFile(
                        relative_path=relative_path,
                        identity=stat_identity(entry),
                    )
                )

        if stat_identity(os.fstat(directory_descriptor)) != before:
            raise RuntimeServiceError("runtime output directory changed while being inspected")

    scan(run_descriptor, Path("."), 0)
    return files, directory_identities


def _open_destination_directory_anchor(
    project_root: Path,
    final_run_dir: Path,
    expected_final_run_identity: tuple[int, int],
) -> _DestinationDirectoryAnchor:
    try:
        relative_run_dir = final_run_dir.relative_to(project_root)
    except ValueError as error:
        raise RuntimeServiceError("artifact destination escapes the project directory") from error

    project_descriptor = _open_directory(project_root)
    descriptors = [project_descriptor]
    identities = [stat_identity(os.fstat(project_descriptor))]
    component_names: list[str] = []
    try:
        project_entry = os.stat(project_root, follow_symlinks=False)
        if _object_identity(project_entry) != _object_identity(os.fstat(project_descriptor)):
            raise RuntimeServiceError(
                "project directory changed while opening artifact destination"
            )
        for component in relative_run_dir.parts:
            if component in {"", ".", ".."}:
                raise RuntimeServiceError("artifact destination contains an unsafe component")
            child_descriptor, child_identity = _open_or_create_directory_at(
                descriptors[-1],
                component,
            )
            descriptors.append(child_descriptor)
            identities.append(child_identity)
            component_names.append(component)
        anchor = _DestinationDirectoryAnchor(
            project_root=project_root,
            component_names=tuple(component_names),
            descriptors=descriptors,
            identities=tuple(identities),
        )
        if _descriptor_object_identity(anchor.final_run_descriptor) != expected_final_run_identity:
            raise RuntimeServiceError("artifact destination directory identity changed")
        _verify_destination_directory_anchor(anchor)
        return anchor
    except Exception:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def _verify_destination_directory_anchor(anchor: _DestinationDirectoryAnchor) -> None:
    try:
        project_entry = os.stat(anchor.project_root, follow_symlinks=False)
    except OSError as error:
        raise RuntimeServiceError("project directory is no longer available") from error
    if _object_identity(project_entry) != _object_identity_from_snapshot(
        anchor.identities[0]
    ) or _object_identity(os.fstat(anchor.descriptors[0])) != _object_identity_from_snapshot(
        anchor.identities[0]
    ):
        raise RuntimeServiceError("project directory changed while collecting artifacts")

    for index, component in enumerate(anchor.component_names):
        expected = _object_identity_from_snapshot(anchor.identities[index + 1])
        entry = _stat_at(anchor.descriptors[index], component)
        opened = os.fstat(anchor.descriptors[index + 1])
        if _object_identity(entry) != expected or _object_identity(opened) != expected:
            raise RuntimeServiceError("artifact destination changed while collecting artifacts")


def _open_destination_parent_anchor(
    destination_anchor: _DestinationDirectoryAnchor,
    relative_parent: Path,
) -> _DestinationParentAnchor:
    _verify_destination_directory_anchor(destination_anchor)
    descriptors = [os.dup(destination_anchor.final_run_descriptor)]
    identities = [stat_identity(os.fstat(descriptors[0]))]
    component_names: list[str] = []
    try:
        for component in relative_parent.parts:
            if component in {"", "."}:
                continue
            if component == "..":
                raise RuntimeServiceError("artifact destination contains an unsafe component")
            child_descriptor, child_identity = _open_or_create_directory_at(
                descriptors[-1],
                component,
            )
            descriptors.append(child_descriptor)
            identities.append(child_identity)
            component_names.append(component)
        parent_anchor = _DestinationParentAnchor(
            component_names=tuple(component_names),
            descriptors=descriptors,
            identities=tuple(identities),
        )
        verify_destination_parent_anchor(destination_anchor, parent_anchor)
        return parent_anchor
    except Exception:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def verify_destination_parent_anchor(
    destination_anchor: _DestinationDirectoryAnchor,
    parent_anchor: _DestinationParentAnchor,
) -> None:
    _verify_destination_directory_anchor(destination_anchor)
    expected_root = _object_identity_from_snapshot(parent_anchor.identities[0])
    if (
        _object_identity(os.fstat(destination_anchor.final_run_descriptor)) != expected_root
        or _object_identity(os.fstat(parent_anchor.descriptors[0])) != expected_root
    ):
        raise RuntimeServiceError("artifact destination changed while copying")
    for index, component in enumerate(parent_anchor.component_names):
        expected = _object_identity_from_snapshot(parent_anchor.identities[index + 1])
        entry = _stat_at(parent_anchor.descriptors[index], component)
        opened = os.fstat(parent_anchor.descriptors[index + 1])
        if _object_identity(entry) != expected or _object_identity(opened) != expected:
            raise RuntimeServiceError("artifact destination changed while copying")


def _open_or_create_directory_at(
    parent_descriptor: int,
    component: str,
) -> tuple[int, _StatIdentity]:
    entry = _stat_at_optional(parent_descriptor, component)
    if entry is None:
        try:
            os.mkdir(component, mode=0o700, dir_fd=parent_descriptor)
        except FileExistsError:
            pass
        except OSError as error:
            raise RuntimeServiceError(
                "artifact destination directory could not be created safely"
            ) from error
        entry = _stat_at(parent_descriptor, component)
    if not stat.S_ISDIR(entry.st_mode):
        raise RuntimeServiceError("artifact destination contains a non-directory component")
    child_descriptor = _open_directory(component, directory_descriptor=parent_descriptor)
    opened = os.fstat(child_descriptor)
    if stat_identity(entry) != stat_identity(opened):
        os.close(child_descriptor)
        raise RuntimeServiceError("artifact destination directory changed while opening")
    return child_descriptor, stat_identity(opened)


def _copy_runtime_file(
    *,
    run_descriptor: int,
    runtime_file: RuntimeFile,
    directory_identities: dict[Path, _StatIdentity],
    destination_anchor: _DestinationDirectoryAnchor,
    expected_content_hash: str,
    expected_size: int,
    expected_environment_hash: str | None,
    capture_attestation: bool,
) -> tuple[str, int, bytes | None]:
    if capture_attestation and expected_size > _MAX_POLICY_ATTESTATION_BYTES:
        raise RuntimeServiceError(
            f"runtime policy attestation is too large: {runtime_file.relative_path.as_posix()}"
        )
    source_parent_descriptor, source_descriptor = open_runtime_file(
        run_descriptor,
        runtime_file,
        directory_identities,
    )
    try:
        destination_parent = _open_destination_parent_anchor(
            destination_anchor,
            runtime_file.relative_path.parent,
        )
        destination_identity: _StatIdentity | None = None
        destination_descriptor: int | None = None
        try:
            (
                content_hash,
                size,
                destination_identity,
                destination_descriptor,
                attestation_bytes,
            ) = copy_and_hash_open_regular_file(
                source_descriptor=source_descriptor,
                source_parent_descriptor=source_parent_descriptor,
                source_name=runtime_file.relative_path.name,
                source_label=runtime_file.relative_path.as_posix(),
                expected_identity=runtime_file.identity,
                destination_parent_descriptor=destination_parent.parent_descriptor,
                destination_name=runtime_file.relative_path.name,
                capture_content=capture_attestation,
            )
            verify_destination_parent_anchor(destination_anchor, destination_parent)
            _assert_destination_file_identity(
                destination_parent.parent_descriptor,
                runtime_file.relative_path.name,
                destination_descriptor,
                destination_identity,
            )
            if (
                content_hash != expected_content_hash
                or size != expected_size
                or (
                    expected_environment_hash is not None
                    and content_hash != expected_environment_hash
                )
            ):
                raise RuntimeServiceError(
                    "runtime artifact integrity mismatch for "
                    f"{runtime_file.relative_path.as_posix()}"
                )
            return content_hash, size, attestation_bytes
        except Exception:
            if destination_identity is not None:
                _unlink_destination_file_if_same_inode(
                    destination_parent.parent_descriptor,
                    runtime_file.relative_path.name,
                    destination_identity,
                )
            raise
        finally:
            if destination_descriptor is not None:
                os.close(destination_descriptor)
            destination_parent.close()
    finally:
        os.close(source_descriptor)
        os.close(source_parent_descriptor)


def open_runtime_file(
    run_descriptor: int,
    runtime_file: RuntimeFile,
    directory_identities: dict[Path, _StatIdentity],
) -> tuple[int, int]:
    current_descriptor = os.dup(run_descriptor)
    relative_directory = Path(".")
    try:
        _assert_descriptor_identity(
            current_descriptor,
            directory_identities[relative_directory],
            "runtime output directory changed before artifact copying",
        )
        for component in runtime_file.relative_path.parent.parts:
            if component in {"", "."}:
                continue
            relative_directory /= component
            expected_directory = directory_identities.get(relative_directory)
            if expected_directory is None:
                raise RuntimeServiceError("runtime artifact has an uninspected parent directory")
            entry = _stat_at(current_descriptor, component)
            child_descriptor = _open_directory(
                component,
                directory_descriptor=current_descriptor,
            )
            try:
                opened = os.fstat(child_descriptor)
                if (
                    stat_identity(entry) != expected_directory
                    or stat_identity(opened) != expected_directory
                ):
                    raise RuntimeServiceError(
                        "runtime artifact parent directory changed before copying"
                    )
            except Exception:
                os.close(child_descriptor)
                raise
            os.close(current_descriptor)
            current_descriptor = child_descriptor

        entry = _stat_at(current_descriptor, runtime_file.relative_path.name)
        if stat_identity(entry) != runtime_file.identity:
            raise RuntimeServiceError("runtime artifact changed before copying")
        source_descriptor = _open_regular_file_at(
            current_descriptor,
            runtime_file.relative_path.name,
        )
        opened = os.fstat(source_descriptor)
        if stat_identity(opened) != runtime_file.identity:
            os.close(source_descriptor)
            raise RuntimeServiceError("runtime artifact changed before copying")
        return current_descriptor, source_descriptor
    except Exception:
        os.close(current_descriptor)
        raise


def copy_and_hash_open_regular_file(
    *,
    source_descriptor: int,
    source_parent_descriptor: int,
    source_name: str,
    source_label: str,
    expected_identity: _StatIdentity,
    destination_parent_descriptor: int,
    destination_name: str,
    capture_content: bool,
) -> tuple[str, int, _StatIdentity, int, bytes | None]:
    destination_descriptor: int | None = None
    destination_identity: _StatIdentity | None = None
    destination_descriptor_transferred = False
    try:
        before = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or stat_identity(before) != expected_identity
        ):
            raise RuntimeServiceError(
                f"runtime artifact is not an unchanged private regular file: {source_label}"
            )
        destination_descriptor = open_destination_file_at(
            destination_parent_descriptor,
            destination_name,
        )
        destination_identity = stat_identity(os.fstat(destination_descriptor))
        digest = hashlib.sha256()
        captured = bytearray() if capture_content else None
        copied = 0
        while chunk := os.read(source_descriptor, 1024 * 1024):
            digest.update(chunk)
            if captured is not None:
                captured.extend(chunk)
            view = memoryview(chunk)
            written = 0
            while written < len(view):
                written += os.write(destination_descriptor, view[written:])
            copied += len(chunk)
        # Docker Desktop bind mounts can report the requested 0400 mode through
        # the directory entry while a still-writable descriptor reports 0600.
        # Reapply the final mode through the anchored descriptor so both views
        # converge before the complete identity comparison below.
        os.fchmod(destination_descriptor, 0o400)
        os.fsync(destination_descriptor)
        after = os.fstat(source_descriptor)
        if stat_identity(before) != stat_identity(after):
            raise RuntimeServiceError(f"runtime artifact changed while copying: {source_label}")
        entry_after = _stat_at(source_parent_descriptor, source_name)
        if stat_identity(entry_after) != expected_identity:
            raise RuntimeServiceError(f"runtime artifact changed while copying: {source_label}")
        if copied != after.st_size:
            raise RuntimeServiceError(f"runtime artifact copy was incomplete: {source_label}")
        destination_after = os.fstat(destination_descriptor)
        destination_entry = _stat_at(destination_parent_descriptor, destination_name)
        if (
            not stat.S_ISREG(destination_after.st_mode)
            or destination_after.st_nlink != 1
            or stat_identity(destination_entry) != stat_identity(destination_after)
        ):
            raise RuntimeServiceError(
                f"runtime artifact destination changed while copying: {source_label}"
            )
        destination_identity = stat_identity(destination_after)
        destination_descriptor_transferred = True
        return (
            digest.hexdigest(),
            copied,
            destination_identity,
            destination_descriptor,
            bytes(captured) if captured is not None else None,
        )
    except Exception:
        if destination_identity is not None:
            _unlink_destination_file_if_same_inode(
                destination_parent_descriptor,
                destination_name,
                destination_identity,
            )
        raise
    finally:
        if destination_descriptor is not None and not destination_descriptor_transferred:
            os.close(destination_descriptor)


def _verify_runtime_directories(
    run_descriptor: int,
    directory_identities: dict[Path, _StatIdentity],
) -> None:
    for relative_directory in sorted(
        directory_identities,
        key=lambda path: (len(path.parts), path.as_posix()),
    ):
        descriptor = os.dup(run_descriptor)
        current_relative = Path(".")
        try:
            _assert_descriptor_identity(
                descriptor,
                directory_identities[current_relative],
                "runtime output directory changed while copying artifacts",
            )
            for component in relative_directory.parts:
                if component in {"", "."}:
                    continue
                current_relative /= component
                expected = directory_identities[current_relative]
                entry = _stat_at(descriptor, component)
                child_descriptor = _open_directory(component, directory_descriptor=descriptor)
                opened = os.fstat(child_descriptor)
                os.close(descriptor)
                descriptor = child_descriptor
                if stat_identity(entry) != expected or stat_identity(opened) != expected:
                    raise RuntimeServiceError(
                        "runtime output directory changed while copying artifacts"
                    )
        finally:
            os.close(descriptor)


def _open_directory(path: str | Path, *, directory_descriptor: int | None = None) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise RuntimeServiceError("safe runtime artifact traversal is unavailable")
    flags = os.O_RDONLY | nofollow | directory | getattr(os, "O_CLOEXEC", 0)
    try:
        return os.open(path, flags, dir_fd=directory_descriptor)
    except OSError as error:
        raise RuntimeServiceError("runtime output directory could not be opened safely") from error


def _open_regular_file_at(directory_descriptor: int, name: str) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise RuntimeServiceError("safe runtime artifact traversal is unavailable")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError as error:
        raise RuntimeServiceError("runtime artifact could not be opened safely") from error
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
        os.close(descriptor)
        raise RuntimeServiceError("runtime artifact is not a private regular file")
    return descriptor


def open_destination_file_at(directory_descriptor: int, name: str) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise RuntimeServiceError("safe runtime artifact traversal is unavailable")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow | getattr(os, "O_CLOEXEC", 0)
    try:
        return os.open(name, flags, 0o400, dir_fd=directory_descriptor)
    except OSError as error:
        raise RuntimeServiceError(
            "artifact destination file could not be created safely"
        ) from error


def _assert_destination_file_identity(
    directory_descriptor: int,
    name: str,
    file_descriptor: int,
    expected_identity: _StatIdentity,
) -> None:
    entry = _stat_at(directory_descriptor, name)
    opened = os.fstat(file_descriptor)
    if stat_identity(entry) != expected_identity or stat_identity(opened) != expected_identity:
        raise RuntimeServiceError("artifact destination file changed while copying")


def _unlink_destination_file_if_same_inode(
    directory_descriptor: int,
    name: str,
    expected_identity: _StatIdentity,
) -> None:
    try:
        entry = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        return
    if not stat.S_ISREG(entry.st_mode) or (entry.st_dev, entry.st_ino) != (
        expected_identity.device,
        expected_identity.inode,
    ):
        return
    try:
        os.unlink(name, dir_fd=directory_descriptor)
    except FileNotFoundError:
        return
    except OSError:
        return


def _stat_at(directory_descriptor: int, name: str) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except OSError as error:
        raise RuntimeServiceError("runtime output path could not be inspected safely") from error


def _stat_at_optional(directory_descriptor: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RuntimeServiceError(
            "artifact destination path could not be inspected safely"
        ) from error


def stat_identity(value: os.stat_result) -> _StatIdentity:
    return _StatIdentity(
        device=value.st_dev,
        inode=value.st_ino,
        size=value.st_size,
        modified_ns=value.st_mtime_ns,
        changed_ns=value.st_ctime_ns,
        mode=value.st_mode,
        link_count=value.st_nlink,
    )


def _object_identity(value: os.stat_result) -> tuple[int, int, int]:
    return value.st_dev, value.st_ino, value.st_mode


def _object_identity_from_snapshot(value: _StatIdentity) -> tuple[int, int, int]:
    return value.device, value.inode, value.mode


def _descriptor_object_identity(descriptor: int) -> tuple[int, int]:
    value = os.fstat(descriptor)
    return value.st_dev, value.st_ino


def _assert_descriptor_identity(
    descriptor: int,
    expected: _StatIdentity,
    message: str,
) -> None:
    if stat_identity(os.fstat(descriptor)) != expected:
        raise RuntimeServiceError(message)


def read_text_file(path: Path, *, max_bytes: int = 2 * 1024 * 1024) -> str:
    with path.open("rb") as handle:
        content = handle.read(max_bytes + 1)
    if len(content) > max_bytes:
        return content[:max_bytes].decode("utf-8", errors="replace") + "\n[truncated]"
    return content.decode("utf-8", errors="replace")


def _parse_runtime_artifact(value: Any) -> RuntimeArtifactInfo:
    if not isinstance(value, dict):
        raise TypeError("artifact is not an object")
    artifact = cast(dict[str, Any], value)
    path = artifact.get("path")
    mime_type = artifact.get("mimeType")
    content_hash = artifact.get("contentHash")
    size_bytes = artifact.get("sizeBytes")
    artifact_type = artifact.get("artifactType")
    if (
        not isinstance(path, str)
        or not isinstance(mime_type, str)
        or not isinstance(content_hash, str)
        or not isinstance(artifact_type, str)
    ):
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

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and _unsafe_path_literal(node.value):
            self._reject(
                node,
                "absolute and parent-relative path literals are not allowed",
            )

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if isinstance(function, ast.Name):
            if function.id in {"compile", "eval", "exec"}:
                self._reject(node, f"calling {function.id} is not allowed")
            if function.id in self._forbidden_os_call_aliases:
                self._reject(node, f"calling {function.id} is not allowed")
            if function.id == "__import__":
                self._check_dynamic_import(node)
            if function.id in self._import_module_aliases:
                self._check_dynamic_import(node)

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
            ):
                self._check_dynamic_import(node)
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

    def _check_dynamic_import(self, node: ast.Call) -> None:
        module_root = _constant_module_root(node)
        if module_root is None:
            self._reject(node, "computed dynamic imports are not allowed")
        if module_root in (_FORBIDDEN_MODULES | {"os"}):
            self._reject(node, "dynamic import of shell-capable modules is not allowed")

    @staticmethod
    def _reject(node: ast.AST, message: str) -> None:
        line = getattr(node, "lineno", None)
        location = f" on line {line}" if line is not None else ""
        raise ValueError(f"Python code policy rejected {message}{location}")


def _is_forbidden_os_method(name: str) -> bool:
    return name in {"kill", "killpg", "popen", "system"} or name.startswith(
        ("spawn", "posix_spawn")
    )


def _unsafe_path_literal(value: str) -> bool:
    path = PurePosixPath(value)
    return path.is_absolute() or ".." in path.parts


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
