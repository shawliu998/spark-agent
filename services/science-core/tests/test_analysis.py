from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, cast

import httpx
import pytest

import open_science_core.analysis as analysis_module
from open_science_core._analysis_service.errors import execution_http_error
from open_science_core.analysis import (
    RuntimeArtifactInfo,
    RuntimeExecutionResult,
    RuntimeServiceError,
    collect_runtime_artifacts,
    execute_in_runtime,
    validate_python_code,
)
from open_science_core.config import settings
from open_science_core.fixed_analysis_policy import (
    COMPILED_ANALYSIS_POLICY_ID,
    COMPILED_ANALYSIS_TEMPLATE,
    FIXED_ANALYSIS_POLICY_ID,
    FixedAnalysisPolicyError,
    FixedAnalysisTemplate,
    fixed_analysis_source,
    fixed_analysis_template_for_repair_attempt,
)

_V1_TEMPLATE_SOURCE_SHA256: dict[FixedAnalysisTemplate, str] = {
    # These digests are the immutable dataset-analysis-fixed-v1 contract.
    # Source changes require a new policy ID instead of updating these values.
    "baseline": "8d3e24189110e8286f287b1873ace80e3ffce7c9b3958acfe1a9eb9d4573ba7e",
    "repair-1": "97ac3bc4dc8038857065a11e919eecdb058c457aa34d5aab272892a7b2d1e736",
    "repair-2": "e2a39274eb7fed95bfb4df7ad6676916306fc7707f2c57fc31ea0adb871d226e",
}


@dataclass(frozen=True, slots=True)
class ArtifactEnvironment:
    exchange: Path
    run_dir: Path
    project_dir: Path
    final_run_dir: Path
    contents: dict[Path, bytes]
    exchange_run_identity: tuple[int, int]
    final_run_identity: tuple[int, int]


