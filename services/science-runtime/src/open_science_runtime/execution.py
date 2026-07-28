from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, Literal, Protocol, cast

import nbformat
from fastapi import HTTPException, status
from nbclient import NotebookClient
from nbformat import NotebookNode

from .code_policy import CodePolicyError, validate_python_code
from .config import KERNEL_NAME, configured_data_root
from .schemas import ArtifactOut, ArtifactType, ExecuteIn, ExecuteOut

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_RESERVED_ARTIFACTS: dict[str, tuple[str, ArtifactType]] = {
    "input.ipynb": ("application/x-ipynb+json", "notebook-input"),
    "executed.ipynb": ("application/x-ipynb+json", "notebook-executed"),
    "environment.json": ("application/json", "environment"),
    "stdout.txt": ("text/plain", "stdout"),
    "stderr.txt": ("text/plain", "stderr"),
    "execution.log": ("text/plain", "log"),
}
_GENERATED_ARTIFACTS: dict[str, tuple[str, ArtifactType]] = {
    ".csv": ("text/csv", "table"),
    ".json": ("application/json", "json"),
    ".png": ("image/png", "image"),
}
_OUTPUT_PREVIEW_JSON_BYTES = 1024 * 1024


class _NotebookV4(Protocol):
    def new_notebook(self, *, cells: list[NotebookNode]) -> NotebookNode: ...

    def new_markdown_cell(self, source: str) -> NotebookNode: ...

    def new_code_cell(
        self,
        source: str,
        *,
        metadata: dict[str, list[str]],
    ) -> NotebookNode: ...


class _NotebookFormat(Protocol):
    NO_CONVERT: object
    v4: _NotebookV4

    def writes(self, notebook: NotebookNode, *, version: object) -> str: ...


_typed_nbformat = cast(_NotebookFormat, nbformat)


class _ReservedFile:
    def __init__(self, path: Path, identity: tuple[int, int]) -> None:
        self.path = path
        self.identity = identity


def environment_manifest() -> dict[str, Any]:
    packages: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            packages[name.lower()] = distribution.version
    return {
        "schemaVersion": 1,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "machine": platform.machine(),
        "kernel": KERNEL_NAME,
        "packages": dict(sorted(packages.items())),
    }


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def execute_notebook(payload: ExecuteIn) -> ExecuteOut:
    try:
        validate_python_code(
            payload.code,
            policy_profile_id=payload.policy_profile_id,
            policy_template=payload.policy_template,
            approved_code_sha256=payload.approved_code_sha256,
        )
    except CodePolicyError as error:
        raise HTTPException(
            status_code=422,
            detail=f"code rejected by runtime policy: {error}",
        ) from error

    data_root_raw, data_root = _validated_data_root()
    run_dir = _validated_run_dir(payload.run_dir, data_root_raw, data_root)
    dataset_path = _validated_dataset(payload.dataset_path, data_root_raw, data_root)
    _validate_initial_run_dir(run_dir, dataset_path)

    manifest = {
        **environment_manifest(),
        "executionPolicy": _policy_attestation(payload),
    }
    manifest_bytes = canonical_json_bytes(manifest)
    environment_hash = sha256_bytes(manifest_bytes)
    notebook = _build_notebook(payload, run_dir, dataset_path, data_root, environment_hash)
    input_notebook_bytes = _typed_nbformat.writes(
        notebook,
        version=_typed_nbformat.NO_CONVERT,
    ).encode("utf-8")

    reserved = _reserve_runtime_files(run_dir)
    _rewrite_reserved(reserved["input.ipynb"], input_notebook_bytes)
    _rewrite_reserved(reserved["environment.json"], manifest_bytes)

    started_at = datetime.now(UTC)
    started_clock = monotonic()
    execution_error: Exception | None = None
    execution_status: Literal["completed", "failed"] = "completed"

    try:
        client = NotebookClient(
            notebook,
            allow_errors=False,
            kernel_name=KERNEL_NAME,
            record_timing=True,
            timeout=payload.timeout_seconds,
        )
        client.execute(cwd=str(run_dir))
    except Exception as error:  # Notebook failures are part of the run result.
        execution_error = error
        execution_status = "failed"

    finished_at = datetime.now(UTC)
    duration_seconds = monotonic() - started_clock
    stdout, stderr = _extract_outputs(notebook)
    if execution_error is not None:
        error_text = _clean_text(f"{type(execution_error).__name__}: {execution_error}")
        if error_text and error_text not in stderr:
            stderr = _join_sections(stderr, error_text)

    executed_notebook_bytes = _typed_nbformat.writes(
        notebook,
        version=_typed_nbformat.NO_CONVERT,
    ).encode("utf-8")
    _rewrite_reserved(reserved["input.ipynb"], input_notebook_bytes)
    _rewrite_reserved(reserved["executed.ipynb"], executed_notebook_bytes)
    _rewrite_reserved(reserved["stdout.txt"], stdout.encode("utf-8"))
    _rewrite_reserved(reserved["stderr.txt"], stderr.encode("utf-8"))

    generated_paths, artifact_warnings = _generated_artifact_paths(
        run_dir,
        dataset_path,
        {item.path for item in reserved.values()},
    )
    if artifact_warnings:
        execution_status = "failed"
        warning_text = "\n".join(f"Artifact warning: {warning}" for warning in artifact_warnings)
        stderr = _join_sections(stderr, warning_text)
        _rewrite_reserved(reserved["stderr.txt"], stderr.encode("utf-8"))

    stdout_preview = _output_preview(
        stdout,
        stream="stdout",
        full_artifact="stdout.txt",
    )
    stderr_preview = _output_preview(
        stderr,
        stream="stderr",
        full_artifact="stderr.txt",
    )
    log_text = _build_log(
        payload=payload,
        data_root=data_root,
        run_dir=run_dir,
        dataset_path=dataset_path,
        environment_hash=environment_hash,
        run_status=execution_status,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        stdout=stdout_preview,
        stderr=stderr_preview,
    )
    _rewrite_reserved(reserved["execution.log"], log_text.encode("utf-8"))

    artifact_paths = [item.path for item in reserved.values()] + generated_paths
    artifacts = [
        _artifact_for_path(path, data_root, _artifact_metadata(path, reserved))
        for path in artifact_paths
    ]
    artifacts.sort(key=lambda artifact: artifact.path)
    return ExecuteOut(
        status=execution_status,
        environment_hash=environment_hash,
        stdout=stdout_preview,
        stderr=stderr_preview,
        log=log_text,
        artifacts=artifacts,
    )


