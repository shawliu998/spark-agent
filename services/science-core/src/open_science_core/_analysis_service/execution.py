from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import stat
import threading
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from ..analysis import (
    CollectedArtifact,
    RuntimeExecutionResult,
    RuntimeServiceError,
    collect_runtime_artifacts,
    execute_in_runtime,
    validate_python_code,
)
from ..analysis_spec import (
    AnalysisSpec,
    StructuredAnalysisResult,
    analysis_spec_sha256,
    structured_analysis_result_sha256,
)
from ..config import settings
from ..fixed_analysis_policy import (
    COMPILED_ANALYSIS_POLICY_ID,
    COMPILED_ANALYSIS_TEMPLATE,
    FIXED_ANALYSIS_POLICY_ID,
    GENERAL_ANALYSIS_POLICY_ID,
    AnalysisPolicyId,
    AnalysisPolicyTemplate,
    FixedAnalysisPolicyError,
    fixed_analysis_template_for_repair_attempt,
)
from ..models import (
    AnalysisIntentRecord,
    ApprovalRecord,
    ArtifactRecord,
    EventRecord,
    ProjectRecord,
    RunRecord,
    SourceRecord,
    StructuredAnalysisResultRecord,
    TaskRecord,
    WorkflowRecord,
    utc_now,
)
from ..schemas import AnalysisRunOut
from .errors import (
    ANALYSIS_V3_SCHEMA,
    ANALYSIS_V4_SCHEMA,
    AnalysisServiceError,
    execution_http_error,
    runtime_result_error_summary,
    safe_error_summary,
    safe_execution_error,
)
from .filesystem import (
    DirectoryIdentity,
    assert_runtime_input_unchanged,
    child_path,
    cleanup_stale_exchange_entries,
    clear_run_outputs,
    copy_dataset_from_safe_descriptor,
    create_anchored_directory,
    open_workspace_file_without_symlinks,
    remove_exchange_run,
    stat_identity,
    write_run_error_log,
)
from .integrity import (
    approval_for_intent,
    assert_approval_record,
    assert_intent_binding,
    intent_or_error,
    project_or_error,
    recompute_approval_hash,
    validated_execution_records,
)
from .outputs import analysis_run_out

SessionFactory = Callable[[], Session]
RuntimeExecutor = Callable[..., Awaitable[RuntimeExecutionResult]]

analysis_execution_slot = threading.Lock()
_RUNTIME_DURATION_GRACE_SECONDS = 30.0


@dataclass(slots=True)
class _ExecutionSnapshot:
    intent_id: str
    task_id: str
    project_id: str
    workflow_id: str | None
    run_id: str
    dataset_source_id: str
    dataset_content_hash: str
    objective: str
    code: str
    payload_sha256: str
    timeout_seconds: int
    policy_profile_id: AnalysisPolicyId
    policy_template: AnalysisPolicyTemplate | None
    analysis_spec_id: str | None
    analysis_spec_sha256: str | None
    dataset_profile_sha256: str | None
    compiler_version: str | None
    approved_code_sha256: str | None
    project_path: Path
    dataset_path: Path
    run_dir: Path
    exchange_run_dir: Path
    runtime_dataset_path: Path
    expected_workflow_id: str | None
    approval_workflow_revision: int | None
    intent_fingerprint: tuple[object, ...]
    task_fingerprint: tuple[object, ...]
    approval_fingerprint: tuple[object, ...]
    run_directory_identity: DirectoryIdentity | None = None
    exchange_run_directory_identity: DirectoryIdentity | None = None


class _CommittedFinalizationError(Exception):
    def __init__(self, error: Exception) -> None:
        super().__init__(str(error))
        self.error = error


def _freeze_json(value: Any) -> object:
    if isinstance(value, dict):
        mapping = cast(dict[object, Any], value)
        return tuple(sorted((str(key), _freeze_json(item)) for key, item in mapping.items()))
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in cast(list[Any], value))
    return value


def _intent_fingerprint(intent: AnalysisIntentRecord) -> tuple[object, ...]:
    return (
        intent.id,
        intent.task_id,
        intent.project_id,
        intent.workflow_id,
        intent.plan_step_id,
        intent.previous_intent_id,
        intent.analysis_spec_id,
        intent.spec_sha256,
        intent.dataset_source_id,
        intent.dataset_content_hash,
        intent.dataset_profile_sha256,
        intent.objective,
        intent.code,
        intent.compiler_version,
        intent.code_sha256,
        intent.runtime_policy_id,
        _freeze_json(intent.expected_outputs),
        intent.timeout_seconds,
        intent.risk_level,
        intent.repair_attempt,
        _freeze_json(intent.error_summary),
        intent.code_diff,
        intent.payload_sha256,
        intent.decision,
    )


def _task_fingerprint(task: TaskRecord) -> tuple[object, ...]:
    return (
        task.id,
        task.project_id,
        task.workflow_id,
        task.plan_id,
        task.step_key,
        task.order_index,
        task.objective,
        task.task_type,
        _freeze_json(task.inputs),
        _freeze_json(task.expected_outputs),
        _freeze_json(task.outputs),
        _freeze_json(task.acceptance_criteria),
        _freeze_json(task.permissions),
        task.risk_level,
        task.input_sha256,
        task.row_version,
        task.retries,
        task.timeout_seconds,
        task.created_at,
        task.started_at,
        task.finished_at,
    )


