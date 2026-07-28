# pyright: reportPrivateUsage=false

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import nbformat
import pytest
from fastapi import HTTPException

from open_science_runtime import execution
from open_science_runtime.config import DATA_ROOT_ENV
from open_science_runtime.fixed_analysis_policy import fixed_analysis_source
from open_science_runtime.schemas import ExecuteIn


def _payload(run_dir: Path, dataset_path: Path) -> ExecuteIn:
    return ExecuteIn(
        run_id="run-01",
        run_dir=str(run_dir),
        dataset_path=str(dataset_path),
        objective="Analyze a local CSV",
        code="print('ok')",
        timeout_seconds=30,
        payload_sha256="a" * 64,
        policy_profile_id="approved-python-container-v1",
    )


def _data_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    data_root = tmp_path / "data"
    run_dir = data_root / "runs" / "run-01"
    dataset_path = data_root / "input.csv"
    run_dir.mkdir(parents=True)
    dataset_path.write_text("value\n1\n", encoding="utf-8")
    monkeypatch.setenv(DATA_ROOT_ENV, str(data_root))
    return data_root, run_dir, dataset_path


def test_rejects_lexical_parent_traversal_before_notebook_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root, run_dir, _dataset_path = _data_layout(tmp_path, monkeypatch)
    traversal_path = data_root / "runs" / ".." / "input.csv"

    with pytest.raises(HTTPException) as caught:
        execution.execute_notebook(_payload(run_dir, traversal_path))

    assert caught.value.status_code == 422
    assert caught.value.detail == f"datasetPath must name a child of {data_root}"
    assert list(run_dir.iterdir()) == []


def test_rejects_dataset_path_with_a_symlink_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root, run_dir, _dataset_path = _data_layout(tmp_path, monkeypatch)
    outside_dataset = tmp_path / "outside.csv"
    outside_dataset.write_text("secret\nvalue\n", encoding="utf-8")
    linked_dataset = data_root / "linked.csv"
    linked_dataset.symlink_to(outside_dataset)

    with pytest.raises(HTTPException) as caught:
        execution.execute_notebook(_payload(run_dir, linked_dataset))

    assert caught.value.status_code == 422
    assert caught.value.detail == "datasetPath may not contain symlinks"
    assert list(run_dir.iterdir()) == []


def test_rejects_symlinks_already_present_beneath_run_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _data_root, run_dir, dataset_path = _data_layout(tmp_path, monkeypatch)
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    (run_dir / "escape").symlink_to(outside_directory, target_is_directory=True)

    with pytest.raises(HTTPException) as caught:
        execution.execute_notebook(_payload(run_dir, dataset_path))

    assert caught.value.status_code == 409
    assert caught.value.detail == "runDir contains a symlink"


def test_existing_reserved_output_is_not_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _data_root, run_dir, dataset_path = _data_layout(tmp_path, monkeypatch)
    reserved_path = run_dir / "input.ipynb"
    reserved_path.write_text("user-owned", encoding="utf-8")

    with pytest.raises(HTTPException) as caught:
        execution.execute_notebook(_payload(run_dir, dataset_path))

    assert caught.value.status_code == 409
    assert caught.value.detail == "runDir must be empty except for datasetPath"
    assert reserved_path.read_text(encoding="utf-8") == "user-owned"