def _validated_data_root() -> tuple[Path, Path]:
    raw = configured_data_root()
    try:
        resolved = raw.resolve(strict=True)
    except OSError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The runtime data root is unavailable",
        ) from error
    if not resolved.is_dir():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The runtime data root is not a directory",
        )
    return raw, resolved


def _validated_request_path(
    raw_value: str,
    field_name: str,
    data_root_raw: Path,
    data_root: Path,
) -> Path:
    candidate = Path(raw_value)
    if not candidate.is_absolute():
        raise HTTPException(status_code=422, detail=f"{field_name} must be an absolute path")
    try:
        lexical_relative = candidate.relative_to(data_root_raw)
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be beneath {data_root_raw}",
        ) from error
    if not lexical_relative.parts or ".." in lexical_relative.parts:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must name a child of {data_root_raw}",
        )

    current = data_root_raw
    for part in lexical_relative.parts:
        current = current / part
        if current.is_symlink():
            raise HTTPException(status_code=422, detail=f"{field_name} may not contain symlinks")

    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(data_root)
    except (OSError, ValueError) as error:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} is not a valid path beneath {data_root_raw}",
        ) from error
    if resolved == data_root:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must name a child of {data_root_raw}",
        )
    return resolved


def _validated_run_dir(raw_value: str, data_root_raw: Path, data_root: Path) -> Path:
    run_dir = _validated_request_path(raw_value, "runDir", data_root_raw, data_root)
    if not run_dir.is_dir():
        raise HTTPException(status_code=422, detail="runDir must be an existing directory")
    if not os.access(run_dir, os.W_OK | os.X_OK):
        raise HTTPException(status_code=403, detail="runDir is not writable by the runtime")
    return run_dir


def _validated_dataset(raw_value: str, data_root_raw: Path, data_root: Path) -> Path:
    dataset_path = _validated_request_path(raw_value, "datasetPath", data_root_raw, data_root)
    if dataset_path.suffix.lower() != ".csv":
        raise HTTPException(status_code=415, detail="datasetPath must be a CSV file")
    if not dataset_path.is_file() or not stat.S_ISREG(dataset_path.stat().st_mode):
        raise HTTPException(status_code=422, detail="datasetPath must be a regular file")
    if not os.access(dataset_path, os.R_OK):
        raise HTTPException(status_code=403, detail="datasetPath is not readable by the runtime")
    return dataset_path


