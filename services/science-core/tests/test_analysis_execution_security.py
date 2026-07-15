from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, cast

import pytest
from sqlalchemy import select

import open_science_core._analysis_service.execution as execution_module
from open_science_core._analysis_service.errors import AnalysisServiceError
from open_science_core._analysis_service.execution import execute_standalone_analysis_intent
from open_science_core._analysis_service.outputs import list_project_analysis_runs
from open_science_core.analysis import RuntimeExecutionResult, RuntimeServiceError
from open_science_core.models import (
    AnalysisIntentRecord,
    ApprovalRecord,
    ArtifactRecord,
    RunRecord,
    TaskRecord,
)
from test_analysis_service import (
    ServiceEnvironment,
    create_approved_standalone,
    runtime_result,
)
from test_analysis_service import (
    service_environment as _service_environment_fixture,
)

service_environment = _service_environment_fixture


@pytest.mark.asyncio
async def test_finalize_rejects_intent_code_tamper_and_persists_failed_state(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    async def tampering_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        with service_environment.session_factory.begin() as session:
            intent = session.get(AnalysisIntentRecord, intent_id)
            assert intent is not None
            intent.code = "print('tampered during runtime')"
        return runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)

    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=tampering_runtime,
        )
    assert conflict.value.code in {
        "analysis-approval-binding-invalid",
        "analysis-finalization-integrity-conflict",
    }
    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        run = session.scalar(select(RunRecord).where(RunRecord.analysis_intent_id == intent_id))
        assert intent is not None and intent.status == "failed"
        assert run is not None and run.status == "failed"


@pytest.mark.asyncio
async def test_finalize_rejects_approval_hash_tamper_and_persists_failed_state(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    async def tampering_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        with service_environment.session_factory.begin() as session:
            approval = session.scalar(
                select(ApprovalRecord).where(ApprovalRecord.subject_id == intent_id)
            )
            assert approval is not None
            approval.intent_hash = "f" * 64
        return runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)

    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=tampering_runtime,
        )
    assert conflict.value.code in {
        "analysis-approval-binding-invalid",
        "analysis-finalization-integrity-conflict",
    }
    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        run = session.scalar(select(RunRecord).where(RunRecord.analysis_intent_id == intent_id))
        assert intent is not None and intent.status == "failed"
        assert run is not None and run.status == "failed"


@pytest.mark.asyncio
async def test_execution_rejects_incomplete_or_wrong_revision_approval_audit(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)
    with service_environment.session_factory.begin() as session:
        approval = session.scalar(
            select(ApprovalRecord).where(ApprovalRecord.subject_id == intent_id)
        )
        assert approval is not None
        approval.decided_at = None
        approval.row_version = 999

    called = False

    async def forbidden_runtime(**_kwargs: Any) -> RuntimeExecutionResult:
        nonlocal called
        called = True
        raise AssertionError("invalid approval must not reach runtime")

    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=forbidden_runtime,
        )
    assert conflict.value.code == "analysis-approval-binding-invalid"
    assert not called


@pytest.mark.asyncio
async def test_finalize_rejects_task_provenance_tamper_and_persists_failed_state(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    async def tampering_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        with service_environment.session_factory.begin() as session:
            intent = session.get(AnalysisIntentRecord, intent_id)
            task = session.get(TaskRecord, intent.task_id) if intent is not None else None
            assert task is not None
            task.row_version = 999
            task.outputs = {"forged": True}
            task.input_sha256 = "f" * 64
            task.order_index = 999
            task.retries = 999
        return runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)

    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=tampering_runtime,
        )
    assert conflict.value.code == "analysis-finalization-integrity-conflict"
    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        run = session.scalar(select(RunRecord).where(RunRecord.analysis_intent_id == intent_id))
        task = session.get(TaskRecord, intent.task_id) if intent is not None else None
        assert intent is not None and intent.status == "failed"
        assert run is not None and run.status == "failed"
        assert task is not None and task.status == "failed"


