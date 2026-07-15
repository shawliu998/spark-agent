from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path

from open_science_core.analysis import (
    RuntimeArtifactInfo,
    RuntimeExecutionResult,
)


def write_attested_runtime_result(
    run_dir: Path,
    exchange_root: Path,
    request: Mapping[str, object],
    *,
    status: str = "completed",
    stdout: str = "",
    stderr: str = "",
    generated_files: Mapping[str, bytes] | None = None,
    evidence_request: Mapping[str, object] | None = None,
    omitted_files: frozenset[str] = frozenset(),
    nested_files: frozenset[str] = frozenset(),
    mutate_files: Callable[[dict[str, bytes]], None] | None = None,
    started_at: str = "2026-07-15T00:00:00+00:00",
    finished_at: str = "2026-07-15T00:00:01+00:00",
    duration_seconds: str = "1.000",
) -> RuntimeExecutionResult:
    evidence = evidence_request or request
    run_id = _request_string(evidence, "run_id")
    objective = _request_string(evidence, "objective").strip()
    code = _request_string(evidence, "code")
    payload_sha256 = _request_string(evidence, "payload_sha256")
    policy_profile_id = _request_string(evidence, "policy_profile_id")
    policy_template_value = evidence.get("policy_template")
    if policy_template_value is not None and not isinstance(policy_template_value, str):
        raise TypeError("policy_template must be a string or None")
    timeout_seconds = evidence.get("timeout_seconds")
    if not isinstance(timeout_seconds, int):
        raise TypeError("timeout_seconds must be an integer")
    policy_template = policy_template_value
    relative_run_dir = run_dir.relative_to(exchange_root).as_posix()
    relative_dataset = (run_dir / "input.csv").relative_to(exchange_root).as_posix()

    environment = {
        "python": "3.12",
        "executionPolicy": {
            "profileId": policy_profile_id,
            "template": policy_template,
        },
    }
    environment_bytes = _json_bytes(environment)
    environment_hash = hashlib.sha256(environment_bytes).hexdigest()
    runtime_metadata = {
        "schemaVersion": 1,
        "runId": run_id,
        "datasetPath": relative_dataset,
        "payloadSha256": payload_sha256,
        "environmentHash": environment_hash,
        "policyProfileId": policy_profile_id,
        "policyTemplate": policy_template,
    }
    dataset_literal = json.dumps(str(run_dir / "input.csv"), ensure_ascii=False)
    run_dir_literal = json.dumps(str(run_dir), ensure_ascii=False)
    setup_source = "\n".join(
        (
            "# Spark Agent runtime inputs (generated; do not edit)",
            "from pathlib import Path as _SparkAgentPath",
            f"DATASET_PATH = _SparkAgentPath({dataset_literal})",
            f"RUN_DIR = _SparkAgentPath({run_dir_literal})",
            "dataset_path = DATASET_PATH",
            "run_dir = RUN_DIR",
        )
    )
    notebook: dict[str, object] = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": f"# Analysis run\n\n{objective}",
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {"tags": ["parameters"]},
                "outputs": [],
                "source": setup_source,
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {"tags": ["analysis"]},
                "outputs": [],
                "source": code,
            },
        ],
        "metadata": {"openScienceRuntime": runtime_metadata},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    notebook_bytes = _json_bytes(notebook)
    log = "\n".join(
        (
            "Spark Agent notebook execution",
            f"runId: {run_id}",
            f"status: {status}",
            f"payloadSha256: {payload_sha256}",
            f"environmentHash: {environment_hash}",
            f"policyProfileId: {policy_profile_id}",
            f"policyTemplate: {policy_template or '-'}",
            f"runDir: {relative_run_dir}",
            f"datasetPath: {relative_dataset}",
            f"timeoutSeconds: {timeout_seconds}",
            f"startedAt: {started_at}",
            f"finishedAt: {finished_at}",
            f"durationSeconds: {duration_seconds}",
            "",
            "[stdout]",
            stdout,
            "",
            "[stderr]",
            stderr,
            "",
        )
    )
    files = {
        "input.ipynb": notebook_bytes,
        "executed.ipynb": notebook_bytes,
        "environment.json": environment_bytes,
        "stdout.txt": stdout.encode("utf-8"),
        "stderr.txt": stderr.encode("utf-8"),
        "execution.log": log.encode("utf-8"),
        **dict(generated_files or {}),
    }
    if mutate_files is not None:
        mutate_files(files)

    artifacts: list[RuntimeArtifactInfo] = []
    for name, content in files.items():
        if name in omitted_files:
            continue
        path = run_dir / "nested" / name if name in nested_files else run_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        artifacts.append(
            RuntimeArtifactInfo(
                path=path.relative_to(exchange_root).as_posix(),
                mime_type="application/octet-stream",
                content_hash=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                artifact_type="runtime-output",
            )
        )
    return RuntimeExecutionResult(
        status=status,
        environment_hash=environment_hash,
        stdout=stdout,
        stderr=stderr,
        log=log,
        artifacts=artifacts,
    )


def _request_string(request: Mapping[str, object], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