def _approval_fingerprint(record: ApprovalRecord) -> tuple[object, ...]:
    return (
        record.id,
        record.task_id,
        record.workflow_id,
        record.plan_id,
        record.subject_type,
        record.subject_id,
        record.payload_schema_version,
        record.row_version,
        record.intent_hash,
        record.requested_action,
        record.risk_level,
        record.reason,
        _freeze_json(record.affected_resources),
        record.user_decision,
        record.created_at,
        record.decided_at,
    )


async def execute_standalone_analysis_intent(
    intent_id: str,
    *,
    session_factory: SessionFactory,
    runtime_executor: RuntimeExecutor = execute_in_runtime,
) -> AnalysisRunOut:
    return await _execute_analysis_intent(
        intent_id,
        session_factory=session_factory,
        runtime_executor=runtime_executor,
        expected_workflow_id=None,
        approval_workflow_revision=None,
    )


async def execute_workflow_analysis_intent(
    intent_id: str,
    *,
    session_factory: SessionFactory,
    expected_workflow_id: str,
    approval_workflow_revision: int,
    runtime_executor: RuntimeExecutor = execute_in_runtime,
) -> AnalysisRunOut:
    return await _execute_analysis_intent(
        intent_id,
        session_factory=session_factory,
        runtime_executor=runtime_executor,
        expected_workflow_id=expected_workflow_id,
        approval_workflow_revision=approval_workflow_revision,
    )


async def _execute_analysis_intent(
    intent_id: str,
    *,
    session_factory: SessionFactory,
    runtime_executor: RuntimeExecutor,
    expected_workflow_id: str | None,
    approval_workflow_revision: int | None,
) -> AnalysisRunOut:
    if not analysis_execution_slot.acquire(blocking=False):
        raise AnalysisServiceError(
            409,
            "Another analysis execution is already active",
            code="analysis-execution-active",
        )
    try:
        snapshot: _ExecutionSnapshot | None = None
        try:
            cleanup_stale_exchange_entries(reject_recent=True)
            project_path, dataset_path, dataset_content_hash = _execution_source_preflight(
                session_factory,
                intent_id,
                expected_workflow_id=expected_workflow_id,
                approval_workflow_revision=approval_workflow_revision,
            )
            _verify_dataset_before_claim(
                project_path,
                dataset_path,
                dataset_content_hash,
            )
            snapshot = _claim_execution(
                session_factory,
                intent_id,
                expected_workflow_id=expected_workflow_id,
                approval_workflow_revision=approval_workflow_revision,
            )
            _prepare_runtime_inputs(snapshot)
        except AnalysisServiceError:
            raise
        except Exception as error:
            if snapshot is not None:
                _persist_execution_failure(session_factory, snapshot, error)
            raise execution_http_error(error) from error

        assert snapshot is not None
        try:
            if (
                snapshot.run_directory_identity is None
                or snapshot.exchange_run_directory_identity is None
            ):
                raise RuntimeServiceError("analysis-directory-identity-unavailable")
            runtime_result = await runtime_executor(
                run_id=snapshot.run_id,
                run_dir=snapshot.exchange_run_dir,
                dataset_path=snapshot.runtime_dataset_path,
                objective=snapshot.objective,
                code=snapshot.code,
                payload_sha256=snapshot.payload_sha256,
                timeout_seconds=snapshot.timeout_seconds,
                policy_profile_id=snapshot.policy_profile_id,
                policy_template=snapshot.policy_template,
                analysis_spec_id=snapshot.analysis_spec_id,
                analysis_spec_sha256=snapshot.analysis_spec_sha256,
                dataset_profile_sha256=snapshot.dataset_profile_sha256,
                compiler_version=snapshot.compiler_version,
                approved_code_sha256=snapshot.approved_code_sha256,
            )
            assert_runtime_input_unchanged(
                exchange_run_dir=snapshot.exchange_run_dir,
                runtime_dataset_path=snapshot.runtime_dataset_path,
                dataset_content_hash=snapshot.dataset_content_hash,
                expected_exchange_run_identity=snapshot.exchange_run_directory_identity,
            )
            collected = collect_runtime_artifacts(
                runtime_result=runtime_result,
                exchange_run_dir=snapshot.exchange_run_dir,
                final_run_dir=snapshot.run_dir,
                project_dir=snapshot.project_path,
                expected_exchange_run_identity=snapshot.exchange_run_directory_identity,
                expected_final_run_identity=snapshot.run_directory_identity,
            )
            _verify_runtime_policy_attestation(snapshot, runtime_result, collected)
        except asyncio.CancelledError:
            _persist_execution_failure(
                session_factory,
                snapshot,
                RuntimeServiceError("analysis-execution-cancelled"),
            )
            raise
        except Exception as error:
            _persist_execution_failure(session_factory, snapshot, error)
            raise execution_http_error(error) from error
        finally:
            remove_exchange_run(
                snapshot.exchange_run_dir,
                snapshot.exchange_run_directory_identity,
            )

        try:
            return _finalize_execution(session_factory, snapshot, runtime_result, collected)
        except _CommittedFinalizationError as committed:
            raise committed.error from committed
        except AnalysisServiceError as error:
            _persist_execution_failure(session_factory, snapshot, error)
            raise
        except Exception as error:
            _persist_execution_failure(session_factory, snapshot, error)
            raise execution_http_error(error) from error
    finally:
        analysis_execution_slot.release()