@pytest.mark.asyncio
async def test_finalize_rejects_forged_terminal_statuses_and_persists_failed_state(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    async def tampering_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        with service_environment.session_factory.begin() as session:
            intent = session.get(AnalysisIntentRecord, intent_id)
            run = session.scalar(
                select(RunRecord).where(RunRecord.analysis_intent_id == intent_id)
            )
            task = session.get(TaskRecord, intent.task_id) if intent is not None else None
            assert intent is not None and run is not None and task is not None
            intent.status = "completed"
            run.status = "completed"
            task.status = "completed"
        return runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)

    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=tampering_runtime,
        )
    assert conflict.value.code == "analysis-finalization-integrity-conflict"
    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        run = session.scalar(select(RunRecord).where(RunRecord.analysis_intent_id == intent_id))
        task = session.get(TaskRecord, intent.task_id) if intent is not None else None
        assert intent is not None and intent.status == "failed"
        assert run is not None and run.status == "failed"
        assert task is not None and task.status == "failed"


@pytest.mark.asyncio
async def test_failure_convergence_clears_tampered_environment_hash_and_remains_listable(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    async def tampering_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        with service_environment.session_factory.begin() as session:
            run = session.scalar(
                select(RunRecord).where(RunRecord.analysis_intent_id == intent_id)
            )
            assert run is not None
            run.environment_hash = "e" * 64
        return runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)

    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=tampering_runtime,
        )
    assert conflict.value.code == "analysis-finalization-integrity-conflict"

    with service_environment.session_factory() as session:
        [run] = list_project_analysis_runs(session, "project-1")
        assert run.intent_id == intent_id
        assert run.status == "failed"
        assert run.environment_hash is None
        assert run.log == "AnalysisExecutionError: analysis-integrity-error\n"


@pytest.mark.asyncio
async def test_finalize_rejects_preinserted_run_artifact_and_failure_removes_it(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    async def tampering_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        with service_environment.session_factory.begin() as session:
            intent = session.get(AnalysisIntentRecord, intent_id)
            run = session.scalar(
                select(RunRecord).where(RunRecord.analysis_intent_id == intent_id)
            )
            assert intent is not None and run is not None
            session.add(
                ArtifactRecord(
                    id="preinserted-forged-artifact",
                    run_id=run.id,
                    artifact_type="log",
                    path=f"runs/{run.id}/forged.log",
                    mime_type="text/plain",
                    content_hash="f" * 64,
                    parent_artifacts=[intent.dataset_source_id],
                    metadata_json={
                        "sizeBytes": 1,
                        "payloadSha256": intent.payload_sha256,
                    },
                )
            )
        return runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)

    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=tampering_runtime,
        )
    assert conflict.value.code == "analysis-records-incomplete"

    with service_environment.session_factory() as session:
        [run_out] = list_project_analysis_runs(session, "project-1")
        run = session.get(RunRecord, run_out.id)
        artifacts = list(
            session.scalars(select(ArtifactRecord).where(ArtifactRecord.run_id == run_out.id))
        )
        assert run_out.status == "failed"
        assert run is not None and run.status == "failed"
        assert len(artifacts) == 1
        assert artifacts[0].path.endswith("core-execution-error.log")


@pytest.mark.asyncio
async def test_finalize_rejects_artifact_bound_outside_snapshot_run_directory(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent_id = create_approved_standalone(service_environment)
    original_collect = execution_module.collect_runtime_artifacts
    outside = service_environment.root / "outside-run-artifact.txt"
    outside.write_bytes(b"must remain unrelated")

    def forged_collection(**kwargs: Any):
        collected = original_collect(**kwargs)
        return [
            replace(
                item,
                absolute_path=outside,
                project_relative_path=outside.relative_to(service_environment.root).as_posix(),
            )
            if item.project_relative_path.endswith("/stdout.txt")
            else item
            for item in collected
        ]

    monkeypatch.setattr(execution_module, "collect_runtime_artifacts", forged_collection)

    async def successful_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        return runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)

    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=successful_runtime,
        )
    assert conflict.value.code == "analysis-artifact-binding-invalid"
    assert outside.read_bytes() == b"must remain unrelated"
    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        run = session.scalar(select(RunRecord).where(RunRecord.analysis_intent_id == intent_id))
        task = session.get(TaskRecord, intent.task_id) if intent is not None else None
        assert intent is not None and intent.status == "failed"
        assert run is not None and run.status == "failed"
        assert task is not None and task.status == "failed"