class ChunkedResponseStream(httpx.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks
        self.chunks_yielded = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.chunks_yielded += 1
            yield chunk

    async def aclose(self) -> None:
        return None


@pytest.fixture
def artifact_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ArtifactEnvironment:
    exchange = tmp_path / "exchange"
    run_dir = exchange / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    project_dir = tmp_path / "project"
    (project_dir / "runs").mkdir(parents=True)
    final_run_dir = project_dir / "runs" / "run-1"
    final_run_dir.mkdir()
    contents = {
        Path("input.ipynb"): b'{"cells":[],"nbformat":4,"nbformat_minor":5}',
        Path("executed.ipynb"): b'{"cells":[],"nbformat":4,"nbformat_minor":5}',
        Path("environment.json"): b'{"python":"3.12"}',
        Path("stdout.txt"): b"done\n",
        Path("stderr.txt"): b"",
        Path("execution.log"): b"completed\n",
        Path("nested/figure.png"): b"safe-figure",
    }
    for relative_path, content in contents.items():
        path = run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    monkeypatch.setattr(
        analysis_module,
        "settings",
        replace(settings, runtime_exchange_dir=exchange),
    )
    return ArtifactEnvironment(
        exchange=exchange,
        run_dir=run_dir,
        project_dir=project_dir,
        final_run_dir=final_run_dir,
        contents=contents,
        exchange_run_identity=_directory_identity(run_dir),
        final_run_identity=_directory_identity(final_run_dir),
    )


def runtime_result(environment: ArtifactEnvironment) -> RuntimeExecutionResult:
    artifacts = [
        RuntimeArtifactInfo(
            path=(environment.run_dir / relative_path).relative_to(environment.exchange).as_posix(),
            mime_type="application/octet-stream",
            content_hash=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            artifact_type="runtime-output",
        )
        for relative_path, content in environment.contents.items()
    ]
    return RuntimeExecutionResult(
        status="completed",
        environment_hash=hashlib.sha256(environment.contents[Path("environment.json")]).hexdigest(),
        stdout="done\n",
        stderr="",
        log="completed\n",
        artifacts=artifacts,
    )


def _collect(environment: ArtifactEnvironment) -> None:
    collect_runtime_artifacts(
        runtime_result=runtime_result(environment),
        exchange_run_dir=environment.run_dir,
        final_run_dir=environment.final_run_dir,
        project_dir=environment.project_dir,
        expected_exchange_run_identity=environment.exchange_run_identity,
        expected_final_run_identity=environment.final_run_identity,
    )


def _directory_identity(path: Path) -> tuple[int, int]:
    metadata = path.stat()
    return metadata.st_dev, metadata.st_ino


@pytest.mark.parametrize(
    "code",
    [
        "from pathlib import Path\nPath('/etc/passwd').read_text()",
        "from pathlib import Path\nPath('../other-run/input.csv').read_text()",
        "module = 'sub' + 'process'\n__import__(module)",
        "exec(\"print('policy bypass')\")",
    ],
)
def test_code_policy_rejects_out_of_run_reads_and_dynamic_bypasses(
    code: str,
) -> None:
    with pytest.raises(ValueError, match="Python code policy rejected"):
        validate_python_code(code)


def test_code_policy_allows_only_injected_paths_in_normal_analysis() -> None:
    validate_python_code(
        "import pandas as pd\n"
        "data = pd.read_csv(DATASET_PATH)\n"
        "data.to_csv(RUN_DIR / 'summary.csv')"
    )


@pytest.mark.parametrize(
    ("template", "selected_column_index"),
    [("baseline", 0), ("baseline", 19), ("repair-1", 0), ("repair-2", 0)],
)
def test_fixed_analysis_policy_accepts_only_canonical_template_asts(
    template: FixedAnalysisTemplate,
    selected_column_index: int,
) -> None:
    code = fixed_analysis_source(
        template,
        selected_column_index=selected_column_index,
    )

    validate_python_code(
        f"# approved fixed template\n{code}\n",
        policy_profile_id=FIXED_ANALYSIS_POLICY_ID,
        policy_template=template,
    )


@pytest.mark.parametrize("template", ["baseline", "repair-1", "repair-2"])
def test_fixed_analysis_v1_template_source_is_frozen(
    template: FixedAnalysisTemplate,
) -> None:
    source = fixed_analysis_source(template)

    assert (
        hashlib.sha256(source.encode("utf-8")).hexdigest() == (_V1_TEMPLATE_SOURCE_SHA256[template])
    )


@pytest.mark.parametrize("invalid_value", [False, True, 1.0, "1"])
def test_fixed_analysis_policy_rejects_non_integer_contract_values(
    invalid_value: object,
) -> None:
    with pytest.raises(FixedAnalysisPolicyError):
        fixed_analysis_source(
            "baseline",
            selected_column_index=cast(Any, invalid_value),
        )
    with pytest.raises(FixedAnalysisPolicyError):
        fixed_analysis_template_for_repair_attempt(cast(Any, invalid_value))


@pytest.mark.parametrize(
    "mutation",
    [
        "\nrunner = eval\nrunner('1 + 1')",
        "\n__builtins__['__import__']('os').system('id')",
        "\npd.io.common.os.system('id')",
        "\nobject.__subclasses__()",
        "\nopen(chr(47) + 'etc/passwd').read()",
        "\nRUN_DIR.parent.joinpath('other').read_text()",
        "\nget_ipython().run_line_magic('sys' + 'tem', 'id')",
    ],
)
def test_fixed_analysis_policy_rejects_every_non_template_statement(
    mutation: str,
) -> None:
    code = fixed_analysis_source("baseline", selected_column_index=0) + mutation

    with pytest.raises(ValueError, match="dataset-analysis-fixed-v1/baseline"):
        validate_python_code(
            code,
            policy_profile_id=FIXED_ANALYSIS_POLICY_ID,
            policy_template="baseline",
        )


def test_fixed_analysis_policy_rejects_template_or_index_shape_drift() -> None:
    repair = fixed_analysis_source("repair-1")
    with pytest.raises(ValueError, match="selected column binding is missing"):
        validate_python_code(
            repair,
            policy_profile_id=FIXED_ANALYSIS_POLICY_ID,
            policy_template="baseline",
        )

    baseline = fixed_analysis_source("baseline").replace(
        "selected_column_index = 0",
        "selected_column_index = 1 + 0",
    )
    with pytest.raises(ValueError, match="selected column index is invalid"):
        validate_python_code(
            baseline,
            policy_profile_id=FIXED_ANALYSIS_POLICY_ID,
            policy_template="baseline",
        )


def test_compiled_analysis_policy_binds_exact_code_and_generic_safety() -> None:
    code = "import pandas as pd\nprint(pd.__version__)"
    approved_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()

    validate_python_code(
        code,
        policy_profile_id=COMPILED_ANALYSIS_POLICY_ID,
        policy_template=COMPILED_ANALYSIS_TEMPLATE,
        approved_code_sha256=approved_hash,
    )

    with pytest.raises(ValueError, match="does not match its approval"):
        validate_python_code(
            code + "\nprint('tampered')",
            policy_profile_id=COMPILED_ANALYSIS_POLICY_ID,
            policy_template=COMPILED_ANALYSIS_TEMPLATE,
            approved_code_sha256=approved_hash,
        )

    blocked = "import subprocess"
    with pytest.raises(ValueError, match="Python code policy rejected"):
        validate_python_code(
            blocked,
            policy_profile_id=COMPILED_ANALYSIS_POLICY_ID,
            policy_template=COMPILED_ANALYSIS_TEMPLATE,
            approved_code_sha256=hashlib.sha256(blocked.encode("utf-8")).hexdigest(),
        )


def test_core_and_runtime_fixed_policy_sources_are_byte_identical() -> None:
    services_dir = Path(__file__).resolve().parents[2]
    core_policy = services_dir / "science-core/src/open_science_core/fixed_analysis_policy.py"
    runtime_policy = (
        services_dir / "science-runtime/src/open_science_runtime/fixed_analysis_policy.py"
    )

    assert core_policy.read_bytes() == runtime_policy.read_bytes()


@pytest.mark.asyncio
async def test_runtime_response_stream_fails_closed_above_decoded_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = ChunkedResponseStream((b"x" * 80, b"y" * 80))
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"Content-Encoding": "identity"},
            stream=stream,
        )
    )
    monkeypatch.setattr(analysis_module, "_MAX_RUNTIME_RESPONSE_BYTES", 128)
    def mock_transport_factory(**_kwargs: object) -> httpx.MockTransport:
        return transport

    monkeypatch.setattr(
        analysis_module.httpx,
        "AsyncHTTPTransport",
        mock_transport_factory,
    )

    with pytest.raises(RuntimeServiceError, match="invalid response.*size limit") as caught:
        await execute_in_runtime(
            run_id="run-1",
            run_dir=tmp_path / "run-1",
            dataset_path=tmp_path / "run-1" / "input.csv",
            objective="Analyze",
            code="print(1)",
            payload_sha256="a" * 64,
            policy_profile_id="approved-python-container-v1",
            policy_template=None,
            timeout_seconds=5,
        )

    assert stream.chunks_yielded == 2
    assert execution_http_error(caught.value).code == "runtime-invalid-response"