def _validate_initial_run_dir(run_dir: Path, dataset_path: Path) -> None:
    allowed_file = dataset_path if _is_beneath(run_dir, dataset_path) else None
    for root, directory_names, file_names in os.walk(run_dir, followlinks=False):
        root_path = Path(root)
        for directory_name in directory_names:
            directory = root_path / directory_name
            if directory.is_symlink():
                raise HTTPException(status_code=409, detail="runDir contains a symlink")
        for file_name in file_names:
            file_path = root_path / file_name
            if file_path.is_symlink():
                raise HTTPException(status_code=409, detail="runDir contains a symlink")
            try:
                resolved = file_path.resolve(strict=True)
            except OSError as error:
                raise HTTPException(
                    status_code=409,
                    detail="runDir contains an invalid file",
                ) from error
            if allowed_file is None or resolved != allowed_file:
                raise HTTPException(
                    status_code=409,
                    detail="runDir must be empty except for datasetPath",
                )


def _build_notebook(
    payload: ExecuteIn,
    run_dir: Path,
    dataset_path: Path,
    data_root: Path,
    environment_hash: str,
) -> NotebookNode:
    dataset_literal = json.dumps(str(dataset_path), ensure_ascii=False)
    run_dir_literal = json.dumps(str(run_dir), ensure_ascii=False)
    setup_code = "\n".join(
        (
            "# Spark Agent runtime inputs (generated; do not edit)",
            "from pathlib import Path as _SparkAgentPath",
            f"DATASET_PATH = _SparkAgentPath({dataset_literal})",
            f"RUN_DIR = _SparkAgentPath({run_dir_literal})",
            "dataset_path = DATASET_PATH",
            "run_dir = RUN_DIR",
        )
    )
    relative_dataset = dataset_path.relative_to(data_root).as_posix()
    notebook = _typed_nbformat.v4.new_notebook(
        cells=[
            _typed_nbformat.v4.new_markdown_cell(f"# Analysis run\n\n{payload.objective}"),
            _typed_nbformat.v4.new_code_cell(
                setup_code,
                metadata={"tags": ["parameters"]},
            ),
            _typed_nbformat.v4.new_code_cell(
                payload.code,
                metadata={"tags": ["analysis"]},
            ),
        ]
    )
    notebook.metadata = {
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": KERNEL_NAME,
        },
        "language_info": {"name": "python", "version": platform.python_version()},
        "openScienceRuntime": {
            "schemaVersion": 1,
            "runId": payload.run_id,
            "datasetPath": relative_dataset,
            "payloadSha256": payload.payload_sha256,
            "environmentHash": environment_hash,
            "policyProfileId": payload.policy_profile_id,
            "policyTemplate": payload.policy_template,
            **_compiled_provenance_attestation(payload),
        },
    }
    return notebook


def _reserve_runtime_files(run_dir: Path) -> dict[str, _ReservedFile]:
    reserved: dict[str, _ReservedFile] = {}
    created_paths: list[Path] = []
    try:
        for name in _RESERVED_ARTIFACTS:
            path = run_dir / name
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags, 0o640)
            try:
                file_stat = os.fstat(descriptor)
                if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
                    raise RuntimeError("Reserved runtime path is not a private regular file")
                reserved[name] = _ReservedFile(path, (file_stat.st_dev, file_stat.st_ino))
                created_paths.append(path)
            finally:
                os.close(descriptor)
    except Exception:
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise
    return reserved


