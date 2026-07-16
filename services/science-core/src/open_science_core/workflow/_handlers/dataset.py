from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass, replace
from datetime import timezone
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..._analysis_service.filesystem import open_workspace_file_without_symlinks
from ...analysis import RuntimeServiceError
from ...analysis_service import (
    AnalysisServiceError,
    CompiledIntentProvenance,
    WorkflowIntentBundle,
    analysis_code_diff,
    analysis_run_out,
    create_workflow_analysis_intent,
    validate_workflow_analysis_intent,
)
from ...analysis_spec import (
    AnalysisReviewIdentity,
    AnalysisSpec,
    AnalysisSpecValidationError,
    AnalysisValidationContext,
    CompiledAnalysis,
    ExactCorrelationPreflight,
    ExactTwoGroupPreflight,
    FigureLineage,
    analysis_spec_sha256,
    compile_analysis_spec,
    review_analysis_spec_outputs,
    validate_analysis_spec,
)
from ...dataset_inspector import (
    DatasetInspectionError,
    dataset_profile_sha256,
    exact_correlation_preflight_csv_dataset,
    exact_two_group_preflight_csv_dataset,
    inspect_csv_dataset,
)
from ...fixed_analysis_policy import fixed_analysis_source
from ...models import (
    AnalysisIntentRecord,
    AnalysisSpecRecord,
    ApprovalRecord,
    ArtifactRecord,
    EventRecord,
    JobRecord,
    PlanRecord,
    ProjectRecord,
    ReviewRecord,
    RunRecord,
    SourceRecord,
    StructuredAnalysisResultRecord,
    TaskRecord,
    WorkflowRecord,
    utc_now,
)
from ...schemas import AnalysisRunOut
from .._service.jobs import analysis_execution_operation_key, job_input_compatibility
from ..schemas import (
    AnalysisApprovalEventData,
    AnalysisArtifactCreatedEventData,
    AnalysisIntentCreatedEventData,
    AnalysisRunEventData,
    AnalysisRunProgressEventData,
    AnalysisStructuredResultEventData,
    CollectArtifactsStepInput,
    DatasetAnalysisPlanSpec,
    DatasetAnalysisReviewCheck,
    DatasetAnalysisReviewIssue,
    DatasetAnalysisReviewResult,
    DatasetAnalysisRuntimeArtifactType,
    DatasetInspectionStepInput,
    DatasetProfile,
    ExecuteAnalysisStepInput,
    ReviewEventData,
    TaskEventData,
)
from ..service import append_workflow_events, transition_task, transition_workflow
from ..state import WorkflowFailure
from .lifecycle import advance_after_task, assert_current_task_contract, finish_job


class AnalysisServiceExecutor(Protocol):
    def __call__(
        self,
        intent_id: str,
        *,
        session_factory: Any,
        expected_workflow_id: str,
        approval_workflow_revision: int,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class PreparedAnalysis:
    outputs: dict[str, Any]
    execution_task: TaskRecord
    intent_bundle: WorkflowIntentBundle


MAX_ANALYSIS_REPAIR_ATTEMPTS = 2
_ANALYSIS_PROGRESS_STAGE_ORDER = {
    "preparing-input": 0,
    "executing-runtime": 1,
    "collecting-artifacts": 2,
}


def _analysis_elapsed_seconds(run: RunRecord) -> float:
    started_at = run.created_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return round(max(0.0, (utc_now() - started_at).total_seconds()), 3)


def _assert_analysis_run_event_lineage(
    event: EventRecord,
    *,
    workflow: WorkflowRecord,
    task: TaskRecord,
    job: JobRecord,
    intent: AnalysisIntentRecord,
    run: RunRecord,
) -> None:
    payload = event.payload
    if (
        event.project_id != workflow.project_id
        or event.workflow_id != workflow.id
        or event.task_id != task.id
        or event.job_id != job.id
        or payload.get("analysisIntentId") != intent.id
        or payload.get("runId") != run.id
        or payload.get("taskId") != task.id
        or payload.get("jobId") != job.id
    ):
        raise WorkflowFailure(
            "analysis-run-event-conflict",
            "An analysis run event has invalid workflow lineage.",
        )


def _analysis_start_event(
    session: Session,
    *,
    workflow: WorkflowRecord,
    task: TaskRecord,
    job: JobRecord,
    intent: AnalysisIntentRecord,
    run: RunRecord,
) -> EventRecord | None:
    started_events = list(
        session.scalars(
            select(EventRecord).where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.job_id == job.id,
                EventRecord.event_type == "analysis.run-started",
            )
        )
    )
    if len(started_events) > 1:
        raise WorkflowFailure(
            "analysis-run-event-conflict",
            "The analysis job has more than one durable start event.",
        )
    if not started_events:
        return None
    started = started_events[0]
    _assert_analysis_run_event_lineage(
        started,
        workflow=workflow,
        task=task,
        job=job,
        intent=intent,
        run=run,
    )
    if (
        started.payload.get("payloadSha256") != intent.payload_sha256
        or started.payload.get("environmentHash") is not None
        or started.payload.get("artifactCount") is not None
        or started.payload.get("errorCode") is not None
    ):
        raise WorkflowFailure(
            "analysis-run-event-conflict",
            "The analysis start event does not bind the approved immutable intent.",
        )
    return started


def _analysis_progress_events(
    session: Session,
    *,
    workflow: WorkflowRecord,
    task: TaskRecord,
    job: JobRecord,
    intent: AnalysisIntentRecord,
    run: RunRecord,
    started: EventRecord | None,
) -> dict[str, EventRecord]:
    progress_events = list(
        session.scalars(
            select(EventRecord)
            .where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.job_id == job.id,
                EventRecord.event_type == "analysis.run-progress",
            )
            .order_by(EventRecord.sequence)
        )
    )
    if progress_events and started is None:
        raise WorkflowFailure(
            "analysis-run-event-conflict",
            "Analysis progress cannot precede its durable start event.",
        )
    by_stage: dict[str, EventRecord] = {}
    previous_stage = -1
    previous_elapsed = -1.0
    for event in progress_events:
        _assert_analysis_run_event_lineage(
            event,
            workflow=workflow,
            task=task,
            job=job,
            intent=intent,
            run=run,
        )
        stage = event.payload.get("stage")
        elapsed = event.payload.get("elapsedSeconds")
        if (
            not isinstance(stage, str)
            or stage not in _ANALYSIS_PROGRESS_STAGE_ORDER
            or stage in by_stage
            or isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or elapsed < 0
            or _ANALYSIS_PROGRESS_STAGE_ORDER[stage] <= previous_stage
            or float(elapsed) < previous_elapsed
            or event.sequence is None
            or (started is not None and event.sequence <= (started.sequence or 0))
        ):
            raise WorkflowFailure(
                "analysis-run-event-conflict",
                "The durable analysis progress sequence is invalid.",
            )
        by_stage[stage] = event
        previous_stage = _ANALYSIS_PROGRESS_STAGE_ORDER[stage]
        previous_elapsed = float(elapsed)
    return by_stage


def _missing_analysis_progress_entries(
    session: Session,
    *,
    workflow: WorkflowRecord,
    task: TaskRecord,
    job: JobRecord,
    intent: AnalysisIntentRecord,
    run: RunRecord,
    started: EventRecord | None,
    include_collecting: bool,
) -> list[tuple[str, Any, str | None, str | None]]:
    progress = _analysis_progress_events(
        session,
        workflow=workflow,
        task=task,
        job=job,
        intent=intent,
        run=run,
        started=started,
    )
    if "collecting-artifacts" in progress and "executing-runtime" not in progress:
        raise WorkflowFailure(
            "analysis-run-event-conflict",
            "Artifact collection progress has no preceding runtime stage.",
        )
    stages: list[str] = []
    if "executing-runtime" not in progress:
        stages.append("executing-runtime")
    if include_collecting and "collecting-artifacts" not in progress:
        stages.append("collecting-artifacts")
    elapsed_seconds = max(
        _analysis_elapsed_seconds(run),
        max(
            (
                float(event.payload["elapsedSeconds"])
                for event in progress.values()
            ),
            default=0.0,
        ),
    )
    return [
        (
            "analysis.run-progress",
            AnalysisRunProgressEventData(
                analysis_intent_id=intent.id,
                run_id=run.id,
                task_id=task.id,
                job_id=job.id,
                stage=cast(Any, stage),
                elapsed_seconds=elapsed_seconds,
            ),
            task.id,
            job.id,
        )
        for stage in stages
    ]