def test_collect_runtime_artifacts_copies_nested_regular_files(
    artifact_environment: ArtifactEnvironment,
) -> None:
    collected = collect_runtime_artifacts(
        runtime_result=runtime_result(artifact_environment),
        exchange_run_dir=artifact_environment.run_dir,
        final_run_dir=artifact_environment.final_run_dir,
        project_dir=artifact_environment.project_dir,
        expected_exchange_run_identity=artifact_environment.exchange_run_identity,
        expected_final_run_identity=artifact_environment.final_run_identity,
    )

    assert {artifact.project_relative_path for artifact in collected} == {
        f"runs/run-1/{path.as_posix()}" for path in artifact_environment.contents
    }
    for relative_path, content in artifact_environment.contents.items():
        assert (artifact_environment.final_run_dir / relative_path).read_bytes() == content


def test_collect_runtime_artifacts_rejects_forged_environment_hash(
    artifact_environment: ArtifactEnvironment,
) -> None:
    result = runtime_result(artifact_environment)
    forged_result = replace(result, environment_hash="0" * 64)

    with pytest.raises(RuntimeServiceError, match="integrity mismatch"):
        collect_runtime_artifacts(
            runtime_result=forged_result,
            exchange_run_dir=artifact_environment.run_dir,
            final_run_dir=artifact_environment.final_run_dir,
            project_dir=artifact_environment.project_dir,
            expected_exchange_run_identity=artifact_environment.exchange_run_identity,
            expected_final_run_identity=artifact_environment.final_run_identity,
        )
    assert not (artifact_environment.final_run_dir / "environment.json").exists()