def _execution_source_preflight(
    session_factory: SessionFactory,
    intent_id: str,
    *,
    expected_workflow_id: str | None,
    approval_workflow_revision: int | None,
) -> tuple[Path, Path, str]:
    with session_factory() as session:
        intent, project, dataset, _task, approval, _current_hash = validated_execution_records(
            session,
            intent_id,
            expected_workflow_id=expected_workflow_id,
            approval_workflow_revision=approval_workflow_revision,
        )
        policy_profile_id, policy_template = _analysis_policy_for_intent(intent, approval)
        try:
            validate_python_code(
                intent.code,
                policy_profile_id=policy_profile_id,
                policy_template=policy_template,
                approved_code_sha256=(
                    intent.code_sha256
                    if policy_profile_id == COMPILED_ANALYSIS_POLICY_ID
                    else None
                ),
            )
        except ValueError as error:
            raise AnalysisServiceError(
                409,
                "Approved analysis code no longer satisfies its execution policy",
                code="analysis-code-policy-mismatch",
            ) from error
        return Path(project.project_path), Path(dataset.local_path), dataset.content_hash


def _verify_dataset_before_claim(
    project_path: Path,
    dataset_path: Path,
    expected_content_hash: str,
) -> None:
    try:
        descriptor = open_workspace_file_without_symlinks(project_path, dataset_path)
    except RuntimeServiceError as error:
        message = str(error)
        if "symlink" in message:
            detail = "Dataset path may not contain symbolic links"
            code = "dataset-path-symlink"
        else:
            detail = "Dataset file is missing"
            code = "dataset-file-missing"
        raise AnalysisServiceError(409, detail, code=code) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise AnalysisServiceError(
                409,
                "Dataset file cannot be opened as one private regular file",
                code="dataset-file-invalid",
            )
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if stat_identity(before) != stat_identity(after):
            raise AnalysisServiceError(
                409,
                "Dataset changed while its approval binding was checked",
                code="dataset-file-changed",
            )
        if digest.hexdigest() != expected_content_hash:
            raise AnalysisServiceError(
                409,
                "Dataset content hash no longer matches its source",
                code="dataset-content-hash-mismatch",
            )
    finally:
        os.close(descriptor)


def _claim_execution(
    session_factory: SessionFactory,
    intent_id: str,
    *,
    expected_workflow_id: str | None,
    approval_workflow_revision: int | None,
) -> _ExecutionSnapshot:
    with session_factory() as session, session.begin():
        intent, project, dataset, task, approval, current_hash = validated_execution_records(
            session,
            intent_id,
            expected_workflow_id=expected_workflow_id,
            approval_workflow_revision=approval_workflow_revision,
        )

        claimed_at = utc_now()
        claim = session.execute(
            update(AnalysisIntentRecord)
            .where(
                AnalysisIntentRecord.id == intent.id,
                AnalysisIntentRecord.status == "approved",
            )
            .values(status="executing", updated_at=claimed_at)
        )
        if cast(CursorResult[object], claim).rowcount != 1:
            raise AnalysisServiceError(
                409,
                "Analysis intent was already claimed",
                code="analysis-already-claimed",
            )

        run_id = str(uuid.uuid4())
        run = RunRecord(
            id=run_id,
            task_id=task.id,
            analysis_intent_id=intent.id,
            environment_hash=None,
            input_artifacts=[dataset.id],
            output_artifacts=[],
            status="running",
        )
        task.status = "running"
        session.add(run)
        session.flush()
        if intent.workflow_id is None:
            session.add(
                EventRecord(
                    id=str(uuid.uuid4()),
                    project_id=project.id,
                    event_type="analysis.run.started",
                    payload={
                        "analysisIntentId": intent.id,
                        "runId": run_id,
                        "payloadSha256": current_hash,
                    },
                )
            )

        project_path = Path(project.project_path)
        dataset_path = Path(dataset.local_path)
        run_dir = child_path(project_path, f"runs/{run_id}")
        exchange_runs_dir = child_path(settings.runtime_exchange_dir, "runs")
        exchange_run_dir = child_path(exchange_runs_dir, run_id)
        policy_profile_id, policy_template = _analysis_policy_for_intent(intent, approval)
        compiled = policy_profile_id == COMPILED_ANALYSIS_POLICY_ID
        return _ExecutionSnapshot(
            intent_id=intent.id,
            task_id=task.id,
            project_id=project.id,
            workflow_id=intent.workflow_id,
            run_id=run_id,
            dataset_source_id=dataset.id,
            dataset_content_hash=dataset.content_hash,
            objective=intent.objective,
            code=intent.code,
            payload_sha256=current_hash,
            timeout_seconds=intent.timeout_seconds or task.timeout_seconds,
            policy_profile_id=policy_profile_id,
            policy_template=policy_template,
            analysis_spec_id=intent.analysis_spec_id if compiled else None,
            analysis_spec_sha256=intent.spec_sha256 if compiled else None,
            dataset_profile_sha256=(intent.dataset_profile_sha256 if compiled else None),
            compiler_version=intent.compiler_version if compiled else None,
            approved_code_sha256=intent.code_sha256 if compiled else None,
            project_path=project_path,
            dataset_path=dataset_path,
            run_dir=run_dir,
            exchange_run_dir=exchange_run_dir,
            runtime_dataset_path=child_path(exchange_run_dir, "input.csv"),
            expected_workflow_id=expected_workflow_id,
            approval_workflow_revision=approval_workflow_revision,
            intent_fingerprint=_intent_fingerprint(intent),
            task_fingerprint=_task_fingerprint(task),
            approval_fingerprint=_approval_fingerprint(approval),
        )


