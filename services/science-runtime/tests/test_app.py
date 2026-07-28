# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, cast
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from open_science_runtime import app as runtime_app
from open_science_runtime import execution as runtime_execution
from open_science_runtime.config import DATA_ROOT_ENV
from open_science_runtime.fixed_analysis_policy import fixed_analysis_source
from open_science_runtime.schemas import ExecuteOut


class _RequestClient(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Response: ...


class TypedTestClient(TestClient):
    def get(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("POST", url, **kwargs)


def _request_payload() -> dict[str, object]:
    return {
        "runId": "run-01",
        "runDir": "/runtime-data/runs/run-01",
        "datasetPath": "/runtime-data/input.csv",
        "objective": "Analyze a local CSV",
        "code": "print('ok')",
        "timeoutSeconds": 30,
        "payloadSha256": "a" * 64,
        "policyProfileId": "approved-python-container-v1",
        "policyTemplate": None,
    }


def test_health_reports_kernel_and_data_root_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setenv(DATA_ROOT_ENV, str(data_root))

    with patch.object(runtime_app.KernelSpecManager, "get_kernel_spec", return_value=object()):
        with TypedTestClient(runtime_app.app) as client:
            response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": "0.2.0",
        "dataRoot": str(data_root),
        "kernel": "python3",
        "kernelAvailable": True,
        "maxTimeoutSeconds": 120,
    }


def test_execute_rejects_unknown_input_before_calling_runtime() -> None:
    payload = _request_payload()
    payload["unexpected"] = "not-allowed"

    with patch.object(runtime_app, "execute_notebook") as execute_notebook:
        with TypedTestClient(runtime_app.app) as client:
            response = client.post("/v1/execute", json=payload)

    assert response.status_code == 422
    execute_notebook.assert_not_called()


def test_execute_returns_conflict_while_an_execution_owns_the_slot() -> None:
    assert runtime_app._execution_slot.acquire(blocking=False)
    try:
        with TypedTestClient(runtime_app.app) as client:
            response = client.post("/v1/execute", json=_request_payload())
    finally:
        runtime_app._execution_slot.release()

    assert response.status_code == 409
    assert response.json() == {
        "detail": "The science runtime is already executing a notebook"
    }


def test_execute_releases_slot_when_runtime_raises() -> None:
    with patch.object(runtime_app, "execute_notebook", side_effect=RuntimeError("failed")):
        with TypedTestClient(runtime_app.app, raise_server_exceptions=False) as client:
            response = client.post("/v1/execute", json=_request_payload())

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert runtime_app._execution_slot.acquire(blocking=False)
    runtime_app._execution_slot.release()


def test_policy_rejection_is_a_safe_422_and_creates_no_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    run_dir = data_root / "runs" / "run-01"
    run_dir.mkdir(parents=True)
    dataset_path = data_root / "input.csv"
    dataset_path.write_text("value\n1\n", encoding="utf-8")
    monkeypatch.setenv(DATA_ROOT_ENV, str(data_root))
    payload = _request_payload()
    payload.update(
        {
            "runDir": str(run_dir),
            "datasetPath": str(dataset_path),
            "code": "import subprocess\nsubprocess.run(['id'])",
        }
    )

    with TypedTestClient(runtime_app.app) as client:
        response = client.post("/v1/execute", json=payload)

    assert response.status_code == 422
    assert response.json() == {
        "detail": (
            "code rejected by runtime policy: "
            "line 1: importing subprocess is not allowed"
        )
    }
    assert list(run_dir.iterdir()) == []


def test_fixed_policy_rejects_approved_code_shape_drift_before_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    run_dir = data_root / "runs" / "run-01"
    run_dir.mkdir(parents=True)
    dataset_path = data_root / "input.csv"
    dataset_path.write_text("value\n1\n", encoding="utf-8")
    monkeypatch.setenv(DATA_ROOT_ENV, str(data_root))
    payload = _request_payload()
    payload.update(
        {
            "runDir": str(run_dir),
            "datasetPath": str(dataset_path),
            "code": fixed_analysis_source("baseline") + "\nprint('extra')",
            "policyProfileId": "dataset-analysis-fixed-v1",
            "policyTemplate": "baseline",
        }
    )

    with TypedTestClient(runtime_app.app) as client:
        response = client.post("/v1/execute", json=payload)

    assert response.status_code == 422
    assert response.json() == {
        "detail": (
            "code rejected by runtime policy: code does not match the "
            "dataset-analysis-fixed-v1/baseline contract"
        )
    }
    assert list(run_dir.iterdir()) == []


def test_execute_serializes_response_with_camel_case_contract() -> None:
    result = ExecuteOut(
        status="completed",
        environment_hash="b" * 64,
        stdout="done\n",
        stderr="",
        log="safe log",
        artifacts=[],
    )
    with patch.object(runtime_app, "execute_notebook", return_value=result):
        with TypedTestClient(runtime_app.app) as client:
            response = client.post("/v1/execute", json=_request_payload())

    assert response.status_code == 200
    assert response.json() == {
        "status": "completed",
        "environmentHash": "b" * 64,
        "stdout": "done\n",
        "stderr": "",
        "log": "safe log",
        "artifacts": [],
    }


def test_execute_maximum_output_previews_fit_core_response_limit() -> None:
    full_output = '\x01"\n\\界' * 200_000
    stdout = runtime_execution._output_preview(
        full_output,
        stream="stdout",
        full_artifact="stdout.txt",
    )
    stderr = runtime_execution._output_preview(
        full_output,
        stream="stderr",
        full_artifact="stderr.txt",
    )
    result = ExecuteOut(
        status="completed",
        environment_hash="b" * 64,
        stdout=stdout,
        stderr=stderr,
        log=f"safe header\n\n[stdout]\n{stdout}\n\n[stderr]\n{stderr}\n",
        artifacts=[],
    )

    with patch.object(runtime_app, "execute_notebook", return_value=result):
        with TypedTestClient(runtime_app.app) as client:
            response = client.post("/v1/execute", json=_request_payload())

    assert response.status_code == 200
    assert len(response.content) < 16 * 1024 * 1024