def test_collect_runtime_artifacts_rejects_nested_environment_duplicate(
    artifact_environment: ArtifactEnvironment,
) -> None:
    duplicate_path = artifact_environment.run_dir / "nested" / "environment.json"
    duplicate_path.write_bytes(b'{"python":"attacker"}')
    environment = replace(
        artifact_environment,
        contents={
            **artifact_environment.contents,
            Path("nested/environment.json"): duplicate_path.read_bytes(),
        },
    )

    with pytest.raises(RuntimeServiceError, match="reserved artifacts"):
        _collect(environment)
    assert not (artifact_environment.final_run_dir / "environment.json").exists()


def test_collect_runtime_artifacts_rejects_nested_reserved_log_duplicate(
    artifact_environment: ArtifactEnvironment,
) -> None:
    duplicate_path = artifact_environment.run_dir / "nested" / "execution.log"
    duplicate_path.write_bytes(b"forged audit log")
    environment = replace(
        artifact_environment,
        contents={
            **artifact_environment.contents,
            Path("nested/execution.log"): duplicate_path.read_bytes(),
        },
    )

    with pytest.raises(RuntimeServiceError, match="reserved artifacts"):
        _collect(environment)
    assert not (artifact_environment.final_run_dir / "execution.log").exists()


def test_collect_runtime_artifacts_bounds_ignored_entry_flood(
    artifact_environment: ArtifactEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(analysis_module, "_MAX_RUNTIME_ENTRIES", 10)
    for index in range(10):
        (artifact_environment.run_dir / f"ignored-{index}.bin").write_bytes(b"ignored")

    with pytest.raises(RuntimeServiceError, match="entry count"):
        _collect(artifact_environment)


def test_collect_runtime_artifacts_bounds_directory_depth(
    artifact_environment: ArtifactEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(analysis_module, "_MAX_RUNTIME_DIRECTORY_DEPTH", 2)
    (artifact_environment.run_dir / "deep" / "one" / "two").mkdir(parents=True)

    with pytest.raises(RuntimeServiceError, match="directory depth"):
        _collect(artifact_environment)


def test_collect_runtime_artifacts_rejects_claimed_small_actual_size(
    artifact_environment: ArtifactEnvironment,
) -> None:
    result = runtime_result(artifact_environment)
    forged_artifacts = [
        replace(artifact, size_bytes=1) if artifact.path.endswith("executed.ipynb") else artifact
        for artifact in result.artifacts
    ]

    with pytest.raises(RuntimeServiceError, match="size metadata mismatch"):
        collect_runtime_artifacts(
            runtime_result=replace(result, artifacts=forged_artifacts),
            exchange_run_dir=artifact_environment.run_dir,
            final_run_dir=artifact_environment.final_run_dir,
            project_dir=artifact_environment.project_dir,
            expected_exchange_run_identity=artifact_environment.exchange_run_identity,
            expected_final_run_identity=artifact_environment.final_run_identity,
        )
    assert list(artifact_environment.final_run_dir.iterdir()) == []


def test_collect_runtime_artifacts_checks_actual_total_size_before_copy(
    artifact_environment: ArtifactEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_size = sum(len(content) for content in artifact_environment.contents.values())
    monkeypatch.setattr(analysis_module, "_MAX_ARTIFACT_BYTES", actual_size - 1)

    with pytest.raises(RuntimeServiceError, match="size limit"):
        _collect(artifact_environment)
    assert list(artifact_environment.final_run_dir.iterdir()) == []


def test_collect_runtime_artifacts_rejects_source_real_directory_replacement(
    artifact_environment: ArtifactEnvironment,
) -> None:
    original = artifact_environment.exchange / "runs" / "run-original"
    artifact_environment.run_dir.rename(original)
    artifact_environment.run_dir.mkdir()
    for relative_path, content in artifact_environment.contents.items():
        path = artifact_environment.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    with pytest.raises(RuntimeServiceError, match="identity changed"):
        _collect(artifact_environment)
    assert list(artifact_environment.final_run_dir.iterdir()) == []


def test_collect_runtime_artifacts_rejects_destination_real_directory_replacement_before_open(
    artifact_environment: ArtifactEnvironment,
) -> None:
    original = artifact_environment.project_dir / "runs" / "run-original"
    artifact_environment.final_run_dir.rename(original)
    artifact_environment.final_run_dir.mkdir()

    with pytest.raises(RuntimeServiceError, match="destination directory identity changed"):
        _collect(artifact_environment)
    assert list(artifact_environment.final_run_dir.iterdir()) == []


def test_collect_runtime_artifacts_rejects_intermediate_directory_symlink_swap(
    artifact_environment: ArtifactEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = artifact_environment.exchange.parent / "outside"
    outside.mkdir()
    (outside / "figure.png").write_bytes(b"attacker-controlled")
    original_open = analysis_module.open_runtime_file
    swapped = False

    def open_after_swap(*args: object, **kwargs: object) -> tuple[int, int]:
        nonlocal swapped
        runtime_file = args[1]
        if (
            not swapped
            and isinstance(runtime_file, analysis_module.RuntimeFile)
            and runtime_file.relative_path == Path("nested/figure.png")
        ):
            nested = artifact_environment.run_dir / "nested"
            nested.rename(artifact_environment.run_dir / "nested-original")
            nested.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(analysis_module, "open_runtime_file", open_after_swap)

    with pytest.raises(RuntimeServiceError):
        _collect(artifact_environment)
    assert swapped
    assert not (artifact_environment.final_run_dir / "nested" / "figure.png").exists()


def test_collect_runtime_artifacts_rejects_destination_parent_symlink_escape(
    artifact_environment: ArtifactEnvironment,
) -> None:
    outside = artifact_environment.project_dir.parent / "outside-destination"
    outside.mkdir()
    (artifact_environment.final_run_dir / "nested").symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(RuntimeServiceError):
        _collect(artifact_environment)
    assert not (outside / "figure.png").exists()


def test_collect_runtime_artifacts_rejects_destination_real_directory_swap(
    artifact_environment: ArtifactEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = analysis_module.open_destination_file_at
    swapped = False

    def open_after_swap(directory_descriptor: int, name: str) -> int:
        nonlocal swapped
        if not swapped and name == "figure.png":
            nested = artifact_environment.final_run_dir / "nested"
            nested.rename(artifact_environment.final_run_dir / "nested-original")
            nested.mkdir()
            (nested / "attacker-marker.txt").write_text("keep", encoding="utf-8")
            swapped = True
        return original_open(directory_descriptor, name)

    monkeypatch.setattr(analysis_module, "open_destination_file_at", open_after_swap)

    with pytest.raises(RuntimeServiceError, match="destination changed while copying"):
        _collect(artifact_environment)
    assert swapped
    assert not (artifact_environment.final_run_dir / "nested" / "figure.png").exists()
    assert not (artifact_environment.final_run_dir / "nested-original" / "figure.png").exists()
    assert (artifact_environment.final_run_dir / "nested" / "attacker-marker.txt").read_text(
        encoding="utf-8"
    ) == "keep"


def test_collect_runtime_artifacts_normalizes_bind_mount_descriptor_mode(
    artifact_environment: ArtifactEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = analysis_module.open_destination_file_at
    original_fstat = analysis_module.os.fstat
    original_fchmod = analysis_module.os.fchmod
    destination_descriptors: set[int] = set()
    normalized_descriptors: set[int] = set()

    def open_destination(directory_descriptor: int, name: str) -> int:
        descriptor = original_open(directory_descriptor, name)
        destination_descriptors.add(descriptor)
        return descriptor

    def bind_mount_fstat(descriptor: int) -> os.stat_result:
        metadata = original_fstat(descriptor)
        if descriptor not in destination_descriptors or descriptor in normalized_descriptors:
            return metadata
        values = list(metadata)
        values[0] = metadata.st_mode | analysis_module.stat.S_IWUSR
        return os.stat_result(values)

    def normalize_mode(descriptor: int, mode: int) -> None:
        original_fchmod(descriptor, mode)
        if descriptor in destination_descriptors:
            normalized_descriptors.add(descriptor)

    monkeypatch.setattr(analysis_module, "open_destination_file_at", open_destination)
    monkeypatch.setattr(analysis_module.os, "fstat", bind_mount_fstat)
    monkeypatch.setattr(analysis_module.os, "fchmod", normalize_mode)

    _collect(artifact_environment)

    assert normalized_descriptors == destination_descriptors
    assert destination_descriptors
    for relative_path, content in artifact_environment.contents.items():
        destination = artifact_environment.final_run_dir / relative_path
        assert destination.read_bytes() == content
        assert destination.stat().st_mode & 0o777 == 0o400


def test_collect_runtime_artifacts_rejects_replacement_after_mode_normalization(
    artifact_environment: ArtifactEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_fchmod = analysis_module.os.fchmod
    replacement_content = b"attacker-replacement"
    replaced = False

    def normalize_then_replace(descriptor: int, mode: int) -> None:
        nonlocal replaced
        original_fchmod(descriptor, mode)
        destination = artifact_environment.final_run_dir / "executed.ipynb"
        if not replaced and destination.exists():
            destination.rename(artifact_environment.final_run_dir / "executed-original.ipynb")
            destination.write_bytes(replacement_content)
            replaced = True

    monkeypatch.setattr(analysis_module.os, "fchmod", normalize_then_replace)

    with pytest.raises(RuntimeServiceError, match="destination changed while copying"):
        _collect(artifact_environment)

    assert replaced
    assert (
        artifact_environment.final_run_dir / "executed.ipynb"
    ).read_bytes() == replacement_content


def test_mode_normalization_failure_closes_fd_and_preserves_replacement_inode(
    artifact_environment: ArtifactEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = analysis_module.open_destination_file_at
    opened_files: dict[int, str] = {}
    replacement_content = b"attacker-replacement"

    def record_open(directory_descriptor: int, name: str) -> int:
        descriptor = original_open(directory_descriptor, name)
        opened_files[descriptor] = name
        return descriptor

    def replace_then_fail(descriptor: int, _mode: int) -> None:
        name = opened_files[descriptor]
        destination = artifact_environment.final_run_dir / name
        destination.rename(artifact_environment.final_run_dir / f"original-{name}")
        destination.write_bytes(replacement_content)
        raise OSError("simulated fchmod failure")

    monkeypatch.setattr(analysis_module, "open_destination_file_at", record_open)
    monkeypatch.setattr(analysis_module.os, "fchmod", replace_then_fail)

    with pytest.raises(OSError, match="simulated fchmod failure"):
        _collect(artifact_environment)

    assert len(opened_files) == 1
    descriptor, name = next(iter(opened_files.items()))
    assert (artifact_environment.final_run_dir / name).read_bytes() == replacement_content
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_destination_cleanup_preserves_replacement_inode(
    artifact_environment: ArtifactEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_verify = analysis_module.verify_destination_parent_anchor
    replacement_content = b"attacker-replacement"
    replaced = False

    def verify_after_replacement(*args: object, **kwargs: object) -> None:
        nonlocal replaced
        destination = artifact_environment.final_run_dir / "executed.ipynb"
        if not replaced and destination.exists():
            destination.rename(artifact_environment.final_run_dir / "executed-original.ipynb")
            destination.write_bytes(replacement_content)
            replaced = True
        original_verify(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        analysis_module,
        "verify_destination_parent_anchor",
        verify_after_replacement,
    )

    with pytest.raises(RuntimeServiceError, match="destination file changed while copying"):
        _collect(artifact_environment)
    assert replaced
    assert (
        artifact_environment.final_run_dir / "executed.ipynb"
    ).read_bytes() == replacement_content


def test_collect_runtime_artifacts_rejects_file_stat_change_after_scan(
    artifact_environment: ArtifactEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = analysis_module.open_runtime_file
    changed = False

    def open_after_change(*args: object, **kwargs: object) -> tuple[int, int]:
        nonlocal changed
        runtime_file = args[1]
        if (
            not changed
            and isinstance(runtime_file, analysis_module.RuntimeFile)
            and runtime_file.relative_path == Path("stdout.txt")
        ):
            stdout_path = artifact_environment.run_dir / "stdout.txt"
            stdout_path.chmod(0o600)
            changed = True
        return original_open(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(analysis_module, "open_runtime_file", open_after_change)

    with pytest.raises(RuntimeServiceError, match="artifact changed before copying"):
        _collect(artifact_environment)
    assert changed


def test_collect_runtime_artifacts_rejects_inode_replacement_before_copy(
    artifact_environment: ArtifactEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = artifact_environment.run_dir / "stdout.txt"
    original_inode = target.stat().st_ino
    replacement = artifact_environment.exchange.parent / "replacement-stdout.txt"
    replacement.write_bytes(artifact_environment.contents[Path("stdout.txt")])
    replacement_inode = replacement.stat().st_ino
    assert replacement_inode != original_inode
    original_copy = analysis_module.copy_and_hash_open_regular_file
    replaced = False

    def copy_after_replacement(**kwargs: object) -> tuple[str, int]:
        nonlocal replaced
        if not replaced and kwargs["source_label"] == "stdout.txt":
            os.replace(replacement, target)
            replaced = True
        return original_copy(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        analysis_module,
        "copy_and_hash_open_regular_file",
        copy_after_replacement,
    )

    with pytest.raises(RuntimeServiceError, match="unchanged private regular file"):
        _collect(artifact_environment)
    assert replaced
    assert target.stat().st_ino == replacement_inode


def test_collect_runtime_artifacts_rejects_file_stat_change_during_copy(
    artifact_environment: ArtifactEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = artifact_environment.run_dir / "executed.ipynb"
    target_inode = target.stat().st_ino
    original_read: Callable[[int, int], bytes] = analysis_module.os.read
    changed = False

    def read_and_change_mode(descriptor: int, size: int) -> bytes:
        nonlocal changed
        chunk = original_read(descriptor, size)
        if chunk and not changed and analysis_module.os.fstat(descriptor).st_ino == target_inode:
            target.chmod(0o600)
            changed = True
        return chunk

    monkeypatch.setattr(analysis_module.os, "read", read_and_change_mode)

    with pytest.raises(RuntimeServiceError, match="artifact changed while copying"):
        _collect(artifact_environment)
    assert changed
    assert not (artifact_environment.final_run_dir / "executed.ipynb").exists()