def _prepare_runtime_inputs(snapshot: _ExecutionSnapshot) -> None:
    run_directory_identity = create_anchored_directory(
        snapshot.project_path,
        snapshot.run_dir,
        mode=0o700,
        create_intermediates=False,
    )
    snapshot.run_directory_identity = run_directory_identity
    exchange_run_directory_identity = create_anchored_directory(
        settings.runtime_exchange_dir,
        snapshot.exchange_run_dir,
        mode=0o1777,
        create_intermediates=True,
    )
    snapshot.exchange_run_directory_identity = exchange_run_directory_identity
    project_dataset_path = child_path(snapshot.run_dir, "input.csv")
    copy_dataset_from_safe_descriptor(
        workspace_root=snapshot.project_path,
        source_path=snapshot.dataset_path,
        destinations=((project_dataset_path, 0o400), (snapshot.runtime_dataset_path, 0o444)),
        expected_content_hash=snapshot.dataset_content_hash,
        expected_destination_directories={
            Path(os.path.abspath(snapshot.run_dir)): run_directory_identity,
            Path(os.path.abspath(snapshot.exchange_run_dir)): exchange_run_directory_identity,
        },
    )


def _finalize_execution(
    session_factory: SessionFactory,
    snapshot: _ExecutionSnapshot,
    runtime_result: RuntimeExecutionResult,
    collected: Sequence[CollectedArtifact],
) -> AnalysisRunOut:
    terminal_conflict: AnalysisServiceError | None = None
    with session_factory() as session, session.begin():
        intent, project, dataset, task, run = _revalidate_claimed_execution(
            session,
            snapshot,
        )
        workflow = (
            session.get(WorkflowRecord, intent.workflow_id)
            if intent.workflow_id is not None
            else None
        )
        if intent.workflow_id is not None and (
            workflow is None
            or workflow.status != "running"
            or workflow.cancel_requested_at is not None
            or task.status == "cancelled"
            or (
                snapshot.approval_workflow_revision is not None
                and workflow.row_version != snapshot.approval_workflow_revision
            )
        ):
            terminal_conflict = AnalysisServiceError(
                409,
                "Workflow changed or was cancelled before analysis completion",
                code="workflow-execution-superseded",
            )
        structured_result = _validated_compiled_result(snapshot, collected)
        structured_result_sha256 = (
            None
            if structured_result is None
            else structured_analysis_result_sha256(structured_result)
        )
        artifact_records: list[ArtifactRecord] = []
        artifact_paths: set[str] = set()
        for item in collected:
            _assert_collected_artifact_binding(snapshot, item)
            if item.project_relative_path in artifact_paths:
                raise AnalysisServiceError(
                    409,
                    "Analysis output contains duplicate artifact paths",
                    code="analysis-artifact-binding-invalid",
                )
            artifact_paths.add(item.project_relative_path)
            artifact = ArtifactRecord(
                id=str(uuid.uuid4()),
                run_id=run.id,
                artifact_type=item.artifact_type,
                path=item.project_relative_path,
                mime_type=item.mime_type,
                content_hash=item.content_hash,
                parent_artifacts=[dataset.id],
                metadata_json={
                    "sizeBytes": item.size_bytes,
                    "payloadSha256": snapshot.payload_sha256,
                    "policyProfileId": snapshot.policy_profile_id,
                    "policyTemplate": snapshot.policy_template,
                    **_compiled_provenance_attestation(snapshot),
                    **(
                        {"structuredResultSha256": structured_result_sha256}
                        if structured_result_sha256 is not None
                        and Path(item.project_relative_path).name == "results.json"
                        else {}
                    ),
                },
            )
            artifact_records.append(artifact)
            session.add(artifact)

        if structured_result is not None:
            assert snapshot.analysis_spec_id is not None
            assert structured_result_sha256 is not None
            session.add(
                StructuredAnalysisResultRecord(
                    id=str(uuid.uuid4()),
                    analysis_spec_id=snapshot.analysis_spec_id,
                    analysis_intent_id=intent.id,
                    run_id=run.id,
                    schema_version="1",
                    result_json=structured_result.model_dump(
                        mode="json", by_alias=True
                    ),
                    result_sha256=structured_result_sha256,
                )
            )

        run.environment_hash = runtime_result.environment_hash
        run.output_artifacts = [artifact.path for artifact in artifact_records]
        log_artifact = next(
            (
                artifact
                for artifact in artifact_records
                if Path(artifact.path).name == "execution.log"
            ),
            None,
        )
        run.logs_path = log_artifact.path if log_artifact is not None else None
        run.status = "failed" if terminal_conflict is not None else runtime_result.status
        run.finished_at = utc_now()
        intent.status = run.status
        if task.status != "cancelled":
            task.status = run.status
        if intent.workflow_id is not None and run.status == "failed":
            intent.error_summary = runtime_result_error_summary(
                superseded=terminal_conflict is not None
            )
        if intent.workflow_id is None:
            session.add(
                EventRecord(
                    id=str(uuid.uuid4()),
                    project_id=project.id,
                    event_type=f"analysis.run.{runtime_result.status}",
                    payload={
                        "analysisIntentId": intent.id,
                        "runId": run.id,
                        "payloadSha256": snapshot.payload_sha256,
                        "environmentHash": runtime_result.environment_hash,
                        "artifactCount": len(artifact_records),
                    },
                )
            )
        session.flush()
    if terminal_conflict is not None:
        raise _CommittedFinalizationError(terminal_conflict)
    try:
        with session_factory() as read_session:
            intent = intent_or_error(read_session, snapshot.intent_id)
            run = read_session.get(RunRecord, snapshot.run_id)
            project = project_or_error(read_session, snapshot.project_id)
            if run is None:
                raise AnalysisServiceError(
                    409,
                    "Analysis run disappeared after completion",
                    code="analysis-records-incomplete",
                )
            return analysis_run_out(read_session, run, intent, project)
    except Exception as error:
        raise _CommittedFinalizationError(error) from error