def execute_leased_analysis_job(
    session_factory: Any,
    job_id: str,
    lease_token: str,
    analysis_executor: AnalysisServiceExecutor,
) -> None:
    intent_id, workflow_id, workflow_revision = _analysis_execution_preflight(
        session_factory,
        job_id,
        lease_token,
    )
    try:
        result = asyncio.run(
            _execute_with_cancellation(
                session_factory,
                job_id=job_id,
                lease_token=lease_token,
                workflow_id=workflow_id,
                workflow_revision=workflow_revision,
                intent_id=intent_id,
                analysis_executor=analysis_executor,
            )
        )
    except AnalysisServiceError as error:
        if _publish_analysis_failure_or_repair(
            session_factory,
            job_id=job_id,
            lease_token=lease_token,
            intent_id=intent_id,
            fallback_error_code=error.code,
        ):
            return
        raise WorkflowFailure(error.code, error.detail) from None
    if result.status == "failed":
        if _publish_analysis_failure_or_repair(
            session_factory,
            job_id=job_id,
            lease_token=lease_token,
            intent_id=intent_id,
            expected_run_id=result.id,
            fallback_error_code="analysis-runtime-failed",
        ):
            return
        raise WorkflowFailure(
            "analysis-failure-binding-invalid",
            "The failed analysis result could not be bound to its workflow job.",
        )
    _publish_analysis_result(
        session_factory,
        job_id=job_id,
        lease_token=lease_token,
        intent_id=intent_id,
        result=result,
    )


async def _execute_with_cancellation(
    session_factory: Any,
    *,
    job_id: str,
    lease_token: str,
    workflow_id: str,
    workflow_revision: int,
    intent_id: str,
    analysis_executor: AnalysisServiceExecutor,
) -> AnalysisRunOut:
    execution = asyncio.create_task(
        analysis_executor(
            intent_id,
            session_factory=session_factory,
            expected_workflow_id=workflow_id,
            approval_workflow_revision=workflow_revision,
        )
    )
    while True:
        done, _pending = await asyncio.wait({execution}, timeout=0.25)
        await asyncio.to_thread(
            _publish_analysis_started_if_claimed,
            session_factory,
            job_id=job_id,
            lease_token=lease_token,
            intent_id=intent_id,
        )
        if done:
            return await execution
        if _workflow_cancel_requested(session_factory, workflow_id):
            execution.cancel()
            try:
                await execution
            except asyncio.CancelledError:
                pass
            raise WorkflowFailure(
                "workflow-cancelled-during-analysis",
                "The workflow was cancelled while analysis was running.",
            )


def _analysis_execution_preflight(
    session_factory: Any,
    job_id: str,
    lease_token: str,
) -> tuple[str, str, int]:
    with session_factory() as session:
        job = session.get(JobRecord, job_id)
        if job is None or job.status != "leased" or job.lease_token != lease_token:
            raise WorkflowFailure(
                "job-lease-lost",
                "The background job lease is no longer valid.",
                retryable=True,
            )
        workflow = session.get(WorkflowRecord, job.workflow_id)
        task = session.get(TaskRecord, job.task_id) if job.task_id is not None else None
        if (
            workflow is None
            or task is None
            or workflow.workflow_type != "dataset-analysis"
            or workflow.status != "running"
            or workflow.cancel_requested_at is not None
            or job.kind != "execute-task"
            or task.task_type != "python-data-analysis"
            or task.step_key != "execute-analysis"
            or task.status != "running"
        ):
            raise WorkflowFailure(
                "analysis-job-binding-invalid",
                "The analysis job no longer matches the running dataset workflow.",
            )
        assert_current_task_contract(session, workflow, task)
        if job_input_compatibility(session, workflow, job, task) is None:
            raise WorkflowFailure(
                "job-input-changed",
                "The workflow input changed after this job was queued.",
            )
        intents = list(
            session.scalars(
                select(AnalysisIntentRecord).where(
                    AnalysisIntentRecord.task_id == task.id,
                    AnalysisIntentRecord.workflow_id == workflow.id,
                    AnalysisIntentRecord.status == "approved",
                    AnalysisIntentRecord.decision == "approved",
                )
            )
        )
        if len(intents) != 1:
            raise WorkflowFailure(
                "analysis-approval-invalid",
                "The analysis step has no unique approved immutable intent.",
            )
        intent = intents[0]
        if job.operation_key != analysis_execution_operation_key(workflow.id, intent.id):
            raise WorkflowFailure(
                "analysis-job-binding-invalid",
                "The analysis job identity does not bind the approved immutable intent.",
            )
        try:
            validate_workflow_analysis_intent(
                session,
                intent,
                expected_workflow_id=workflow.id,
                expected_workflow_revision=workflow.row_version,
                require_approval=True,
                require_current_revision=True,
            )
        except AnalysisServiceError as error:
            raise WorkflowFailure(error.code, error.detail) from None
        return intent.id, workflow.id, workflow.row_version


def _workflow_cancel_requested(session_factory: Any, workflow_id: str) -> bool:
    with session_factory() as session:
        workflow = session.get(WorkflowRecord, workflow_id)
        return workflow is None or workflow.cancel_requested_at is not None


def _publish_analysis_started_if_claimed(
    session_factory: Any,
    *,
    job_id: str,
    lease_token: str,
    intent_id: str,
) -> str | None:
    with session_factory() as session:
        job = session.get(JobRecord, job_id)
        intent = session.get(AnalysisIntentRecord, intent_id)
        if job is None or job.status != "leased" or job.lease_token != lease_token:
            raise WorkflowFailure(
                "job-lease-lost",
                "The background job lease is no longer valid.",
                retryable=True,
            )
        workflow = session.get(WorkflowRecord, job.workflow_id)
        task = session.get(TaskRecord, job.task_id) if job.task_id is not None else None
        if (
            workflow is None
            or task is None
            or intent is None
            or workflow.status != "running"
            or workflow.cancel_requested_at is not None
            or intent.workflow_id != workflow.id
            or intent.task_id != task.id
            or job.operation_key
            != analysis_execution_operation_key(workflow.id, intent.id)
        ):
            raise WorkflowFailure(
                "analysis-job-binding-invalid",
                "The claimed analysis run no longer matches its workflow job.",
            )
        run = session.scalar(
            select(RunRecord).where(RunRecord.analysis_intent_id == intent.id)
        )
        if run is None:
            return None
        if (
            run.task_id != task.id
            or run.input_artifacts != [intent.dataset_source_id]
            or run.status not in {"running", "completed", "failed"}
        ):
            raise WorkflowFailure(
                "analysis-run-lineage-invalid",
                "The claimed analysis run has invalid workflow lineage.",
            )
        started = _analysis_start_event(
            session,
            workflow=workflow,
            task=task,
            job=job,
            intent=intent,
            run=run,
        )
        entries: list[tuple[str, Any, str | None, str | None]] = []
        if started is None:
            started_payload = AnalysisRunEventData(
                analysis_intent_id=intent.id,
                run_id=run.id,
                task_id=task.id,
                job_id=job.id,
                payload_sha256=intent.payload_sha256,
            )
            entries.append(
                ("analysis.run-started", started_payload, task.id, job.id)
            )
            if intent.analysis_spec_id is not None:
                entries.append(
                    ("analysis.execution-started", started_payload, task.id, job.id)
                )
        entries.extend(
            _missing_analysis_progress_entries(
                session,
                workflow=workflow,
                task=task,
                job=job,
                intent=intent,
                run=run,
                started=started,
                include_collecting=False,
            )
        )
        append_workflow_events(session, workflow, entries)
        if entries:
            session.commit()
        return run.id


def recover_leased_analysis_job(
    session_factory: Any,
    job_id: str,
    lease_token: str,
) -> bool:
    with session_factory() as session:
        job = session.get(JobRecord, job_id)
        if job is None or job.status != "leased" or job.lease_token != lease_token:
            return False
        workflow = session.get(WorkflowRecord, job.workflow_id)
        task = session.get(TaskRecord, job.task_id) if job.task_id is not None else None
        prefix = (
            f"workflow:{workflow.id}:analysis-intent:"
            if workflow is not None
            else ""
        )
        if (
            workflow is None
            or task is None
            or workflow.workflow_type != "dataset-analysis"
            or task.task_type != "python-data-analysis"
            or not job.operation_key.startswith(prefix)
        ):
            return False
        intent_id = job.operation_key.removeprefix(prefix)
        if not intent_id or ":" in intent_id:
            return False
        intent = session.get(AnalysisIntentRecord, intent_id)
        run = session.scalar(
            select(RunRecord).where(RunRecord.analysis_intent_id == intent_id)
        )
        if intent is None or run is None:
            return False
        run_status = run.status
        project = session.get(ProjectRecord, workflow.project_id)
        if project is None:
            return False
        try:
            result = analysis_run_out(session, run, intent, project)
        except AnalysisServiceError:
            return False
    _publish_analysis_started_if_claimed(
        session_factory,
        job_id=job_id,
        lease_token=lease_token,
        intent_id=intent_id,
    )
    if run_status == "completed":
        _publish_analysis_result(
            session_factory,
            job_id=job_id,
            lease_token=lease_token,
            intent_id=intent_id,
            result=result,
        )
        return True
    if run_status == "failed":
        return _publish_analysis_failure_or_repair(
            session_factory,
            job_id=job_id,
            lease_token=lease_token,
            intent_id=intent_id,
            expected_run_id=result.id,
            fallback_error_code="analysis-interrupted",
        )
    return False