def test_reserved_file_collision_rolls_back_only_files_created_by_runtime(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    blocker = run_dir / "environment.json"
    blocker.write_text("user-owned", encoding="utf-8")

    with pytest.raises(FileExistsError):
        execution._reserve_runtime_files(run_dir)

    assert {path.name for path in run_dir.iterdir()} == {"environment.json"}
    assert blocker.read_text(encoding="utf-8") == "user-owned"


def test_reserved_runtime_artifacts_are_group_readable_and_complete(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    reserved = execution._reserve_runtime_files(run_dir)

    assert set(reserved) == set(execution._RESERVED_ARTIFACTS)
    assert {path.name for path in run_dir.iterdir()} == set(execution._RESERVED_ARTIFACTS)
    assert {
        name: reserved_file.path.stat().st_mode & 0o777
        for name, reserved_file in reserved.items()
    } == {name: 0o640 for name in execution._RESERVED_ARTIFACTS}


def test_reserved_file_rewrite_rejects_symlink_substitution(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("preserve-me", encoding="utf-8")
    reserved = execution._reserve_runtime_files(run_dir)["stdout.txt"]
    reserved.path.unlink()
    reserved.path.symlink_to(victim)

    with pytest.raises(RuntimeError, match="Reserved runtime file changed: stdout.txt"):
        execution._rewrite_reserved(reserved, b"attacker-controlled")

    assert victim.read_text(encoding="utf-8") == "preserve-me"


def test_fixed_policy_is_recorded_in_environment_notebook_and_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _data_root, run_dir, dataset_path = _data_layout(tmp_path, monkeypatch)
    payload = ExecuteIn(
        run_id="run-01",
        run_dir=str(run_dir),
        dataset_path=str(dataset_path),
        objective="Analyze a local CSV",
        code=fixed_analysis_source("baseline"),
        timeout_seconds=30,
        payload_sha256="a" * 64,
        policy_profile_id="dataset-analysis-fixed-v1",
        policy_template="baseline",
    )

    with patch.object(execution, "NotebookClient") as notebook_client:
        result = execution.execute_notebook(payload)

    notebook_client.return_value.execute.assert_called_once()
    assert result.status == "completed"
    manifest = json.loads((run_dir / "environment.json").read_text(encoding="utf-8"))
    assert manifest["executionPolicy"] == {
        "profileId": "dataset-analysis-fixed-v1",
        "template": "baseline",
    }
    notebook = json.loads((run_dir / "input.ipynb").read_text(encoding="utf-8"))
    assert notebook["metadata"]["openScienceRuntime"]["policyProfileId"] == (
        "dataset-analysis-fixed-v1"
    )
    assert notebook["metadata"]["openScienceRuntime"]["policyTemplate"] == "baseline"
    log = (run_dir / "execution.log").read_text(encoding="utf-8")
    assert "policyProfileId: dataset-analysis-fixed-v1" in log
    assert "policyTemplate: baseline" in log


def test_compiled_policy_provenance_is_recorded_in_every_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _data_root, run_dir, dataset_path = _data_layout(tmp_path, monkeypatch)
    code = "print('ok')"
    provenance = {
        "analysisSpecId": "spec-1",
        "analysisSpecSha256": "b" * 64,
        "datasetProfileSha256": "c" * 64,
        "compilerVersion": "analysis-spec-compiler-v1",
        "approvedCodeSha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
    }
    payload = ExecuteIn.model_validate(
        {
            "runId": "run-01",
            "runDir": str(run_dir),
            "datasetPath": str(dataset_path),
            "objective": "Analyze a local CSV",
            "code": code,
            "timeoutSeconds": 30,
            "payloadSha256": "a" * 64,
            "policyProfileId": "dataset-analysis-spec-v1",
            "policyTemplate": "analysis-spec-compiler-v1",
            **provenance,
        }
    )

    with patch.object(execution, "NotebookClient") as notebook_client:
        result = execution.execute_notebook(payload)

    notebook_client.return_value.execute.assert_called_once()
    assert result.status == "completed"
    manifest = json.loads((run_dir / "environment.json").read_text(encoding="utf-8"))
    assert manifest["executionPolicy"] == {
        "profileId": "dataset-analysis-spec-v1",
        "template": "analysis-spec-compiler-v1",
        **provenance,
    }
    notebook = json.loads((run_dir / "input.ipynb").read_text(encoding="utf-8"))
    assert notebook["metadata"]["openScienceRuntime"] == {
        "schemaVersion": 1,
        "runId": "run-01",
        "datasetPath": "input.csv",
        "payloadSha256": "a" * 64,
        "environmentHash": result.environment_hash,
        "policyProfileId": "dataset-analysis-spec-v1",
        "policyTemplate": "analysis-spec-compiler-v1",
        **provenance,
    }
    log = (run_dir / "execution.log").read_text(encoding="utf-8")
    for key, value in provenance.items():
        assert f"{key}: {value}" in log


def test_small_output_preview_is_unchanged() -> None:
    output = 'small output with 界, a newline\n, and a control byte \x01'

    assert (
        execution._output_preview(
            output,
            stream="stdout",
            full_artifact="stdout.txt",
        )
        == output
    )


def test_output_preview_budget_includes_json_quotes() -> None:
    at_limit = "a" * (execution._OUTPUT_PREVIEW_JSON_BYTES - 2)
    above_limit = at_limit + "a"

    assert (
        execution._output_preview(
            at_limit,
            stream="stdout",
            full_artifact="stdout.txt",
        )
        == at_limit
    )
    assert (
        execution._output_preview(
            above_limit,
            stream="stdout",
            full_artifact="stdout.txt",
        )
        != above_limit
    )


def test_large_output_uses_bounded_previews_and_preserves_full_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _data_root, run_dir, dataset_path = _data_layout(tmp_path, monkeypatch)
    stdout = "STDOUT-HEAD-界\n" + ('"\n\\\x01界' * 100_000) + "\nSTDOUT-TAIL-界"
    stderr = "STDERR-HEAD-界\n" + ("\x02err界\n" * 100_000) + "STDERR-RAW-TAIL"
    notebook_v4: Any = nbformat.v4

    class OutputNotebookClient:
        def __init__(self, notebook: Any, **_kwargs: object) -> None:
            self._notebook = notebook

        def execute(self, *, cwd: str) -> None:
            assert cwd == str(run_dir)
            self._notebook.cells[-1].outputs = [
                notebook_v4.new_output(
                    output_type="stream",
                    name="stdout",
                    text=stdout,
                ),
                notebook_v4.new_output(
                    output_type="stream",
                    name="stderr",
                    text=stderr,
                ),
            ]
            (run_dir / "ignored-link").symlink_to(dataset_path)
            raise RuntimeError("final execution failure")

    monkeypatch.setattr(execution, "NotebookClient", OutputNotebookClient)

    result = execution.execute_notebook(_payload(run_dir, dataset_path))

    assert result.status == "failed"
    full_stdout = (run_dir / "stdout.txt").read_bytes()
    full_stderr = (run_dir / "stderr.txt").read_bytes()
    assert full_stdout == stdout.encode("utf-8")
    assert full_stderr.startswith(stderr.encode("utf-8"))
    assert full_stderr.endswith(b"Artifact warning: ignored symlink file ignored-link")
    assert b"RuntimeError: final execution failure" in full_stderr

    for preview, full_output, stream, artifact_name in (
        (result.stdout, full_stdout, "stdout", "stdout.txt"),
        (result.stderr, full_stderr, "stderr", "stderr.txt"),
    ):
        encoded_preview = json.dumps(
            preview,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        assert len(encoded_preview) <= execution._OUTPUT_PREVIEW_JSON_BYTES
        assert len(encoded_preview) == execution._json_encoded_string_size(
            preview
        )
        assert f"stream={stream}" in preview
        assert f"originalBytes={len(full_output)}" in preview
        assert f"sha256={hashlib.sha256(full_output).hexdigest()}" in preview
        assert f"fullArtifact={artifact_name}" in preview
        assert preview.count("Spark Agent output preview truncated") == 1
        assert "\ufffd" not in preview
        preview.encode("utf-8", errors="strict")
        assert b"Spark Agent output preview truncated" not in full_output

    assert result.stdout.startswith("STDOUT-HEAD-界")
    assert result.stdout.endswith("STDOUT-TAIL-界")
    assert result.stderr.startswith("STDERR-HEAD-界")
    assert result.stderr.endswith("Artifact warning: ignored symlink file ignored-link")
    assert "RuntimeError: final execution failure" in result.stderr
    assert result.stdout != full_stdout.decode("utf-8")
    assert result.stderr != full_stderr.decode("utf-8")

    log = (run_dir / "execution.log").read_text(encoding="utf-8")
    assert result.log == log
    assert f"[stdout]\n{result.stdout}\n\n[stderr]\n{result.stderr}\n" in log

    artifacts = {artifact.artifact_type: artifact for artifact in result.artifacts}
    assert artifacts["stdout"].size_bytes == len(full_stdout)
    assert artifacts["stdout"].content_hash == hashlib.sha256(full_stdout).hexdigest()
    assert artifacts["stderr"].size_bytes == len(full_stderr)
    assert artifacts["stderr"].content_hash == hashlib.sha256(full_stderr).hexdigest()

    executed = json.loads((run_dir / "executed.ipynb").read_text(encoding="utf-8"))
    serialized_outputs = executed["cells"][-1]["outputs"]
    assert _serialized_output_text(serialized_outputs[0]["text"]) == stdout
    assert _serialized_output_text(serialized_outputs[1]["text"]) == stderr


def _serialized_output_text(value: str | list[str]) -> str:
    return value if isinstance(value, str) else "".join(value)