@pytest.mark.asyncio
async def test_unknown_runtime_error_is_redacted_and_persists_failed_state(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    async def unexpected_runtime(**_kwargs: Any) -> RuntimeExecutionResult:
        raise RuntimeError("unexpected failure at /Users/private/secret.csv")

    with pytest.raises(AnalysisServiceError) as failure:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=unexpected_runtime,
        )
    assert failure.value.code == "analysis-integrity-error"
    assert "/Users/private" not in failure.value.detail
    assert "secret.csv" not in failure.value.detail
    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        run = session.scalar(select(RunRecord).where(RunRecord.analysis_intent_id == intent_id))
        task = session.get(TaskRecord, intent.task_id) if intent is not None else None
        assert intent is not None and intent.status == "failed"
        assert run is not None and run.status == "failed"
        assert task is not None and task.status == "failed"


@pytest.mark.asyncio
async def test_hydration_failure_does_not_roll_back_committed_terminal_state(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    async def successful_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        return runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)

    def failed_hydration(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("hydrate failed after commit")

    monkeypatch.setattr(execution_module, "analysis_run_out", failed_hydration)

    with pytest.raises(RuntimeError, match="hydrate failed after commit"):
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=successful_runtime,
        )
    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        run = session.scalar(select(RunRecord).where(RunRecord.analysis_intent_id == intent_id))
        assert intent is not None and intent.status == "completed"
    assert run is not None and run.status == "completed"


@pytest.mark.asyncio
async def test_finalize_rejects_coherently_forged_policy_attestation(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    async def forged_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        evidence_request = {
            **kwargs,
            "policy_profile_id": "dataset-analysis-fixed-v1",
            "policy_template": "baseline",
        }
        return runtime_result(
            kwargs["run_dir"],
            service_environment.exchange,
            kwargs,
            evidence_request=evidence_request,
        )

    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=forged_runtime,
        )
    assert conflict.value.code == "analysis-integrity-error"


@pytest.mark.asyncio
async def test_finalize_rejects_notebook_analysis_source_tamper(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    async def forged_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        return runtime_result(
            kwargs["run_dir"],
            service_environment.exchange,
            kwargs,
            evidence_request={**kwargs, "code": "print('forged analysis source')"},
        )

    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=forged_runtime,
        )
    assert conflict.value.code == "analysis-integrity-error"