def _publish_analysis_failure_or_repair(
    session_factory: Any,
    *,
    job_id: str,
    lease_token: str,
    intent_id: str,
    fallback_error_code: str,
    expected_run_id: str | None = None,
) -> bool:
    with session_factory() as session:
        job = session.get(JobRecord, job_id)
        intent = session.get(AnalysisIntentRecord, intent_id)
        if job is None or job.status != "leased" or job.lease_token != lease_token:
            raise WorkflowFailure(
                "job-lease-lost",
                "The background job lease is no longer valid.",
                retryable=True,
            )
        workflow = session.get(WorkflowRecord, job.workflow_id)
        task = session.get(TaskRecord, job.task_id) if job.task_id is not None else None
        project = (
            session.get(ProjectRecord, workflow.project_id)
            if workflow is not None
            else None
        )
        run = (
            session.scalar(
                select(RunRecord).where(RunRecord.analysis_intent_id == intent.id)
            )
            if intent is not None
            else None
        )
        if run is None:
            return False
        if (
            workflow is None
            or task is None
            or project is None
            or intent is None
            or workflow.status != "running"
            or workflow.cancel_requested_at is not None
            or task.status != "failed"
            or intent.status != "failed"
            or intent.decision != "approved"
            or intent.workflow_id != workflow.id
            or intent.task_id != task.id
            or run.task_id != task.id
            or run.status != "failed"
            or (expected_run_id is not None and run.id != expected_run_id)
            or job.operation_key
            != analysis_execution_operation_key(workflow.id, intent.id)
        ):
            return False
        assert_current_task_contract(session, workflow, task)
        try:
            analysis_run_out(session, run, intent, project)
        except AnalysisServiceError as error:
            raise WorkflowFailure(error.code, error.detail) from None
        raw_error_summary: object = intent.error_summary
        if not isinstance(raw_error_summary, dict):
            raise WorkflowFailure(
                "analysis-error-summary-missing",
                "The failed analysis has no structured safe error summary.",
            )
        error_summary = cast(dict[str, Any], raw_error_summary)
        raw_code = error_summary.get("code")
        raw_message = error_summary.get("userMessage")
        failure_code = raw_code if isinstance(raw_code, str) and raw_code else fallback_error_code
        failure_message = (
            raw_message
            if isinstance(raw_message, str) and raw_message
            else "The approved analysis failed in the restricted runtime."
        )
        artifacts = list(
            session.scalars(
                select(ArtifactRecord)
                .where(ArtifactRecord.run_id == run.id)
                .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
            )
        )
        started = _analysis_start_event(
            session,
            workflow=workflow,
            task=task,
            job=job,
            intent=intent,
            run=run,
        )
        failed = session.scalar(
            select(EventRecord).where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.job_id == job.id,
                EventRecord.event_type == "analysis.run-failed",
            )
        )
        if failed is not None:
            raise WorkflowFailure(
                "analysis-run-event-conflict",
                "The failed analysis job was already published without settlement.",
            )
        events: list[tuple[str, Any, str | None, str | None]] = []
        if started is None:
            started_payload = AnalysisRunEventData(
                analysis_intent_id=intent.id,
                run_id=run.id,
                task_id=task.id,
                job_id=job.id,
                payload_sha256=intent.payload_sha256,
            )
            events.append(
                ("analysis.run-started", started_payload, task.id, job.id)
            )
            if intent.analysis_spec_id is not None:
                events.append(
                    ("analysis.execution-started", started_payload, task.id, job.id)
                )
        events.extend(
            _missing_analysis_progress_entries(
                session,
                workflow=workflow,
                task=task,
                job=job,
                intent=intent,
                run=run,
                started=started,
                include_collecting=True,
            )
        )
        events.extend(
            [
                (
                    "analysis.run-failed",
                    AnalysisRunEventData(
                        analysis_intent_id=intent.id,
                        run_id=run.id,
                        task_id=task.id,
                        job_id=job.id,
                        payload_sha256=intent.payload_sha256,
                        environment_hash=run.environment_hash,
                        artifact_count=len(artifacts),
                        error_code=failure_code,
                    ),
                    task.id,
                    job.id,
                ),
                *[
                    (
                        "artifact.created",
                        AnalysisArtifactCreatedEventData(
                            analysis_intent_id=intent.id,
                            run_id=run.id,
                            task_id=task.id,
                            job_id=job.id,
                            artifact_id=artifact.id,
                            artifact_type=cast(
                                DatasetAnalysisRuntimeArtifactType,
                                artifact.artifact_type,
                            ),
                            content_hash=artifact.content_hash,
                            path=artifact.path,
                        ),
                        task.id,
                        job.id,
                    )
                    for artifact in artifacts
                ],
                (
                    "step.failed",
                    TaskEventData(
                        task_id=task.id,
                        step_key=task.step_key or "",
                        order_index=task.order_index or 0,
                        status="failed",
                        error_code=failure_code,
                    ),
                    task.id,
                    job.id,
                ),
            ]
        )
        repair_attempt = intent.repair_attempt or 0
        repair_allowed = intent.analysis_spec_id is None and error_summary.get(
            "code"
        ) in {
            "analysis-runtime-failed",
            "runtime-timeout",
        }
        if repair_allowed and repair_attempt < MAX_ANALYSIS_REPAIR_ATTEMPTS:
            inspect_task = session.scalar(
                select(TaskRecord).where(
                    TaskRecord.workflow_id == workflow.id,
                    TaskRecord.plan_id == task.plan_id,
                    TaskRecord.step_key == "inspect-dataset",
                )
            )
            if inspect_task is None:
                raise WorkflowFailure(
                    "dataset-profile-missing",
                    "The approved dataset profile is missing for analysis repair.",
                )
            profile = _validated_profile(inspect_task, workflow)
            next_attempt = repair_attempt + 1
            proposed_code = deterministic_repair_analysis_code(profile, next_attempt)
            code_diff = analysis_code_diff(intent.code, proposed_code)
            if not code_diff:
                raise WorkflowFailure(
                    "analysis-repair-diff-empty",
                    "The proposed analysis repair did not change the approved code.",
                )
            try:
                bundle = create_workflow_analysis_intent(
                    session,
                    expected_workflow_id=workflow.id,
                    task_id=task.id,
                    code=proposed_code,
                    expected_outputs=intent.expected_outputs or [],
                    expected_workflow_revision=workflow.row_version,
                    previous_intent_id=intent.id,
                    error_summary=error_summary,
                    code_diff=code_diff,
                )
            except AnalysisServiceError as error:
                raise WorkflowFailure(error.code, error.detail) from None
            transition_task(session, task, "waiting-approval")
            task.retries = next_attempt
            finish_job(session, job, "failed", failure_code, failure_message)
            events.extend(
                [
                    (
                        "analysis.intent-created",
                        AnalysisIntentCreatedEventData(
                            analysis_intent_id=bundle.intent.id,
                            task_id=task.id,
                            job_id=job.id,
                            plan_step_id="execute-analysis",
                            dataset_source_id=bundle.intent.dataset_source_id,
                            dataset_content_hash=bundle.intent.dataset_content_hash or "",
                            payload_sha256=bundle.intent.payload_sha256,
                            repair_attempt=cast(Any, next_attempt),
                        ),
                        task.id,
                        job.id,
                    ),
                    (
                        "analysis.approval-requested",
                        AnalysisApprovalEventData(
                            approval_id=bundle.approval.id,
                            analysis_intent_id=bundle.intent.id,
                            task_id=task.id,
                            job_id=job.id,
                            payload_sha256=bundle.intent.payload_sha256,
                            approval_schema_version=cast(
                                Literal[
                                    "analysis-intent-v2",
                                    "analysis-intent-v3",
                                    "analysis-intent-v4",
                                ],
                                bundle.approval.payload_schema_version,
                            ),
                            expected_workflow_revision=bundle.expected_workflow_revision,
                        ),
                        task.id,
                        job.id,
                    ),
                ]
            )
            append_workflow_events(session, workflow, events)
            session.commit()
            return True

        blocking_code = (
            "analysis-compiled-execution-failed"
            if intent.analysis_spec_id is not None
            else
            "analysis-repair-limit-exceeded"
            if repair_attempt >= MAX_ANALYSIS_REPAIR_ATTEMPTS
            else "analysis-repair-not-safe"
        )
        blocking_message = (
            "The approved compiled analysis failed. Its immutable AnalysisSpec and code "
            "cannot be repaired automatically; review the recorded run before retrying."
            if intent.analysis_spec_id is not None
            else
            "Analysis failed after two approved repair attempts. Review the recorded runs "
            "before retrying."
            if repair_attempt >= MAX_ANALYSIS_REPAIR_ATTEMPTS
            else "Analysis failed with an integrity condition that cannot be repaired "
            "automatically. Review the recorded run before retrying."
        )
        finish_job(session, job, "failed", failure_code, failure_message)
        append_workflow_events(session, workflow, events)
        transition_workflow(
            session,
            workflow,
            "blocked",
            reason_code=blocking_code,
            blocking_message=blocking_message,
        )
        session.commit()
        return True