def _validated_compiled_result(
    snapshot: _ExecutionSnapshot,
    collected: Sequence[CollectedArtifact],
) -> StructuredAnalysisResult | None:
    if snapshot.analysis_spec_id is None:
        return None
    captured = {
        Path(item.project_relative_path).name: item
        for item in collected
        if Path(item.project_relative_path).name
        in {"analysis-spec.json", "results.json"}
    }
    if set(captured) != {"analysis-spec.json", "results.json"} or any(
        item.attestation_bytes is None for item in captured.values()
    ):
        raise AnalysisServiceError(
            409,
            "Compiled analysis output is missing its structured evidence",
            code="analysis-structured-result-missing",
        )
    spec_bytes = captured["analysis-spec.json"].attestation_bytes
    result_bytes = captured["results.json"].attestation_bytes
    assert spec_bytes is not None
    assert result_bytes is not None
    try:
        spec = AnalysisSpec.model_validate_json(spec_bytes)
        result = StructuredAnalysisResult.model_validate_json(result_bytes)
    except (ValueError, UnicodeDecodeError) as error:
        raise AnalysisServiceError(
            409,
            "Compiled analysis output does not match the strict result schema",
            code="analysis-structured-result-invalid",
        ) from error
    if (
        snapshot.analysis_spec_sha256 is None
        or snapshot.dataset_profile_sha256 is None
        or analysis_spec_sha256(spec) != snapshot.analysis_spec_sha256
        or spec.dataset_source_id != snapshot.dataset_source_id
        or spec.dataset_content_hash != snapshot.dataset_content_hash
        or spec.dataset_profile_hash != snapshot.dataset_profile_sha256
        or result.objective != spec.objective
        or result.operation_type != spec.operation.type
        or result.dataset_source_id != spec.dataset_source_id
        or result.dataset_content_hash != spec.dataset_content_hash
        or result.dataset_profile_hash != spec.dataset_profile_hash
        or captured["analysis-spec.json"].content_hash
        != snapshot.analysis_spec_sha256
        or captured["results.json"].content_hash
        != structured_analysis_result_sha256(result)
        or not _structured_result_matches_spec(spec, result)
    ):
        raise AnalysisServiceError(
            409,
            "Compiled analysis output does not match the approved AnalysisSpec",
            code="analysis-structured-result-binding-invalid",
        )
    return result


def _structured_result_matches_spec(
    spec: AnalysisSpec,
    result: StructuredAnalysisResult,
) -> bool:
    operation = spec.operation
    operation_result = result.result
    if operation.type == "descriptive":
        return (
            operation_result.type == "descriptive"
            and [item.column for item in operation_result.columns]
            == operation.columns
            and result.requested_method == "descriptive"
        )
    if operation.type == "two-group-comparison":
        return (
            operation_result.type == "two-group-comparison"
            and operation_result.outcome_column == operation.outcome_column
            and operation_result.group_column == operation.group_column
            and operation_result.groups == operation.groups
            and result.requested_method == operation.method
        )
    return (
        operation_result.type == "correlation"
        and operation_result.x_column == operation.x_column
        and operation_result.y_column == operation.y_column
        and result.requested_method == operation.method
    )


def _verify_runtime_policy_attestation(
    snapshot: _ExecutionSnapshot,
    runtime_result: RuntimeExecutionResult,
    collected: Sequence[CollectedArtifact],
) -> None:
    required_names = {
        "input.ipynb",
        "executed.ipynb",
        "environment.json",
        "execution.log",
    }
    evidence: dict[str, CollectedArtifact] = {}
    run_root = Path(os.path.abspath(snapshot.run_dir))
    for item in collected:
        if item.attestation_bytes is None:
            continue
        actual = Path(os.path.abspath(item.absolute_path))
        try:
            relative = actual.relative_to(run_root)
        except ValueError as error:
            raise RuntimeServiceError("runtime policy attestation escaped its run") from error
        if relative.as_posix() not in required_names:
            continue
        if relative.name in evidence:
            raise RuntimeServiceError("runtime policy attestation is duplicated")
        if hashlib.sha256(item.attestation_bytes).hexdigest() != item.content_hash:
            raise RuntimeServiceError("runtime policy attestation hash is inconsistent")
        evidence[relative.name] = item
    if set(evidence) != required_names:
        raise RuntimeServiceError("runtime policy attestation is incomplete")

    environment_bytes = _attestation_bytes(evidence, "environment.json")
    if hashlib.sha256(environment_bytes).hexdigest() != runtime_result.environment_hash:
        raise RuntimeServiceError("runtime environment attestation hash is invalid")
    environment = _attestation_json(environment_bytes, "environment.json")
    if environment.get("executionPolicy") != {
        "profileId": snapshot.policy_profile_id,
        "template": snapshot.policy_template,
        **_compiled_provenance_attestation(snapshot),
    }:
        raise RuntimeServiceError("runtime environment policy attestation is invalid")

    expected_runtime_metadata = {
        "schemaVersion": 1,
        "runId": snapshot.run_id,
        "datasetPath": snapshot.runtime_dataset_path.relative_to(
            settings.runtime_exchange_dir
        ).as_posix(),
        "payloadSha256": snapshot.payload_sha256,
        "environmentHash": runtime_result.environment_hash,
        "policyProfileId": snapshot.policy_profile_id,
        "policyTemplate": snapshot.policy_template,
        **_compiled_provenance_attestation(snapshot),
    }
    for name in ("input.ipynb", "executed.ipynb"):
        notebook = _attestation_json(_attestation_bytes(evidence, name), name)
        metadata_value = notebook.get("metadata")
        if (
            not isinstance(metadata_value, dict)
            or cast(dict[str, object], metadata_value).get("openScienceRuntime")
            != expected_runtime_metadata
        ):
            raise RuntimeServiceError(f"runtime notebook policy attestation is invalid: {name}")
        cells_value = notebook.get("cells")
        if not isinstance(cells_value, list):
            raise RuntimeServiceError(f"runtime notebook cells are invalid: {name}")
        _verify_runtime_notebook_cells(
            snapshot,
            name,
            cast(list[object], cells_value),
        )

    log_bytes = _attestation_bytes(evidence, "execution.log")
    try:
        log_text = log_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeServiceError("runtime execution log is not UTF-8") from error
    if log_text != runtime_result.log:
        raise RuntimeServiceError("runtime execution log response does not match its artifact")
    _verify_execution_log_header(snapshot, runtime_result, log_text)