@pytest.mark.asyncio
async def test_finalize_rejects_notebook_parameters_source_tamper(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    def forge_parameters(files: dict[str, bytes]) -> None:
        for name in ("input.ipynb", "executed.ipynb"):
            notebook_value: object = json.loads(files[name])
            assert isinstance(notebook_value, dict)
            notebook = cast(dict[str, object], notebook_value)
            cells_value = notebook["cells"]
            assert isinstance(cells_value, list)
            cells = cast(list[object], cells_value)
            parameters_value = cells[1]
            assert isinstance(parameters_value, dict)
            parameters = cast(dict[str, object], parameters_value)
            parameters["source"] = "DATASET_PATH = RUN_DIR / 'forged.csv'"
            files[name] = json.dumps(
                notebook,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")

    async def forged_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        return runtime_result(
            kwargs["run_dir"],
            service_environment.exchange,
            kwargs,
            mutate_files=forge_parameters,
        )

    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=forged_runtime,
        )
    assert conflict.value.code == "analysis-integrity-error"


@pytest.mark.asyncio
async def test_finalize_rejects_notebook_with_an_extra_code_cell(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    def add_code_cell(files: dict[str, bytes]) -> None:
        for name in ("input.ipynb", "executed.ipynb"):
            notebook_value: object = json.loads(files[name])
            assert isinstance(notebook_value, dict)
            notebook = cast(dict[str, object], notebook_value)
            cells_value = notebook["cells"]
            assert isinstance(cells_value, list)
            cells = cast(list[object], cells_value)
            cells.append(
                {
                    "cell_type": "code",
                    "execution_count": 3,
                    "metadata": {},
                    "outputs": [],
                    "source": "print('unapproved extra code')",
                }
            )
            files[name] = json.dumps(
                notebook,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")

    async def forged_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        return runtime_result(
            kwargs["run_dir"],
            service_environment.exchange,
            kwargs,
            mutate_files=add_code_cell,
        )

    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=forged_runtime,
        )
    assert conflict.value.code == "analysis-integrity-error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("started_at", "finished_at", "duration_seconds"),
    [
        ("not-a-timestamp", "2026-07-15T00:00:01+00:00", "1.000"),
        ("2026-07-15T01:00:00+01:00", "2026-07-15T01:00:01+01:00", "1.000"),
        ("2026-07-15T00:00:02+00:00", "2026-07-15T00:00:01+00:00", "1.000"),
        ("2026-07-15T00:00:00+00:00", "2026-07-15T00:00:01+00:00", "nan"),
        ("2026-07-15T00:00:00+00:00", "2026-07-15T00:00:01+00:00", "-1.000"),
        ("2026-07-15T00:00:00+00:00", "2026-07-15T00:00:01+00:00", "35.001"),
    ],
)
async def test_finalize_rejects_invalid_execution_log_timing(
    service_environment: ServiceEnvironment,
    started_at: str,
    finished_at: str,
    duration_seconds: str,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    async def forged_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        return runtime_result(
            kwargs["run_dir"],
            service_environment.exchange,
            kwargs,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration_seconds,
        )

    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=forged_runtime,
        )
    assert conflict.value.code == "analysis-integrity-error"


@pytest.mark.asyncio
async def test_finalize_accepts_utc_z_and_divergent_wall_and_monotonic_timings(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    async def successful_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        return runtime_result(
            kwargs["run_dir"],
            service_environment.exchange,
            kwargs,
            started_at="2026-07-15T00:00:00Z",
            finished_at="2026-07-15T01:00:00+00:00",
            duration_seconds="0.25",
        )

    result = await execute_standalone_analysis_intent(
        intent_id,
        session_factory=service_environment.session_factory,
        runtime_executor=successful_runtime,
    )
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_finalize_rejects_execution_log_response_mismatch(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    async def forged_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        result = runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)
        return replace(result, log=result.log + "forged response")

    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=forged_runtime,
        )
    assert conflict.value.code == "analysis-integrity-error"


@pytest.mark.asyncio
async def test_oversized_exchange_cleanup_cannot_block_failed_state_or_lock_release(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    async def oversized_failed_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        run_dir = kwargs["run_dir"]
        for index in range(1_001):
            (run_dir / f"ignored-{index}.bin").write_bytes(b"")
        raise RuntimeServiceError("runtime output rejected")

    with pytest.raises(AnalysisServiceError):
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=oversized_failed_runtime,
        )
    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        task = session.get(TaskRecord, intent.task_id) if intent is not None else None
        run = session.scalar(select(RunRecord).where(RunRecord.analysis_intent_id == intent_id))
        assert intent is not None and intent.status == "failed"
        assert task is not None and task.status == "failed"
        assert run is not None and run.status == "failed"
    assert execution_module.analysis_execution_slot.acquire(blocking=False)
    execution_module.analysis_execution_slot.release()