def _publish_analysis_result(
    session_factory: Any,
    *,
    job_id: str,
    lease_token: str,
    intent_id: str,
    result: AnalysisRunOut,
) -> None:
    with session_factory() as session:
        job = session.get(JobRecord, job_id)
        if job is None or job.status != "leased" or job.lease_token != lease_token:
            raise WorkflowFailure(
                "job-lease-lost",
                "The background job lease is no longer valid.",
                retryable=True,
            )
        workflow = session.get(WorkflowRecord, job.workflow_id)
        task = session.get(TaskRecord, job.task_id) if job.task_id is not None else None
        intent = session.get(AnalysisIntentRecord, intent_id)
        run = session.get(RunRecord, result.id)
        if (
            workflow is None
            or task is None
            or intent is None
            or run is None
            or workflow.status != "running"
            or workflow.cancel_requested_at is not None
            or task.status != result.status
            or intent.workflow_id != workflow.id
            or intent.task_id != task.id
            or result.intent_id != intent.id
            or result.task_id != task.id
            or run.analysis_intent_id != intent.id
            or run.task_id != task.id
            or run.status != "completed"
            or result.payload_sha256 != intent.payload_sha256
            or job.operation_key
            != analysis_execution_operation_key(workflow.id, intent.id)
        ):
            raise WorkflowFailure(
                "analysis-result-binding-invalid",
                "The analysis result no longer matches its leased workflow job.",
            )
        if result.status != "completed":
            raise WorkflowFailure(
                "analysis-run-failed",
                "The approved analysis did not complete successfully.",
            )
        artifacts = list(
            session.scalars(
                select(ArtifactRecord)
                .where(ArtifactRecord.run_id == run.id)
                .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
            )
        )
        if {artifact.id for artifact in artifacts} != {
            artifact.id for artifact in result.artifacts
        }:
            raise WorkflowFailure(
                "analysis-result-binding-invalid",
                "The analysis artifact set changed before workflow publication.",
            )
        started = _analysis_start_event(
            session,
            workflow=workflow,
            task=task,
            job=job,
            intent=intent,
            run=run,
        )
        if started is None:
            raise WorkflowFailure(
                "analysis-run-start-event-missing",
                "The completed analysis has no durable start event.",
            )
        structured_result = session.scalar(
            select(StructuredAnalysisResultRecord).where(
                StructuredAnalysisResultRecord.run_id == run.id
            )
        )
        if intent.analysis_spec_id is not None and structured_result is None:
            raise WorkflowFailure(
                "analysis-structured-result-missing",
                "The completed compiled analysis has no structured result record.",
            )
        task.outputs = {
            "analysisIntentId": intent.id,
            "analysisPayloadSha256": intent.payload_sha256,
            "runId": run.id,
            "artifactIds": [artifact.id for artifact in artifacts],
        }
        finish_job(session, job, "succeeded")
        append_workflow_events(
            session,
            workflow,
            [
                *_missing_analysis_progress_entries(
                    session,
                    workflow=workflow,
                    task=task,
                    job=job,
                    intent=intent,
                    run=run,
                    started=started,
                    include_collecting=True,
                ),
                (
                    "analysis.run-completed",
                    AnalysisRunEventData(
                        analysis_intent_id=intent.id,
                        run_id=run.id,
                        task_id=task.id,
                        job_id=job.id,
                        payload_sha256=intent.payload_sha256,
                        environment_hash=run.environment_hash,
                        artifact_count=len(artifacts),
                    ),
                    task.id,
                    job.id,
                ),
                *(
                    [
                        (
                            "analysis.structured-result-created",
                            AnalysisStructuredResultEventData(
                                structured_result_id=structured_result.id,
                                analysis_spec_id=structured_result.analysis_spec_id,
                                analysis_intent_id=structured_result.analysis_intent_id,
                                run_id=structured_result.run_id,
                                result_sha256=structured_result.result_sha256,
                            ),
                            task.id,
                            job.id,
                        )
                    ]
                    if structured_result is not None
                    else []
                ),
                *[
                    (
                        "artifact.created",
                        AnalysisArtifactCreatedEventData(
                            analysis_intent_id=intent.id,
                            run_id=run.id,
                            task_id=task.id,
                            job_id=job.id,
                            artifact_id=artifact.id,
                            artifact_type=cast(
                                DatasetAnalysisRuntimeArtifactType,
                                artifact.artifact_type,
                            ),
                            content_hash=artifact.content_hash,
                            path=artifact.path,
                        ),
                        task.id,
                        job.id,
                    )
                    for artifact in artifacts
                ],
                (
                    "step.completed",
                    TaskEventData(
                        task_id=task.id,
                        step_key=task.step_key or "",
                        order_index=task.order_index or 0,
                        status="completed",
                        output_count=len(artifacts),
                    ),
                    task.id,
                    job.id,
                ),
            ],
        )
        advance_after_task(
            session,
            workflow,
            task,
            preserve_legacy_review=False,
        )
        session.commit()


def handle_dataset_inspection(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord,
) -> dict[str, Any]:
    inputs = _inspection_inputs(task)
    project, dataset = _dataset_records(session, workflow, inputs.dataset_source_id)
    if inputs.dataset_content_hash != dataset.content_hash:
        raise WorkflowFailure(
            "dataset-identity-changed",
            "The dataset no longer matches the immutable workflow identity.",
        )
    try:
        result = inspect_csv_dataset(
            workspace_root=Path(project.project_path),
            dataset_path=Path(dataset.local_path),
            source_id=dataset.id,
            expected_content_hash=dataset.content_hash,
            max_sample_rows=inputs.max_sample_rows,
        )
    except DatasetInspectionError:
        raise WorkflowFailure(
            "dataset-inspection-failed",
            "The dataset could not be inspected without violating its integrity checks.",
        ) from None
    return {
        "datasetProfile": result.profile.model_dump(mode="json", by_alias=True),
        "datasetProfileSha256": result.profile_sha256,
    }


def handle_prepare_analysis(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord,
) -> PreparedAnalysis:
    previous = _adjacent_task(session, task, offset=-1)
    execution_task = _adjacent_task(session, task, offset=1)
    if (
        previous.step_key != "inspect-dataset"
        or previous.task_type != "dataset-inspection"
        or previous.status != "completed"
        or execution_task.step_key != "execute-analysis"
        or execution_task.task_type != "python-data-analysis"
        or execution_task.status != "pending"
    ):
        raise WorkflowFailure(
            "dataset-step-order-invalid",
            "The dataset workflow steps are not in the required sequential state.",
        )
    profile = _validated_profile(previous, workflow)
    try:
        execution_inputs = ExecuteAnalysisStepInput.model_validate(execution_task.inputs)
    except ValidationError:
        raise WorkflowFailure(
            "analysis-task-input-invalid",
            "The approved analysis execution inputs are invalid.",
        ) from None
    compiled_bundle = _compiled_analysis_for_plan(
        session,
        workflow,
        execution_task,
        profile,
    )
    code = (
        compiled_bundle[0].code
        if compiled_bundle is not None
        else deterministic_analysis_code(profile)
    )
    try:
        bundle = create_workflow_analysis_intent(
            session,
            expected_workflow_id=workflow.id,
            task_id=execution_task.id,
            code=code,
            expected_outputs=execution_inputs.expected_outputs,
            expected_workflow_revision=workflow.row_version,
            compiled_provenance=(
                compiled_bundle[1] if compiled_bundle is not None else None
            ),
        )
    except AnalysisServiceError as error:
        raise WorkflowFailure(error.code, error.detail) from None
    return PreparedAnalysis(
        outputs={
            "analysisIntentId": bundle.intent.id,
            "analysisPayloadSha256": bundle.intent.payload_sha256,
            "datasetProfileSha256": dataset_profile_sha256(profile),
            **(
                {
                    "analysisSpecId": compiled_bundle[1].analysis_spec_id,
                    "analysisSpecSha256": compiled_bundle[1].analysis_spec_sha256,
                    "compilerVersion": compiled_bundle[1].compiler_version,
                    "codeSha256": compiled_bundle[1].code_sha256,
                    "runtimePolicyId": compiled_bundle[1].runtime_policy_id,
                }
                if compiled_bundle is not None
                else {}
            ),
        },
        execution_task=execution_task,
        intent_bundle=bundle,
    )