def _rewrite_reserved(reserved: _ReservedFile, content: bytes) -> None:
    flags = os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(reserved.path, flags)
    except OSError as error:
        raise RuntimeError(f"Reserved runtime file changed: {reserved.path.name}") from error
    try:
        file_stat = os.fstat(descriptor)
        identity = (file_stat.st_dev, file_stat.st_ino)
        if (
            identity != reserved.identity
            or not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_nlink != 1
        ):
            raise RuntimeError(f"Reserved runtime file changed: {reserved.path.name}")
        os.ftruncate(descriptor, 0)
        view = memoryview(content)
        written = 0
        while written < len(view):
            written += os.write(descriptor, view[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _extract_outputs(notebook: NotebookNode) -> tuple[str, str]:
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    cells = cast(list[dict[str, Any]], notebook.cells)
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        outputs = cast(list[dict[str, Any]], cell.get("outputs", []))
        for output in outputs:
            output_type = output.get("output_type")
            if output_type == "stream":
                text_value = _output_text(output.get("text", ""))
                if output.get("name") == "stderr":
                    stderr_parts.append(text_value)
                else:
                    stdout_parts.append(text_value)
            elif output_type in {"display_data", "execute_result"}:
                text_value = _output_text(output.get("data", {}).get("text/plain", ""))
                if text_value:
                    stdout_parts.append(text_value + ("" if text_value.endswith("\n") else "\n"))
            elif output_type == "error":
                traceback_lines = output.get("traceback", [])
                if traceback_lines:
                    stderr_parts.append("\n".join(_clean_text(line) for line in traceback_lines))
                else:
                    stderr_parts.append(
                        f"{output.get('ename', 'Error')}: {output.get('evalue', '')}"
                    )
    return _clean_text("".join(stdout_parts)), _clean_text("\n".join(stderr_parts))


def _output_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(str(item) for item in cast(list[object], value))
    return str(value) if value is not None else ""


def _clean_text(value: str) -> str:
    return _ANSI_ESCAPE.sub("", value).strip("\x00")


def _join_sections(first: str, second: str) -> str:
    if not first:
        return second
    if not second:
        return first
    return f"{first.rstrip()}\n{second.lstrip()}"


def _output_preview(
    value: str,
    *,
    stream: Literal["stdout", "stderr"],
    full_artifact: Literal["stdout.txt", "stderr.txt"],
) -> str:
    if _json_encoded_string_size(value) <= _OUTPUT_PREVIEW_JSON_BYTES:
        return value
    full_bytes = value.encode("utf-8")
    marker = (
        "\n\n[Spark Agent output preview truncated: "
        f"stream={stream}; originalBytes={len(full_bytes)}; "
        f"sha256={sha256_bytes(full_bytes)}; fullArtifact={full_artifact}]\n\n"
    )
    available_content_bytes = (
        _OUTPUT_PREVIEW_JSON_BYTES - 2 - _json_encoded_content_size(marker)
    )
    if available_content_bytes < 0:
        raise RuntimeError("Runtime output preview marker exceeds its size budget")
    head_budget = available_content_bytes // 2
    tail_budget = available_content_bytes - head_budget
    preview = (
        _json_bounded_prefix(value, head_budget)
        + marker
        + _json_bounded_suffix(value, tail_budget)
    )
    if _json_encoded_string_size(preview) > _OUTPUT_PREVIEW_JSON_BYTES:
        raise RuntimeError("Runtime output preview exceeds its size budget")
    return preview


def _json_encoded_string_size(value: str) -> int:
    return 2 + _json_encoded_content_size(value)


def _json_encoded_content_size(value: str) -> int:
    return sum(_json_encoded_character_size(character) for character in value)


def _json_encoded_character_size(character: str) -> int:
    code_point = ord(character)
    if character in {'"', "\\", "\b", "\f", "\n", "\r", "\t"}:
        return 2
    if code_point < 0x20:
        return 6
    if code_point <= 0x7F:
        return 1
    if code_point <= 0x7FF:
        return 2
    if 0xD800 <= code_point <= 0xDFFF:
        return len(character.encode("utf-8"))
    if code_point <= 0xFFFF:
        return 3
    return 4


def _json_bounded_prefix(value: str, budget: int) -> str:
    used = 0
    end = 0
    for index, character in enumerate(value):
        size = _json_encoded_character_size(character)
        if used + size > budget:
            break
        used += size
        end = index + 1
    return value[:end]


def _json_bounded_suffix(value: str, budget: int) -> str:
    used = 0
    start = len(value)
    for index in range(len(value) - 1, -1, -1):
        size = _json_encoded_character_size(value[index])
        if used + size > budget:
            break
        used += size
        start = index
    return value[start:]


def _generated_artifact_paths(
    run_dir: Path,
    dataset_path: Path,
    reserved_paths: set[Path],
) -> tuple[list[Path], list[str]]:
    generated: list[Path] = []
    warnings: list[str] = []
    for root, directory_names, file_names in os.walk(run_dir, followlinks=False):
        root_path = Path(root)
        for directory_name in list(directory_names):
            directory = root_path / directory_name
            if directory.is_symlink():
                directory_names.remove(directory_name)
                warnings.append(f"ignored symlink directory {directory.relative_to(run_dir)}")
        for file_name in file_names:
            path = root_path / file_name
            if path in reserved_paths:
                continue
            if path.is_symlink():
                warnings.append(f"ignored symlink file {path.relative_to(run_dir)}")
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                warnings.append(f"ignored unreadable file {path.relative_to(run_dir)}")
                continue
            if resolved == dataset_path:
                continue
            if not _is_beneath(run_dir, resolved):
                warnings.append(f"ignored escaped file {path.relative_to(run_dir)}")
                continue
            if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
                warnings.append(f"ignored non-regular file {path.relative_to(run_dir)}")
                continue
            if path.suffix.lower() in _GENERATED_ARTIFACTS:
                generated.append(path)
    generated.sort()
    return generated, warnings


def _build_log(
    *,
    payload: ExecuteIn,
    data_root: Path,
    run_dir: Path,
    dataset_path: Path,
    environment_hash: str,
    run_status: str,
    started_at: datetime,
    finished_at: datetime,
    duration_seconds: float,
    stdout: str,
    stderr: str,
) -> str:
    lines = [
        "Spark Agent notebook execution",
        f"runId: {payload.run_id}",
        f"status: {run_status}",
        f"payloadSha256: {payload.payload_sha256}",
        f"environmentHash: {environment_hash}",
        f"policyProfileId: {payload.policy_profile_id}",
        f"policyTemplate: {payload.policy_template or '-'}",
        *(
            [
                f"analysisSpecId: {payload.analysis_spec_id}",
                f"analysisSpecSha256: {payload.analysis_spec_sha256}",
                f"datasetProfileSha256: {payload.dataset_profile_sha256}",
                f"compilerVersion: {payload.compiler_version}",
                f"approvedCodeSha256: {payload.approved_code_sha256}",
            ]
            if payload.analysis_spec_id is not None
            else []
        ),
        f"runDir: {run_dir.relative_to(data_root).as_posix()}",
        f"datasetPath: {dataset_path.relative_to(data_root).as_posix()}",
        f"timeoutSeconds: {payload.timeout_seconds}",
        f"startedAt: {started_at.isoformat()}",
        f"finishedAt: {finished_at.isoformat()}",
        f"durationSeconds: {duration_seconds:.3f}",
        "",
        "[stdout]",
        stdout,
        "",
        "[stderr]",
        stderr,
        "",
    ]
    return "\n".join(lines)


def _compiled_provenance_attestation(payload: ExecuteIn) -> dict[str, str]:
    if payload.analysis_spec_id is None:
        return {}
    assert payload.analysis_spec_sha256 is not None
    assert payload.dataset_profile_sha256 is not None
    assert payload.compiler_version is not None
    assert payload.approved_code_sha256 is not None
    return {
        "analysisSpecId": payload.analysis_spec_id,
        "analysisSpecSha256": payload.analysis_spec_sha256,
        "datasetProfileSha256": payload.dataset_profile_sha256,
        "compilerVersion": payload.compiler_version,
        "approvedCodeSha256": payload.approved_code_sha256,
    }


def _policy_attestation(payload: ExecuteIn) -> dict[str, object]:
    return {
        "profileId": payload.policy_profile_id,
        "template": payload.policy_template,
        **_compiled_provenance_attestation(payload),
    }


def _artifact_metadata(
    path: Path,
    reserved: dict[str, _ReservedFile],
) -> tuple[str, ArtifactType]:
    if path.name in reserved and path.parent == reserved[path.name].path.parent:
        return _RESERVED_ARTIFACTS[path.name]
    return _GENERATED_ARTIFACTS[path.suffix.lower()]


def _artifact_for_path(
    path: Path,
    data_root: Path,
    metadata: tuple[str, ArtifactType],
) -> ArtifactOut:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"Artifact is not a regular file: {path.name}")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise RuntimeError(f"Artifact changed while hashing: {path.name}")
    finally:
        os.close(descriptor)
    mime_type, artifact_type = metadata
    relative_path = path.resolve(strict=True).relative_to(data_root).as_posix()
    return ArtifactOut(
        path=relative_path,
        mime_type=mime_type,
        content_hash=digest.hexdigest(),
        size_bytes=after.st_size,
        artifact_type=artifact_type,
    )


def _is_beneath(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return child != parent