def _attestation_bytes(
    evidence: dict[str, CollectedArtifact],
    name: str,
) -> bytes:
    content = evidence[name].attestation_bytes
    if content is None:
        raise RuntimeServiceError(f"runtime policy attestation content is missing: {name}")
    return content


def _attestation_json(content: bytes, name: str) -> dict[str, object]:
    try:
        decoded: object = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeServiceError(f"runtime policy attestation is invalid JSON: {name}") from error
    if not isinstance(decoded, dict):
        raise RuntimeServiceError(f"runtime policy attestation is not an object: {name}")
    return cast(dict[str, object], decoded)


def _notebook_source(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = cast(list[object], value)
        if all(isinstance(part, str) for part in parts):
            return "".join(cast(list[str], parts))
    return None


def _verify_runtime_notebook_cells(
    snapshot: _ExecutionSnapshot,
    name: str,
    cells: list[object],
) -> None:
    dataset_literal = json.dumps(str(snapshot.runtime_dataset_path), ensure_ascii=False)
    run_dir_literal = json.dumps(str(snapshot.exchange_run_dir), ensure_ascii=False)
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
    expected_cells: tuple[tuple[str, str, list[str] | None], ...] = (
        ("markdown", f"# Analysis run\n\n{snapshot.objective.strip()}", None),
        ("code", setup_source, ["parameters"]),
        ("code", snapshot.code, ["analysis"]),
    )
    if len(cells) != len(expected_cells):
        raise RuntimeServiceError(f"runtime notebook cell structure is invalid: {name}")
    for cell_value, (cell_type, source, tags) in zip(cells, expected_cells, strict=True):
        if not isinstance(cell_value, dict):
            raise RuntimeServiceError(f"runtime notebook cell structure is invalid: {name}")
        cell = cast(dict[str, object], cell_value)
        metadata_value = cell.get("metadata")
        if not isinstance(metadata_value, dict):
            raise RuntimeServiceError(f"runtime notebook cell metadata is invalid: {name}")
        metadata = cast(dict[str, object], metadata_value)
        tags_are_valid = (
            "tags" not in metadata if tags is None else metadata.get("tags") == tags
        )
        if (
            cell.get("cell_type") != cell_type
            or _notebook_source(cell.get("source")) != source
            or not tags_are_valid
        ):
            raise RuntimeServiceError(f"runtime notebook cell binding is invalid: {name}")


def _verify_execution_log_header(
    snapshot: _ExecutionSnapshot,
    runtime_result: RuntimeExecutionResult,
    log_text: str,
) -> None:
    header_lines = log_text.split("\n\n", maxsplit=1)[0].splitlines()
    expected_keys = [
        "runId",
        "status",
        "payloadSha256",
        "environmentHash",
        "policyProfileId",
        "policyTemplate",
        *(
            [
                "analysisSpecId",
                "analysisSpecSha256",
                "datasetProfileSha256",
                "compilerVersion",
                "approvedCodeSha256",
            ]
            if snapshot.analysis_spec_id is not None
            else []
        ),
        "runDir",
        "datasetPath",
        "timeoutSeconds",
        "startedAt",
        "finishedAt",
        "durationSeconds",
    ]
    if not header_lines or header_lines[0] != "Spark Agent notebook execution":
        raise RuntimeServiceError("runtime execution log header is invalid")
    fields: dict[str, str] = {}
    observed_keys: list[str] = []
    for line in header_lines[1:]:
        key, separator, value = line.partition(": ")
        if not separator or key in fields:
            raise RuntimeServiceError("runtime execution log header is invalid")
        fields[key] = value
        observed_keys.append(key)
    if observed_keys != expected_keys:
        raise RuntimeServiceError("runtime execution log fields are invalid")
    expected_values = {
        "runId": snapshot.run_id,
        "status": runtime_result.status,
        "payloadSha256": snapshot.payload_sha256,
        "environmentHash": runtime_result.environment_hash,
        "policyProfileId": snapshot.policy_profile_id,
        "policyTemplate": snapshot.policy_template or "-",
        **_compiled_provenance_attestation(snapshot),
        "runDir": snapshot.exchange_run_dir.relative_to(
            settings.runtime_exchange_dir
        ).as_posix(),
        "datasetPath": snapshot.runtime_dataset_path.relative_to(
            settings.runtime_exchange_dir
        ).as_posix(),
        "timeoutSeconds": str(snapshot.timeout_seconds),
    }
    if any(fields.get(key) != value for key, value in expected_values.items()):
        raise RuntimeServiceError("runtime execution log binding is invalid")
    started_at = _parse_runtime_utc_timestamp(fields["startedAt"])
    finished_at = _parse_runtime_utc_timestamp(fields["finishedAt"])
    if finished_at < started_at:
        raise RuntimeServiceError("runtime execution log timestamps are invalid")
    duration_text = fields["durationSeconds"]
    try:
        duration_seconds = float(duration_text)
    except ValueError as error:
        raise RuntimeServiceError("runtime execution log duration is invalid") from error
    if not math.isfinite(duration_seconds) or duration_seconds < 0:
        raise RuntimeServiceError("runtime execution log duration is invalid")
    if duration_seconds > snapshot.timeout_seconds + _RUNTIME_DURATION_GRACE_SECONDS:
        raise RuntimeServiceError("runtime execution log duration exceeds its allowed bound")


def _parse_runtime_utc_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise RuntimeServiceError("runtime execution log timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise RuntimeServiceError("runtime execution log timestamp is invalid")
    return parsed


def _analysis_policy_for_intent(
    intent: AnalysisIntentRecord,
    approval: ApprovalRecord,
) -> tuple[AnalysisPolicyId, AnalysisPolicyTemplate | None]:
    if intent.workflow_id is None:
        return GENERAL_ANALYSIS_POLICY_ID, None
    if approval.payload_schema_version == ANALYSIS_V4_SCHEMA:
        compiled_fields = (
            intent.analysis_spec_id,
            intent.spec_sha256,
            intent.dataset_profile_sha256,
            intent.compiler_version,
            intent.code_sha256,
            intent.runtime_policy_id,
        )
        if (
            any(value is None for value in compiled_fields)
            or intent.runtime_policy_id != COMPILED_ANALYSIS_POLICY_ID
            or intent.compiler_version != COMPILED_ANALYSIS_TEMPLATE
            or intent.repair_attempt != 0
            or intent.previous_intent_id is not None
            or intent.code_diff is not None
            or intent.code_sha256
            != hashlib.sha256(intent.code.encode("utf-8")).hexdigest()
        ):
            raise AnalysisServiceError(
                409,
                "Workflow analysis compiled policy binding is invalid",
                code="analysis-code-policy-mismatch",
            )
        return COMPILED_ANALYSIS_POLICY_ID, COMPILED_ANALYSIS_TEMPLATE
    if approval.payload_schema_version != ANALYSIS_V3_SCHEMA:
        raise AnalysisServiceError(
            409,
            "The approved workflow analysis predates execution policy binding",
            code="analysis-policy-binding-upgrade-required",
        )
    if intent.repair_attempt is None:
        raise AnalysisServiceError(
            409,
            "Workflow analysis policy binding is incomplete",
            code="analysis-code-policy-mismatch",
        )
    try:
        template = fixed_analysis_template_for_repair_attempt(intent.repair_attempt)
    except FixedAnalysisPolicyError as error:
        raise AnalysisServiceError(
            409,
            "Workflow analysis policy binding is invalid",
            code="analysis-code-policy-mismatch",
        ) from error
    return FIXED_ANALYSIS_POLICY_ID, template


def _compiled_provenance_attestation(snapshot: _ExecutionSnapshot) -> dict[str, str]:
    if snapshot.analysis_spec_id is None:
        return {}
    if (
        snapshot.analysis_spec_sha256 is None
        or snapshot.dataset_profile_sha256 is None
        or snapshot.compiler_version is None
        or snapshot.approved_code_sha256 is None
    ):
        raise RuntimeServiceError("runtime compiled provenance is incomplete")
    return {
        "analysisSpecId": snapshot.analysis_spec_id,
        "analysisSpecSha256": snapshot.analysis_spec_sha256,
        "datasetProfileSha256": snapshot.dataset_profile_sha256,
        "compilerVersion": snapshot.compiler_version,
        "approvedCodeSha256": snapshot.approved_code_sha256,
    }


def _revalidate_claimed_execution(
    session: Session,
    snapshot: _ExecutionSnapshot,
) -> tuple[AnalysisIntentRecord, ProjectRecord, SourceRecord, TaskRecord, RunRecord]:
    intent = intent_or_error(session, snapshot.intent_id)
    project = project_or_error(session, snapshot.project_id)
    dataset = session.get(SourceRecord, snapshot.dataset_source_id)
    task = session.get(TaskRecord, snapshot.task_id)
    run = session.get(RunRecord, snapshot.run_id)
    approval = approval_for_intent(session, intent)
    existing_artifact_id = session.scalar(
        select(ArtifactRecord.id).where(ArtifactRecord.run_id == snapshot.run_id).limit(1)
    )
    if (
        dataset is None
        or task is None
        or run is None
        or approval is None
        or existing_artifact_id is not None
    ):
        raise AnalysisServiceError(
            409,
            "Analysis execution records changed before completion",
            code="analysis-records-incomplete",
        )
    assert_intent_binding(session, intent, task, dataset, project, run=run)
    assert_approval_record(session, intent, approval)
    current_hash = recompute_approval_hash(
        session,
        intent,
        expected_workflow_revision=snapshot.approval_workflow_revision,
    )
    if (
        intent.status != "executing"
        or run.status != "running"
        or (
            task.status != "running"
            and not (intent.workflow_id is not None and task.status == "cancelled")
        )
        or intent.decision != "approved"
        or approval.user_decision != "approved"
        or current_hash != snapshot.payload_sha256
        or intent.payload_sha256 != snapshot.payload_sha256
        or approval.intent_hash != snapshot.payload_sha256
        or _intent_fingerprint(intent) != snapshot.intent_fingerprint
        or _task_fingerprint(task) != snapshot.task_fingerprint
        or _approval_fingerprint(approval) != snapshot.approval_fingerprint
        or Path(project.project_path) != snapshot.project_path
        or Path(dataset.local_path or "") != snapshot.dataset_path
        or dataset.content_hash != snapshot.dataset_content_hash
        or intent.workflow_id != snapshot.expected_workflow_id
        or run.id != snapshot.run_id
        or run.task_id != snapshot.task_id
        or run.analysis_intent_id != snapshot.intent_id
        or run.input_artifacts != [snapshot.dataset_source_id]
        or run.output_artifacts
        or run.logs_path is not None
        or run.environment_hash is not None
    ):
        raise AnalysisServiceError(
            409,
            "Analysis execution provenance changed before completion",
            code="analysis-finalization-integrity-conflict",
        )
    return intent, project, dataset, task, run


def _assert_collected_artifact_binding(
    snapshot: _ExecutionSnapshot,
    item: CollectedArtifact,
) -> None:
    relative = Path(item.project_relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise AnalysisServiceError(
            409,
            "Analysis artifact path is unsafe",
            code="analysis-artifact-binding-invalid",
        )
    expected = Path(os.path.abspath(snapshot.project_path / relative))
    actual = Path(os.path.abspath(item.absolute_path))
    run_root = Path(os.path.abspath(snapshot.run_dir))
    try:
        run_relative = actual.relative_to(run_root)
    except ValueError as error:
        raise AnalysisServiceError(
            409,
            "Analysis artifact path does not match its run binding",
            code="analysis-artifact-binding-invalid",
        ) from error
    if not run_relative.parts or run_relative == Path(".") or expected != actual:
        raise AnalysisServiceError(
            409,
            "Analysis artifact path does not match its run binding",
            code="analysis-artifact-binding-invalid",
        )


def _persist_execution_failure(
    session_factory: SessionFactory,
    snapshot: _ExecutionSnapshot,
    error: Exception,
) -> None:
    safe_error = safe_execution_error(error)
    run_directory_trusted = snapshot.run_directory_identity is not None
    try:
        clear_run_outputs(
            snapshot.project_path,
            snapshot.run_dir,
            snapshot.run_directory_identity,
        )
    except (OSError, RuntimeServiceError):
        run_directory_trusted = False
    remove_exchange_run(
        snapshot.exchange_run_dir,
        snapshot.exchange_run_directory_identity,
    )
    error_message = f"AnalysisExecutionError: {safe_error.code}\n"
    error_log: tuple[str, str, int] | None = None
    if run_directory_trusted:
        try:
            error_log = write_run_error_log(
                snapshot.project_path,
                snapshot.run_dir,
                snapshot.run_directory_identity,
                error_message.encode("utf-8"),
            )
        except (OSError, RuntimeServiceError):
            error_log = None

    with session_factory() as session, session.begin():
        intent = session.get(AnalysisIntentRecord, snapshot.intent_id)
        task = session.get(TaskRecord, snapshot.task_id)
        run = session.get(RunRecord, snapshot.run_id)
        project = session.get(ProjectRecord, snapshot.project_id)
        session.execute(delete(ArtifactRecord).where(ArtifactRecord.run_id == snapshot.run_id))
        exact_run_binding = (
            run is not None
            and run.task_id == snapshot.task_id
            and run.analysis_intent_id == snapshot.intent_id
        )
        exact_intent_binding = (
            intent is not None
            and intent.task_id == snapshot.task_id
            and intent.project_id == snapshot.project_id
            and intent.workflow_id == snapshot.workflow_id
        )
        exact_task_binding = (
            task is not None
            and task.project_id == snapshot.project_id
            and task.workflow_id == snapshot.workflow_id
        )
        if error_log is not None and exact_run_binding:
            relative_path, error_hash, error_size = error_log
            artifact = ArtifactRecord(
                id=str(uuid.uuid4()),
                run_id=snapshot.run_id,
                artifact_type="log",
                path=relative_path,
                mime_type="text/plain",
                content_hash=error_hash,
                parent_artifacts=[snapshot.dataset_source_id],
                metadata_json={
                    "sizeBytes": error_size,
                    "payloadSha256": snapshot.payload_sha256,
                    "producer": "science-core",
                    "errorCode": safe_error.code,
                },
            )
            session.add(artifact)
            assert run is not None
            run.logs_path = relative_path
            run.output_artifacts = [relative_path]
        elif run is not None:
            run.logs_path = None
            run.output_artifacts = []
        if run is not None:
            run.environment_hash = None
            run.status = "failed"
            run.finished_at = utc_now()
        if intent is not None:
            intent.status = "failed"
            if snapshot.workflow_id is not None:
                intent.error_summary = safe_error_summary(safe_error)
        if task is not None and task.status != "cancelled":
            task.status = "failed"
        if (
            snapshot.workflow_id is None
            and project is not None
            and exact_run_binding
            and exact_intent_binding
            and exact_task_binding
        ):
            session.add(
                EventRecord(
                    id=str(uuid.uuid4()),
                    project_id=snapshot.project_id,
                    event_type="analysis.run.failed",
                    payload={
                        "analysisIntentId": snapshot.intent_id,
                        "runId": snapshot.run_id,
                        "payloadSha256": snapshot.payload_sha256,
                        "errorCode": safe_error.code,
                        "error": safe_error.user_message,
                    },
                )
            )