def _compiled_analysis_for_plan(
    session: Session,
    workflow: WorkflowRecord,
    execution_task: TaskRecord,
    profile: DatasetProfile,
) -> tuple[CompiledAnalysis, CompiledIntentProvenance] | None:
    plan = (
        session.get(PlanRecord, execution_task.plan_id)
        if execution_task.plan_id is not None
        else None
    )
    if plan is None or plan.workflow_id != workflow.id or plan.status != "approved":
        raise WorkflowFailure(
            "analysis-plan-binding-invalid",
            "Analysis preparation requires the approved dataset plan.",
        )
    try:
        plan_spec = DatasetAnalysisPlanSpec.model_validate(plan.spec_json)
    except ValidationError:
        raise WorkflowFailure(
            "analysis-plan-binding-invalid",
            "The approved dataset plan no longer matches its strict schema.",
        ) from None
    if plan_spec.analysis_spec_id is None:
        return None
    if plan_spec.analysis_spec_sha256 is None:
        raise WorkflowFailure(
            "analysis-spec-binding-invalid",
            "The approved plan has incomplete AnalysisSpec identity.",
        )
    record = session.get(AnalysisSpecRecord, plan_spec.analysis_spec_id)
    if (
        record is None
        or record.workflow_id != workflow.id
        or record.status != "approved"
        or record.spec_sha256 != plan_spec.analysis_spec_sha256
        or record.dataset_source_id != workflow.dataset_source_id
        or record.dataset_content_hash != workflow.dataset_content_hash
        or record.dataset_profile_sha256 != dataset_profile_sha256(profile)
    ):
        raise WorkflowFailure(
            "analysis-spec-binding-invalid",
            "The approved AnalysisSpec no longer matches the workflow, plan, or profile.",
        )
    try:
        # SQLAlchemy JSON values are Python containers. Validate through JSON so
        # strict tuple fields keep their wire-format semantics after persistence.
        spec = AnalysisSpec.model_validate_json(
            json.dumps(record.spec_json, allow_nan=False, ensure_ascii=False)
        )
    except ValidationError:
        raise WorkflowFailure(
            "analysis-spec-binding-invalid",
            "The approved AnalysisSpec no longer matches schema version 1.",
        ) from None
    if analysis_spec_sha256(spec) != record.spec_sha256:
        raise WorkflowFailure(
            "analysis-spec-binding-invalid",
            "The approved AnalysisSpec content hash is invalid.",
        )
    project, dataset = _dataset_records(session, workflow, record.dataset_source_id)
    context = AnalysisValidationContext(
        project_id=workflow.project_id,
        source_project_id=dataset.project_id,
        source_kind=dataset.source_kind,
        source_status=dataset.ingestion_status,
        source_id=dataset.id,
        source_content_hash=dataset.content_hash,
        profile=profile,
        profile_sha256=record.dataset_profile_sha256,
    )
    try:
        preliminary = validate_analysis_spec(spec, context)
        operation = preliminary.spec.operation
        if operation.type == "two-group-comparison":
            exact = exact_two_group_preflight_csv_dataset(
                workspace_root=Path(project.project_path),
                dataset_path=Path(dataset.local_path),
                expected_content_hash=dataset.content_hash,
                outcome_column=operation.outcome_column,
                group_column=operation.group_column,
                groups=operation.groups,
            )
            context = replace(
                context,
                two_group_preflight=ExactTwoGroupPreflight(
                    outcome_column=exact.outcome_column,
                    group_column=exact.group_column,
                    valid_counts=exact.valid_counts,
                    non_constant_groups=exact.non_constant_groups,
                ),
            )
        elif operation.type == "correlation":
            exact = exact_correlation_preflight_csv_dataset(
                workspace_root=Path(project.project_path),
                dataset_path=Path(dataset.local_path),
                expected_content_hash=dataset.content_hash,
                x_column=operation.x_column,
                y_column=operation.y_column,
            )
            context = replace(
                context,
                correlation_preflight=ExactCorrelationPreflight(
                    x_column=exact.x_column,
                    y_column=exact.y_column,
                    valid_pair_count=exact.valid_pair_count,
                ),
            )
        validated = validate_analysis_spec(preliminary.spec, context)
    except (AnalysisSpecValidationError, DatasetInspectionError) as error:
        code = getattr(error, "code", "analysis-preflight-failed")
        message = getattr(
            error,
            "message",
            "The approved analysis columns could not be inspected safely.",
        )
        raise WorkflowFailure(code, message) from None
    if analysis_spec_sha256(validated.spec) != record.spec_sha256:
        raise WorkflowFailure(
            "analysis-spec-normalization-changed",
            "The approved AnalysisSpec no longer matches deterministic normalization.",
        )
    compiled = compile_analysis_spec(validated)
    expected_semantic_outputs = (
        (
            "executed-notebook",
            "summary-table",
            "figures",
            "analysis-log",
            "environment-manifest",
        )
        if operation.plot != "none"
        else (
            "executed-notebook",
            "summary-table",
            "analysis-log",
            "environment-manifest",
        )
    )
    try:
        execution_inputs = ExecuteAnalysisStepInput.model_validate(execution_task.inputs)
    except ValidationError:
        raise WorkflowFailure(
            "analysis-task-input-invalid",
            "The approved analysis execution inputs are invalid.",
        ) from None
    if execution_inputs.expected_outputs != expected_semantic_outputs:
        raise WorkflowFailure(
            "analysis-output-contract-invalid",
            "The approved plan outputs do not match the compiled AnalysisSpec.",
        )
    return compiled, CompiledIntentProvenance(
        analysis_spec_id=record.id,
        analysis_spec_sha256=record.spec_sha256,
        dataset_profile_sha256=record.dataset_profile_sha256,
        compiler_version=compiled.compiler_version,
        code_sha256=compiled.code_sha256,
        runtime_policy_id=cast(Any, compiled.runtime_policy_id),
    )


def handle_collect_artifacts(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord,
) -> dict[str, Any]:
    execution_task = _adjacent_task(session, task, offset=-1)
    if (
        execution_task.step_key != "execute-analysis"
        or execution_task.task_type != "python-data-analysis"
        or execution_task.status != "completed"
    ):
        raise WorkflowFailure(
            "dataset-step-order-invalid",
            "Artifact collection requires the completed adjacent analysis step.",
        )
    try:
        inputs = CollectArtifactsStepInput.model_validate(task.inputs)
    except ValidationError:
        raise WorkflowFailure(
            "artifact-collection-input-invalid",
            "The approved artifact collection inputs are invalid.",
        ) from None
    intent_id = execution_task.outputs.get("analysisIntentId")
    run_id = execution_task.outputs.get("runId")
    if not isinstance(intent_id, str) or not isinstance(run_id, str):
        raise WorkflowFailure(
            "analysis-result-binding-invalid",
            "The completed analysis step has no exact intent and run binding.",
        )
    intent = session.get(AnalysisIntentRecord, intent_id)
    run = session.get(RunRecord, run_id)
    project = session.get(ProjectRecord, workflow.project_id)
    if (
        intent is None
        or run is None
        or project is None
        or intent.workflow_id != workflow.id
        or intent.task_id != execution_task.id
        or intent.objective != workflow.goal
        or run.analysis_intent_id != intent.id
        or run.task_id != execution_task.id
        or intent.status != "completed"
        or run.status != "completed"
        or list(inputs.expected_outputs) != intent.expected_outputs
    ):
        raise WorkflowFailure(
            "analysis-result-binding-invalid",
            "The collected run no longer matches the approved analysis intent.",
        )
    try:
        output = analysis_run_out(session, run, intent, project)
    except AnalysisServiceError as error:
        raise WorkflowFailure(error.code, error.detail) from None
    artifacts = list(
        session.scalars(
            select(ArtifactRecord)
            .where(ArtifactRecord.run_id == run.id)
            .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
        )
    )
    if {artifact.id for artifact in artifacts} != {
        artifact.id for artifact in output.artifacts
    }:
        raise WorkflowFailure(
            "analysis-artifact-binding-invalid",
            "The analysis artifact records changed during collection.",
        )
    _assert_expected_analysis_artifacts(intent, run, artifacts)
    evidence = [
        _verified_artifact_evidence(project, run, intent, artifact)
        for artifact in artifacts
    ]
    return {
        "analysisIntentId": intent.id,
        "analysisPayloadSha256": intent.payload_sha256,
        "runId": run.id,
        "environmentHash": run.environment_hash,
        "artifactIds": [artifact.id for artifact in artifacts],
        "artifactEvidence": evidence,
    }


def _compiled_dataset_review_result(
    *,
    session: Session,
    project: ProjectRecord,
    dataset: SourceRecord,
    intent: AnalysisIntentRecord,
    run: RunRecord,
) -> DatasetAnalysisReviewResult:
    artifacts = list(
        session.scalars(
            select(ArtifactRecord)
            .where(ArtifactRecord.run_id == run.id)
            .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
        )
    )
    run_prefix = f"runs/{run.id}/"
    by_name = {
        artifact.path.removeprefix(run_prefix): artifact
        for artifact in artifacts
        if artifact.path.startswith(run_prefix)
        and "/" not in artifact.path.removeprefix(run_prefix)
    }
    spec_artifact = by_name.get("analysis-spec.json")
    result_artifact = by_name.get("results.json")
    summary_artifact = by_name.get("summary.csv")
    notebook_artifact = by_name.get("executed.ipynb")
    figure_artifact = by_name.get("figure.png")
    spec_bytes = _review_artifact_bytes(project, run, intent, spec_artifact)
    result_bytes = _review_artifact_bytes(project, run, intent, result_artifact)
    summary_bytes = _review_artifact_bytes(project, run, intent, summary_artifact)
    notebook_bytes = _review_artifact_bytes(project, run, intent, notebook_artifact)

    approval = session.scalar(
        select(ApprovalRecord).where(
            ApprovalRecord.task_id == intent.task_id,
            ApprovalRecord.workflow_id == intent.workflow_id,
            ApprovalRecord.subject_type == "analysis-intent",
            ApprovalRecord.subject_id == intent.id,
            ApprovalRecord.requested_action == "execute-python-data-analysis",
        )
    )
    zero_hash = "0" * 64
    approval_hash = (
        approval.intent_hash
        if approval is not None and approval.user_decision == "approved"
        else zero_hash
    )
    approved_identity = AnalysisReviewIdentity(
        dataset_content_hash=dataset.content_hash,
        dataset_profile_sha256=intent.dataset_profile_sha256 or zero_hash,
        analysis_spec_sha256=intent.spec_sha256 or zero_hash,
        compiler_version=intent.compiler_version or "invalid",
        code_sha256=intent.code_sha256 or zero_hash,
        approval_hash=approval_hash,
        runtime_policy_id=intent.runtime_policy_id or "invalid",
    )
    result_metadata = result_artifact.metadata_json if result_artifact is not None else {}
    observed_identity = AnalysisReviewIdentity(
        dataset_content_hash=dataset.content_hash,
        dataset_profile_sha256=_review_metadata_hash(
            result_metadata,
            "datasetProfileSha256",
        ),
        analysis_spec_sha256=_review_metadata_hash(
            result_metadata,
            "analysisSpecSha256",
        ),
        compiler_version=_review_metadata_text(result_metadata, "compilerVersion"),
        code_sha256=_review_metadata_hash(result_metadata, "approvedCodeSha256"),
        approval_hash=_review_metadata_hash(result_metadata, "payloadSha256"),
        runtime_policy_id=_review_metadata_text(result_metadata, "policyProfileId"),
    )
    structured_record = session.scalar(
        select(StructuredAnalysisResultRecord).where(
            StructuredAnalysisResultRecord.run_id == run.id
        )
    )
    recorded_result_sha256 = (
        structured_record.result_sha256
        if structured_record is not None
        and structured_record.analysis_intent_id == intent.id
        and structured_record.analysis_spec_id == intent.analysis_spec_id
        else zero_hash
    )
    expected_result_sha256 = (
        recorded_result_sha256
        if result_metadata.get("structuredResultSha256") == recorded_result_sha256
        else zero_hash
    )
    parsed_spec: AnalysisSpec | None
    try:
        parsed_spec = AnalysisSpec.model_validate_json(spec_bytes)
    except (ValidationError, ValueError):
        parsed_spec = None
    figure_lineage = _compiled_figure_lineage(
        parsed_spec,
        figure_artifact,
        zero_hash=zero_hash,
    )
    reviewed = review_analysis_spec_outputs(
        analysis_spec_json=spec_bytes,
        results_json=result_bytes,
        summary_csv=summary_bytes,
        executed_notebook_json=notebook_bytes,
        approved_code=intent.code,
        approved_identity=approved_identity,
        observed_identity=observed_identity,
        expected_result_sha256=expected_result_sha256,
        figure_lineage=figure_lineage,
    )
    artifact_issue_codes = {
        "summary-matches-results",
        "figure-lineage-matches",
        "notebook-code-matches",
    }
    numeric_issue_codes = {
        "sample-size-present",
        "missing-count-present",
        "p-value-valid",
        "effect-size-present",
        "confidence-interval-present",
    }
    artifact_issues = [
        DatasetAnalysisReviewIssue(
            code=check.code,
            message=check.message,
            artifact_id=check.artifact_id,
        )
        for check in reviewed.checks
        if check.status == "failed"
        and (check.category == "identity" or check.code in artifact_issue_codes)
    ]
    numeric_issues = [
        DatasetAnalysisReviewIssue(
            code=check.code,
            message=check.message,
            artifact_id=check.artifact_id,
        )
        for check in reviewed.checks
        if check.status == "failed" and check.code in numeric_issue_codes
    ]
    other_failures = [
        DatasetAnalysisReviewIssue(
            code=check.code,
            message=check.message,
            artifact_id=check.artifact_id,
        )
        for check in reviewed.checks
        if check.status == "failed"
        and check.category != "identity"
        and check.code not in artifact_issue_codes | numeric_issue_codes
    ]
    method_warnings = [
        DatasetAnalysisReviewIssue(
            code=check.code,
            message=check.message,
            artifact_id=check.artifact_id,
        )
        for check in reviewed.checks
        if check.status == "warning"
    ]
    return DatasetAnalysisReviewResult(
        schema_version="1",
        verdict=reviewed.verdict,
        checks=[
            DatasetAnalysisReviewCheck(
                code=check.code,
                status=check.status,
                message=check.message,
                artifact_id=check.artifact_id,
            )
            for check in reviewed.checks
        ],
        artifact_issues=artifact_issues,
        numeric_issues=[*numeric_issues, *other_failures],
        method_warnings=method_warnings,
        required_revisions=reviewed.required_revisions,
        run_id=run.id,
        analysis_intent_id=intent.id,
        input_dataset_content_hash=dataset.content_hash,
        conclusion=reviewed.conclusion,
        analysis_spec_id=intent.analysis_spec_id,
        structured_result_sha256=recorded_result_sha256,
    )


def _compiled_figure_lineage(
    spec: AnalysisSpec | None,
    artifact: ArtifactRecord | None,
    *,
    zero_hash: str,
) -> FigureLineage | None:
    if artifact is None or spec is None:
        return None
    operation = spec.operation
    if operation.type == "descriptive":
        columns = operation.columns
    elif operation.type == "two-group-comparison":
        columns = [operation.group_column, operation.outcome_column]
    else:
        columns = [operation.x_column, operation.y_column]
    return FigureLineage(
        artifact_id=artifact.id,
        analysis_spec_sha256=_review_metadata_hash(
            artifact.metadata_json,
            "analysisSpecSha256",
            fallback=zero_hash,
        ),
        code_sha256=_review_metadata_hash(
            artifact.metadata_json,
            "approvedCodeSha256",
            fallback=zero_hash,
        ),
        columns=columns,
    )


def _review_metadata_hash(
    metadata: dict[str, Any],
    key: str,
    *,
    fallback: str = "0" * 64,
) -> str:
    value = metadata.get(key)
    if (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    ):
        return value
    return fallback


def _review_metadata_text(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    return value if isinstance(value, str) and value.strip() else "invalid"


def _review_artifact_bytes(
    project: ProjectRecord,
    run: RunRecord,
    intent: AnalysisIntentRecord,
    artifact: ArtifactRecord | None,
) -> bytes:
    if artifact is None:
        return b""
    _verified_artifact_evidence(project, run, intent, artifact)
    raw_size = artifact.metadata_json.get("sizeBytes")
    if not isinstance(raw_size, int) or isinstance(raw_size, bool) or raw_size > 2 * 1024 * 1024:
        return b"\0" * (2 * 1024 * 1024 + 1)
    try:
        descriptor = open_workspace_file_without_symlinks(
            Path(project.project_path),
            Path(project.project_path) / artifact.path,
        )
    except RuntimeServiceError:
        return b""
    try:
        before = os.fstat(descriptor)
        content = b""
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            content += chunk
        after = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or len(content) != raw_size
            or digest.hexdigest() != artifact.content_hash
        ):
            return b""
        return content
    finally:
        os.close(descriptor)


def handle_dataset_review(
    session: Session,
    workflow: WorkflowRecord,
    job: JobRecord,
) -> None:
    if workflow.status != "reviewing":
        raise WorkflowFailure(
            "workflow-not-reviewing",
            "The workflow is no longer ready for deterministic review.",
        )
    plan = session.scalar(
        select(PlanRecord).where(
            PlanRecord.workflow_id == workflow.id,
            PlanRecord.status == "approved",
        )
    )
    tasks = (
        list(
            session.scalars(
                select(TaskRecord)
                .where(
                    TaskRecord.workflow_id == workflow.id,
                    TaskRecord.plan_id == plan.id,
                )
                .order_by(TaskRecord.order_index)
            )
        )
        if plan is not None
        else []
    )
    if plan is None or len(tasks) != 4 or any(task.status != "completed" for task in tasks):
        raise WorkflowFailure(
            "analysis-review-task-set-invalid",
            "Deterministic analysis review requires all four approved steps.",
        )
    for task in tasks:
        assert_current_task_contract(session, workflow, task)

    profile = _validated_profile(tasks[0], workflow)
    inspection_inputs = _inspection_inputs(tasks[0])
    project, dataset = _dataset_records(
        session,
        workflow,
        inspection_inputs.dataset_source_id,
    )
    try:
        current_profile = inspect_csv_dataset(
            workspace_root=Path(project.project_path),
            dataset_path=Path(dataset.local_path),
            source_id=dataset.id,
            expected_content_hash=dataset.content_hash,
            max_sample_rows=inspection_inputs.max_sample_rows,
        )
    except DatasetInspectionError:
        raise WorkflowFailure(
            "analysis-review-dataset-invalid",
            "The reviewed dataset no longer passes its integrity checks.",
        ) from None
    if current_profile.profile_sha256 != dataset_profile_sha256(profile):
        raise WorkflowFailure(
            "analysis-review-dataset-changed",
            "The reviewed dataset profile changed after analysis preparation.",
        )
    collected = handle_collect_artifacts(session, workflow, tasks[3])
    if collected != tasks[3].outputs:
        raise WorkflowFailure(
            "analysis-review-artifact-evidence-changed",
            "The collected artifact evidence changed before deterministic review.",
        )

    intent_id = collected.get("analysisIntentId")
    run_id = collected.get("runId")
    if not isinstance(intent_id, str) or not isinstance(run_id, str):
        raise WorkflowFailure(
            "analysis-review-lineage-invalid",
            "The deterministic review has no exact intent and run binding.",
        )
    intent = session.get(AnalysisIntentRecord, intent_id)
    run = session.get(RunRecord, run_id)
    if (
        intent is None
        or run is None
        or intent.workflow_id != workflow.id
        or run.analysis_intent_id != intent.id
    ):
        raise WorkflowFailure(
            "analysis-review-lineage-invalid",
            "The deterministic review intent and run binding is invalid.",
        )
    if intent.analysis_spec_id is not None:
        result = _compiled_dataset_review_result(
            session=session,
            project=project,
            dataset=dataset,
            intent=intent,
            run=run,
        )
        verdict = result.verdict
        review = ReviewRecord(
            id=str(uuid.uuid4()),
            workflow_id=workflow.id,
            plan_id=plan.id,
            task_id=tasks[3].id,
            review_type="deterministic-analysis-v1",
            input_sha256=job.input_sha256,
            verdict=verdict,
            result_json=result.model_dump(mode="json", by_alias=True),
        )
        session.add(review)
        finish_job(session, job, "succeeded")
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "review.completed",
                    ReviewEventData(
                        review_id=review.id,
                        verdict=verdict,
                        claim_count=None,
                    ),
                    tasks[3].id,
                    job.id,
                ),
                (
                    "analysis.review-completed",
                    ReviewEventData(
                        review_id=review.id,
                        verdict=verdict,
                        claim_count=None,
                    ),
                    tasks[3].id,
                    job.id,
                ),
            ],
        )
        if verdict == "passed":
            transition_workflow(session, workflow, "completed")
        elif verdict != "passed-with-warnings":
            transition_workflow(
                session,
                workflow,
                "blocked",
                reason_code="analysis-review-required",
                blocking_message=(
                    "Deterministic review found inconsistent compiled analysis evidence."
                ),
            )
        return
    checks = [
        DatasetAnalysisReviewCheck(
            code="dataset-hash-matches",
            status="passed",
            message="The source file matches the dataset hash approved for this workflow.",
            artifact_id=None,
        ),
        DatasetAnalysisReviewCheck(
            code="analysis-payload-approved",
            status="passed",
            message="The completed run matches the separately approved analysis payload.",
            artifact_id=None,
        ),
        DatasetAnalysisReviewCheck(
            code="artifact-hashes-match",
            status="passed",
            message="Every recorded run artifact matches its private regular file and hash.",
            artifact_id=None,
        ),
    ]
    profile_warnings = [
        DatasetAnalysisReviewIssue(
            code=warning.code,
            message=warning.message,
            artifact_id=None,
        )
        for warning in profile.warnings
    ]
    method_warnings = [
        DatasetAnalysisReviewIssue(
            code="descriptive-baseline-method-scope",
            message=(
                "The local deterministic baseline records descriptive outputs only; "
                "it does not prove that every inferential, causal, or domain-specific "
                "method implied by the workflow goal was performed."
            ),
            artifact_id=None,
        ),
        *profile_warnings,
    ]
    checks.append(
        DatasetAnalysisReviewCheck(
            code="goal-method-coverage",
            status="warning",
            message=(
                "The approved intent is bound to the workflow goal, but the baseline "
                "method is descriptive and requires explicit user acceptance."
            ),
            artifact_id=None,
        )
    )
    if profile_warnings:
        checks.append(
            DatasetAnalysisReviewCheck(
                code="dataset-profile-warnings",
                status="warning",
                message="The dataset profile contains explicit parsing or sampling warnings.",
                artifact_id=None,
            )
        )
    verdict = "passed-with-warnings" if method_warnings else "passed"
    result = DatasetAnalysisReviewResult(
        schema_version="1",
        verdict=verdict,
        checks=checks,
        artifact_issues=[],
        numeric_issues=[],
        method_warnings=method_warnings,
        required_revisions=[],
        run_id=run_id,
        analysis_intent_id=intent_id,
        input_dataset_content_hash=dataset.content_hash,
        conclusion=None,
        analysis_spec_id=None,
        structured_result_sha256=None,
    )
    review = ReviewRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        plan_id=plan.id,
        task_id=tasks[3].id,
        review_type="deterministic-analysis-v1",
        input_sha256=job.input_sha256,
        verdict=verdict,
        result_json=result.model_dump(mode="json", by_alias=True),
    )
    session.add(review)
    finish_job(session, job, "succeeded")
    append_workflow_events(
        session,
        workflow,
        [
            (
                "review.completed",
                ReviewEventData(
                    review_id=review.id,
                    verdict=verdict,
                    claim_count=None,
                ),
                tasks[3].id,
                job.id,
            ),
            (
                "analysis.review-completed",
                ReviewEventData(
                    review_id=review.id,
                    verdict=verdict,
                    claim_count=None,
                ),
                tasks[3].id,
                job.id,
            ),
        ],
    )
    if verdict == "passed":
        transition_workflow(session, workflow, "completed")


def _verified_artifact_evidence(
    project: ProjectRecord,
    run: RunRecord,
    intent: AnalysisIntentRecord,
    artifact: ArtifactRecord,
) -> dict[str, Any]:
    run_prefix = f"runs/{run.id}/"
    if not artifact.path.startswith(run_prefix):
        raise WorkflowFailure(
            "analysis-artifact-path-invalid",
            "An analysis artifact is outside its exact run directory.",
        )
    try:
        descriptor = open_workspace_file_without_symlinks(
            Path(project.project_path),
            Path(project.project_path) / artifact.path,
        )
    except RuntimeServiceError:
        raise WorkflowFailure(
            "analysis-artifact-file-invalid",
            "An analysis artifact is missing or has an unsafe path.",
        ) from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise WorkflowFailure(
                "analysis-artifact-file-invalid",
                "An analysis artifact is not one private regular file.",
            )
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        raw_size = artifact.metadata_json.get("sizeBytes")
        compiled_provenance_matches = True
        if intent.analysis_spec_id is not None:
            compiled_provenance_matches = (
                artifact.metadata_json.get("analysisSpecId")
                == intent.analysis_spec_id
                and artifact.metadata_json.get("analysisSpecSha256")
                == intent.spec_sha256
                and artifact.metadata_json.get("datasetProfileSha256")
                == intent.dataset_profile_sha256
                and artifact.metadata_json.get("compilerVersion")
                == intent.compiler_version
                and artifact.metadata_json.get("approvedCodeSha256")
                == intent.code_sha256
                and artifact.metadata_json.get("policyProfileId")
                == intent.runtime_policy_id
                and artifact.metadata_json.get("policyTemplate")
                == intent.compiler_version
            )
        if (
            identity_before != identity_after
            or size != before.st_size
            or digest.hexdigest() != artifact.content_hash
            or raw_size != size
            or artifact.metadata_json.get("payloadSha256") != intent.payload_sha256
            or artifact.parent_artifacts != [intent.dataset_source_id]
            or not compiled_provenance_matches
        ):
            raise WorkflowFailure(
                "analysis-artifact-integrity-failed",
                "An analysis artifact no longer matches its immutable record.",
            )
        return {
            "artifactId": artifact.id,
            "artifactType": artifact.artifact_type,
            "path": artifact.path,
            "contentHash": artifact.content_hash,
            "sizeBytes": size,
        }
    finally:
        os.close(descriptor)


def deterministic_analysis_code(profile: DatasetProfile) -> str:
    """Return a fixed, policy-compatible baseline analysis for explicit approval."""

    numeric_columns = [
        column.index
        for column in profile.columns
        if column.inferred_type in {"integer", "number"}
    ]
    preferred_column_index = numeric_columns[0] if numeric_columns else 0
    return fixed_analysis_source(
        "baseline",
        selected_column_index=preferred_column_index,
    )


def deterministic_repair_analysis_code(
    profile: DatasetProfile,
    repair_attempt: int,
) -> str:
    """Return one of two bounded, simpler deterministic repair programs."""

    if repair_attempt not in {1, 2}:
        raise WorkflowFailure(
            "analysis-repair-limit-exceeded",
            "Automatic analysis repair is limited to two attempts.",
        )
    if repair_attempt == 1:
        return fixed_analysis_source("repair-1")
    return fixed_analysis_source("repair-2")


def _assert_expected_analysis_artifacts(
    intent: AnalysisIntentRecord,
    run: RunRecord,
    artifacts: list[ArtifactRecord],
) -> None:
    expected = intent.expected_outputs or []
    run_prefix = f"runs/{run.id}/"
    by_relative_path = {
        artifact.path.removeprefix(run_prefix): artifact
        for artifact in artifacts
        if artifact.path.startswith(run_prefix)
    }
    declared_tables, declared_figures, reads_dataset = _declared_analysis_outputs(intent.code)
    required_names = {
        "executed-notebook": ("executed.ipynb", "notebook-executed"),
        "analysis-log": ("execution.log", "log"),
        "environment-manifest": ("environment.json", "environment"),
    }
    missing: list[str] = []
    for output in expected:
        required = required_names.get(output)
        if required is not None:
            name, artifact_type = required
            artifact = by_relative_path.get(name)
            if artifact is None or artifact.artifact_type != artifact_type:
                missing.append(output)
        elif output == "summary-table":
            tables = [
                artifact
                for artifact in artifacts
                if artifact.artifact_type in {"dataset", "structured-data"}
                and artifact.path.removeprefix(run_prefix) in declared_tables
                and artifact.path.startswith(run_prefix)
            ]
            if not reads_dataset or not tables:
                missing.append(output)
        elif output == "figures":
            figures = [
                artifact
                for artifact in artifacts
                if artifact.artifact_type == "figure"
                and artifact.path.removeprefix(run_prefix) in declared_figures
                and artifact.path.startswith(run_prefix)
            ]
            if not figures:
                missing.append(output)
        else:
            missing.append(output)
    if missing or not run.environment_hash:
        raise WorkflowFailure(
            "analysis-expected-artifact-missing",
            "The completed run does not contain every approved, code-declared analysis output.",
        )


def _declared_analysis_outputs(code: str) -> tuple[set[str], set[str], bool]:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return set(), set(), False
    table_names: set[str] = set()
    figure_names: set[str] = set()
    reads_dataset = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        attribute = node.func.attr if isinstance(node.func, ast.Attribute) else None
        if attribute == "read_csv" and node.args and _is_name(node.args[0], "DATASET_PATH"):
            reads_dataset = True
        if not node.args:
            continue
        output_name = _run_dir_literal(node.args[0])
        if output_name is None:
            continue
        if attribute in {"to_csv", "to_json", "to_parquet"}:
            table_names.add(output_name)
        elif attribute == "savefig":
            figure_names.add(output_name)
    return table_names, figure_names, reads_dataset


def _is_name(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _run_dir_literal(node: ast.expr) -> str | None:
    if (
        not isinstance(node, ast.BinOp)
        or not isinstance(node.op, ast.Div)
        or not _is_name(node.left, "RUN_DIR")
        or not isinstance(node.right, ast.Constant)
        or not isinstance(node.right.value, str)
    ):
        return None
    path = Path(node.right.value)
    if path.is_absolute() or len(path.parts) != 1 or path.name in {"", ".", ".."}:
        return None
    return path.name


def _inspection_inputs(task: TaskRecord) -> DatasetInspectionStepInput:
    try:
        return DatasetInspectionStepInput.model_validate(task.inputs)
    except ValidationError:
        raise WorkflowFailure(
            "dataset-task-input-invalid",
            "The approved dataset inspection inputs are invalid.",
        ) from None


def _dataset_records(
    session: Session,
    workflow: WorkflowRecord,
    source_id: str,
) -> tuple[ProjectRecord, SourceRecord]:
    project = session.get(ProjectRecord, workflow.project_id)
    dataset = session.get(SourceRecord, source_id)
    if (
        workflow.workflow_type != "dataset-analysis"
        or project is None
        or dataset is None
        or dataset.project_id != workflow.project_id
        or dataset.source_kind != "dataset"
        or dataset.ingestion_status != "ready"
        or workflow.dataset_source_id != dataset.id
        or workflow.dataset_content_hash != dataset.content_hash
    ):
        raise WorkflowFailure(
            "dataset-binding-invalid",
            "The workflow is not bound to the approved ready dataset.",
        )
    return project, dataset


def _adjacent_task(session: Session, task: TaskRecord, *, offset: int) -> TaskRecord:
    if task.plan_id is None or task.order_index is None:
        raise WorkflowFailure(
            "dataset-step-order-invalid",
            "The dataset workflow step has no immutable plan position.",
        )
    adjacent = session.scalar(
        select(TaskRecord).where(
            TaskRecord.plan_id == task.plan_id,
            TaskRecord.order_index == task.order_index + offset,
        )
    )
    if adjacent is None:
        raise WorkflowFailure(
            "dataset-step-order-invalid",
            "The adjacent dataset workflow step is missing.",
        )
    return adjacent


def _validated_profile(task: TaskRecord, workflow: WorkflowRecord) -> DatasetProfile:
    raw_profile = task.outputs.get("datasetProfile")
    raw_hash = task.outputs.get("datasetProfileSha256")
    try:
        profile = DatasetProfile.model_validate(raw_profile)
    except ValidationError:
        raise WorkflowFailure(
            "dataset-profile-invalid",
            "The persisted dataset profile is invalid.",
        ) from None
    if (
        profile.dataset_source_id != workflow.dataset_source_id
        or profile.content_hash != workflow.dataset_content_hash
        or raw_hash != dataset_profile_sha256(profile)
    ):
        raise WorkflowFailure(
            "dataset-profile-integrity-failed",
            "The persisted dataset profile no longer matches its immutable hash.",
        )
    return profile
