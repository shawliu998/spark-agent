from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Literal, cast

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..._analysis_service.filesystem import open_workspace_file_without_symlinks
from ...analysis import RuntimeServiceError
from ...analysis_service import (
    AnalysisServiceError,
    decide_workflow_analysis_intent,
    validate_workflow_analysis_intent,
)
from ...model_gateway import OpenAICompatibleModelGateway
from ...models import (
    AnalysisIntentRecord,
    ApprovalRecord,
    EventRecord,
    JobRecord,
    PlanRecord,
    ProjectRecord,
    ReviewRecord,
    SourceRecord,
    TaskRecord,
    WorkflowRecord,
    utc_now,
)
from ..schemas import (
    AnalysisApprovalEventData,
    CancelEventData,
    CreatedEventData,
    DatasetAnalysisReviewResult,
    DatasetReviewWarningsAcceptedEventData,
    PlanEventData,
    RemoteDataApprovalEventData,
    ResearchWorkflowCreateIn,
    TaskEventData,
    WorkflowEventData,
)
from .events import append_workflow_events, transition_task, transition_workflow
from .integrity import (
    TASK_PERMISSIONS_BY_TYPE,
    WorkflowConflict,
    assert_plan_approval_integrity,
    assert_plan_for_workflow,
    content_sha256,
    model_payload,
    plan_approval_hash,
    plan_step_materialization,
    workflow_create_hash,
)
from .jobs import (
    analysis_execution_operation_key,
    current_job_input_hash,
    enqueue_job,
    job_input_compatibility,
)


def start_workflow(
    session: Session,
    project: ProjectRecord,
    payload: ResearchWorkflowCreateIn,
    idempotency_key: str,
    *,
    gateway: OpenAICompatibleModelGateway,
) -> WorkflowRecord:
    payload_hash = workflow_create_hash(payload)
    project_id = project.id
    existing = _workflow_create_replay(
        session,
        project_id=project_id,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
    )
    if existing is not None:
        return existing
    dataset: SourceRecord | None = None
    if payload.workflow_type == "dataset-analysis":
        dataset = _verified_dataset_for_workflow(session, project, payload.dataset_source_id)
    if payload.generation_mode == "remote-model-assisted" and not gateway.configured:
        raise WorkflowConflict(
            "model-gateway-not-configured",
            "Configure a remote model endpoint, model, and credential before starting "
            "a remote-model-assisted workflow.",
            retryable=True,
        )
    workflow = WorkflowRecord(
        id=str(uuid.uuid4()),
        project_id=project.id,
        create_idempotency_key=idempotency_key,
        create_payload_sha256=payload_hash,
        workflow_type=payload.workflow_type,
        dataset_source_id=dataset.id if dataset is not None else None,
        dataset_content_hash=dataset.content_hash if dataset is not None else None,
        goal=payload.goal,
        generation_mode=payload.generation_mode,
        status="planning",
        row_version=1,
        event_sequence=0,
    )
    session.add(workflow)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        replay = _workflow_create_replay(
            session,
            project_id=project_id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        if replay is not None:
            return replay
        raise
    enqueue_job(
        session,
        workflow,
        kind="generate-plan",
        operation_key=f"workflow:{workflow.id}:plan:1",
    )
    events: list[tuple[str, WorkflowEventData, str | None, str | None]] = [
        (
            "workflow.created",
            CreatedEventData(
                workflow_type=payload.workflow_type,
                goal_sha256=hashlib.sha256(workflow.goal.encode("utf-8")).hexdigest(),
                generation_mode=payload.generation_mode,
            ),
            None,
            None,
        )
    ]
    if payload.generation_mode == "remote-model-assisted":
        events.append(
            (
                "remote-data.approved",
                RemoteDataApprovalEventData(
                    provider="openai-compatible",
                    endpoint_host=gateway.endpoint_host,
                    endpoint_identity=gateway.endpoint_identity,
                    model=gateway.default_model,
                    data_categories=["user-goal"],
                ),
                None,
                None,
            )
        )
    append_workflow_events(session, workflow, events)
    session.commit()
    session.refresh(workflow)
    return workflow


def _workflow_create_replay(
    session: Session,
    *,
    project_id: str,
    idempotency_key: str,
    payload_hash: str,
) -> WorkflowRecord | None:
    existing = session.scalar(
        select(WorkflowRecord)
        .where(
            WorkflowRecord.project_id == project_id,
            WorkflowRecord.create_idempotency_key == idempotency_key,
        )
        .execution_options(populate_existing=True)
    )
    if existing is None:
        return None
    if existing.create_payload_sha256 != payload_hash:
        raise WorkflowConflict(
            "idempotency-key-reused",
            "This Idempotency-Key was already used with a different workflow request.",
        )
    return existing


def _verified_dataset_for_workflow(
    session: Session,
    project: ProjectRecord,
    dataset_source_id: str,
) -> SourceRecord:
    dataset = session.get(SourceRecord, dataset_source_id)
    if dataset is None or dataset.project_id != project.id:
        raise WorkflowConflict(
            "dataset-not-found",
            "The selected dataset does not exist in this project.",
        )
    if (
        dataset.source_kind != "dataset"
        or dataset.ingestion_status != "ready"
        or len(dataset.content_hash) != 64
        or any(character not in "0123456789abcdef" for character in dataset.content_hash)
        or Path(dataset.local_path).suffix.lower() != ".csv"
    ):
        raise WorkflowConflict(
            "dataset-not-ready",
            "The selected source is not a ready, content-addressed CSV dataset.",
        )
    descriptor: int | None = None
    try:
        descriptor = open_workspace_file_without_symlinks(
            Path(project.project_path),
            Path(dataset.local_path),
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise WorkflowConflict(
                "dataset-file-invalid",
                "The selected dataset is not a private regular workspace file.",
            )
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        identity_fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in identity_fields):
            raise WorkflowConflict(
                "dataset-file-changed",
                "The selected dataset changed while its workflow identity was verified.",
                retryable=True,
            )
        if digest.hexdigest() != dataset.content_hash:
            raise WorkflowConflict(
                "dataset-content-hash-mismatch",
                "The selected dataset bytes no longer match its recorded content hash.",
            )
    except WorkflowConflict:
        raise
    except (OSError, RuntimeServiceError):
        raise WorkflowConflict(
            "dataset-file-invalid",
            "The selected dataset file is missing or has an unsafe workspace path.",
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return dataset


def materialize_plan_tasks(
    session: Session, workflow: WorkflowRecord, plan: PlanRecord
) -> list[TaskRecord]:
    assert_plan_for_workflow(workflow, plan)
    existing = list(
        session.scalars(
            select(TaskRecord).where(TaskRecord.plan_id == plan.id).order_by(TaskRecord.order_index)
        )
    )
    if existing:
        return existing
    spec = assert_plan_for_workflow(workflow, plan)
    tasks: list[TaskRecord] = []
    for order_index, step in enumerate(spec.steps):
        inputs = model_payload(step.inputs)
        expected_outputs, risk_level, timeout_seconds = plan_step_materialization(
            spec,
            order_index,
        )
        task = TaskRecord(
            id=str(uuid.uuid4()),
            project_id=workflow.project_id,
            workflow_id=workflow.id,
            plan_id=plan.id,
            step_key=step.key,
            order_index=order_index,
            objective=step.objective,
            task_type=step.type,
            inputs=inputs,
            input_sha256=content_sha256(
                {
                    "inputs": inputs,
                    "objective": step.objective,
                    "stepKey": step.key,
                    "stepType": step.type,
                }
            ),
            expected_outputs=expected_outputs,
            outputs={},
            acceptance_criteria=list(step.acceptance_criteria),
            permissions=TASK_PERMISSIONS_BY_TYPE[step.type],
            risk_level=risk_level,
            status="pending",
            row_version=1,
            retries=0,
            timeout_seconds=timeout_seconds,
        )
        tasks.append(task)
        session.add(task)
    session.flush()
    return tasks


def approve_plan(
    session: Session,
    workflow: WorkflowRecord,
    *,
    approval_id: str,
    plan_id: str,
    plan_version: int,
    plan_sha256: str,
    expected_revision: int,
) -> WorkflowRecord:
    plan = session.get(PlanRecord, plan_id)
    approval = session.get(ApprovalRecord, approval_id)
    if (
        plan is None
        or plan.workflow_id != workflow.id
        or plan.version != plan_version
        or approval is None
        or approval.workflow_id != workflow.id
        or approval.plan_id != plan.id
    ):
        raise WorkflowConflict(
            "plan-approval-not-found",
            "The plan approval does not belong to this workflow.",
        )
    assert_plan_for_workflow(workflow, plan)
    if plan.spec_sha256 != plan_sha256:
        raise WorkflowConflict(
            "plan-hash-mismatch",
            "The displayed plan no longer matches the stored plan.",
        )
    assert_plan_approval_integrity(session, workflow, plan)
    if approval.payload_schema_version not in {
        "workflow-plan-approval-v1",
        "workflow-plan-approval-v2",
        "workflow-plan-approval-v3",
    }:
        raise WorkflowConflict(
            "approval-schema-unsupported",
            "The plan approval payload schema is not supported.",
        )
    expected_approval_hash = plan_approval_hash(
        plan,
        approval.affected_resources,
        schema_version=approval.payload_schema_version,
        workflow_goal=(
            workflow.goal
            if approval.payload_schema_version in {
                "workflow-plan-approval-v2",
                "workflow-plan-approval-v3",
            }
            else None
        ),
        risk_level=(
            approval.risk_level
            if approval.payload_schema_version in {
                "workflow-plan-approval-v2",
                "workflow-plan-approval-v3",
            }
            else None
        ),
        reason=(
            approval.reason
            if approval.payload_schema_version in {
                "workflow-plan-approval-v2",
                "workflow-plan-approval-v3",
            }
            else None
        ),
        subject_id=(
            approval.subject_id
            if approval.payload_schema_version in {
                "workflow-plan-approval-v2",
                "workflow-plan-approval-v3",
            }
            else None
        ),
        task_id=(
            approval.task_id
            if approval.payload_schema_version in {
                "workflow-plan-approval-v2",
                "workflow-plan-approval-v3",
            }
            else None
        ),
        dataset_source_id=(
            workflow.dataset_source_id
            if approval.payload_schema_version == "workflow-plan-approval-v3"
            else None
        ),
        dataset_content_hash=(
            workflow.dataset_content_hash
            if approval.payload_schema_version == "workflow-plan-approval-v3"
            else None
        ),
        expected_workflow_revision=(
            int(
                next(
                    resource.removeprefix("workflow-revision:")
                    for resource in approval.affected_resources
                    if resource.startswith("workflow-revision:")
                )
            )
            if approval.payload_schema_version == "workflow-plan-approval-v3"
            else None
        ),
    )
    if approval.intent_hash != expected_approval_hash:
        raise WorkflowConflict(
            "approval-hash-mismatch",
            "The approval payload does not match this immutable plan.",
        )
    if approval.user_decision == "approved" and plan.status == "approved":
        return workflow
    if approval.user_decision is not None:
        raise WorkflowConflict(
            "approval-already-decided",
            "This plan approval already has a final decision.",
        )
    if workflow.status != "waiting-plan-approval" or plan.status != "pending-approval":
        raise WorkflowConflict(
            "plan-not-approvable",
            "This plan is no longer waiting for approval.",
        )
    now = utc_now()
    approval_result = session.execute(
        update(ApprovalRecord)
        .where(
            ApprovalRecord.id == approval.id,
            ApprovalRecord.row_version == approval.row_version,
            ApprovalRecord.user_decision.is_(None),
        )
        .values(
            user_decision="approved",
            decided_at=now,
            row_version=ApprovalRecord.row_version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[object], approval_result).rowcount != 1:
        raise WorkflowConflict(
            "approval-revision-conflict",
            "The approval changed before the decision was recorded.",
            retryable=True,
        )
    previous_approved = session.scalar(
        select(PlanRecord).where(
            PlanRecord.workflow_id == workflow.id,
            PlanRecord.status == "approved",
            PlanRecord.id != plan.id,
        )
    )
    if previous_approved is not None:
        if previous_approved.version >= plan.version:
            raise WorkflowConflict(
                "plan-version-conflict",
                "A newer approved workflow plan already exists.",
            )
        session.execute(
            update(PlanRecord)
            .where(
                PlanRecord.id == previous_approved.id,
                PlanRecord.status == "approved",
            )
            .values(status="superseded", superseded_at=now)
            .execution_options(synchronize_session=False)
        )
    session.execute(
        update(PlanRecord)
        .where(PlanRecord.id == plan.id, PlanRecord.status == "pending-approval")
        .values(status="approved", approved_at=now)
        .execution_options(synchronize_session=False)
    )
    tasks = materialize_plan_tasks(session, workflow, plan)
    first_task = tasks[0]
    transition_task(session, first_task, "queued")
    job = enqueue_job(
        session,
        workflow,
        kind="execute-task",
        task=first_task,
        operation_key=f"workflow:{workflow.id}:task:{first_task.id}",
    )
    transition_workflow(
        session,
        workflow,
        "running",
        expected_revision=expected_revision,
    )
    append_workflow_events(
        session,
        workflow,
        [
            (
                "plan.approved",
                PlanEventData(
                    plan_id=plan.id,
                    version=plan.version,
                    plan_sha256=plan.spec_sha256,
                ),
                None,
                None,
            ),
            (
                "step.queued",
                TaskEventData(
                    task_id=first_task.id,
                    step_key=first_task.step_key or "",
                    order_index=first_task.order_index or 0,
                    status="queued",
                ),
                first_task.id,
                job.id,
            ),
        ],
    )
    session.commit()
    session.refresh(workflow)
    session.refresh(plan)
    session.refresh(approval)
    return workflow


def approve_analysis_execution(
    session: Session,
    workflow: WorkflowRecord,
    *,
    approval_id: str,
    intent_id: str,
    payload_sha256: str,
    expected_revision: int,
) -> WorkflowRecord:
    return decide_analysis_execution(
        session,
        workflow,
        approval_id=approval_id,
        intent_id=intent_id,
        decision="approved",
        payload_sha256=payload_sha256,
        expected_revision=expected_revision,
    )


def decide_analysis_execution(
    session: Session,
    workflow: WorkflowRecord,
    *,
    approval_id: str,
    intent_id: str,
    decision: str,
    payload_sha256: str,
    expected_revision: int,
) -> WorkflowRecord:
    if decision not in {"approved", "rejected"}:
        raise WorkflowConflict(
            "analysis-decision-invalid",
            "The analysis decision must be approved or rejected.",
        )
    intent = session.get(AnalysisIntentRecord, intent_id)
    approval = session.get(ApprovalRecord, approval_id)
    task = session.get(TaskRecord, intent.task_id) if intent is not None else None
    if _analysis_decision_is_exact_replay(
        session,
        workflow,
        intent=intent,
        approval=approval,
        task=task,
        decision=decision,
        payload_sha256=payload_sha256,
        expected_revision=expected_revision,
    ):
        from .snapshots import workflow_snapshot

        workflow_snapshot(session, workflow)
        return workflow
    if workflow.row_version != expected_revision:
        raise WorkflowConflict(
            "workflow-revision-conflict",
            "The workflow changed before analysis approval. Reload it and try again.",
            retryable=True,
        )
    if (
        workflow.workflow_type != "dataset-analysis"
        or workflow.status != "running"
        or workflow.cancel_requested_at is not None
        or intent is None
        or approval is None
        or task is None
        or intent.workflow_id != workflow.id
        or task.workflow_id != workflow.id
        or task.step_key != "execute-analysis"
        or approval.workflow_id != workflow.id
        or approval.task_id != task.id
        or approval.subject_type != "analysis-intent"
        or approval.subject_id != intent.id
        or approval.intent_hash != payload_sha256
        or intent.payload_sha256 != payload_sha256
    ):
        raise WorkflowConflict(
            "analysis-approval-binding-invalid",
            "The analysis approval does not match this workflow and immutable intent.",
        )
    from .snapshots import workflow_snapshot

    current_snapshot = workflow_snapshot(session, workflow)
    current_approval_ids = {item.id for item in current_snapshot.pending_approvals}
    if (
        current_snapshot.analysis_intent is None
        or current_snapshot.analysis_intent.id != intent.id
        or current_snapshot.analysis_intent.payload_sha256 != payload_sha256
        or approval.id not in current_approval_ids
    ):
        raise WorkflowConflict(
            "analysis-approval-binding-invalid",
            "The analysis decision does not match the current canonical workflow snapshot.",
        )
    try:
        validate_workflow_analysis_intent(
            session,
            intent,
            expected_workflow_id=workflow.id,
            expected_workflow_revision=expected_revision,
            require_approval=True,
            require_current_revision=True,
        )
        decided = decide_workflow_analysis_intent(
            session,
            intent.id,
            decision,
            expected_workflow_id=workflow.id,
            expected_workflow_revision=expected_revision,
        )
    except AnalysisServiceError as error:
        raise WorkflowConflict(error.code, error.detail) from None
    if decided.decision != decision:
        raise WorkflowConflict(
            "analysis-approval-binding-invalid",
            "The immutable analysis intent did not record the requested decision.",
        )
    if decision == "rejected":
        if task.status == "waiting-approval":
            transition_task(session, task, "blocked")
        elif task.status != "blocked":
            raise WorkflowConflict(
                "analysis-task-not-rejectable",
                "The analysis step is no longer waiting for this rejection.",
            )
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "analysis.rejected",
                    AnalysisApprovalEventData(
                        approval_id=approval.id,
                        analysis_intent_id=intent.id,
                        task_id=task.id,
                        payload_sha256=intent.payload_sha256,
                        approval_schema_version=cast(
                            Literal["analysis-intent-v2", "analysis-intent-v3"],
                            approval.payload_schema_version,
                        ),
                        expected_workflow_revision=expected_revision,
                    ),
                    task.id,
                    None,
                )
            ],
        )
        transition_workflow(
            session,
            workflow,
            "blocked",
            expected_revision=expected_revision,
            reason_code="analysis-execution-rejected",
            blocking_message="The proposed analysis execution was rejected.",
        )
        session.commit()
        session.refresh(workflow)
        return workflow
    if task.status == "waiting-approval":
        transition_task(session, task, "queued")
    elif task.status not in {"queued", "running", "completed"}:
        raise WorkflowConflict(
            "analysis-task-not-approvable",
            "The analysis step is no longer waiting for this approval.",
        )
    job = enqueue_job(
        session,
        workflow,
        kind="execute-task",
        task=task,
        operation_key=analysis_execution_operation_key(workflow.id, intent.id),
    )
    existing_event = session.scalar(
        select(EventRecord).where(
            EventRecord.workflow_id == workflow.id,
            EventRecord.event_type == "analysis.approved",
            EventRecord.job_id == job.id,
        )
    )
    if existing_event is None:
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "analysis.approved",
                    AnalysisApprovalEventData(
                        approval_id=approval.id,
                        analysis_intent_id=intent.id,
                        task_id=task.id,
                        job_id=job.id,
                        payload_sha256=intent.payload_sha256,
                        approval_schema_version=cast(
                            Literal["analysis-intent-v2", "analysis-intent-v3"],
                            approval.payload_schema_version,
                        ),
                        expected_workflow_revision=expected_revision,
                    ),
                    task.id,
                    job.id,
                )
            ],
        )
    cancellation_guard = session.execute(
        update(WorkflowRecord)
        .where(
            WorkflowRecord.id == workflow.id,
            WorkflowRecord.row_version == expected_revision,
            WorkflowRecord.status == "running",
            WorkflowRecord.cancel_requested_at.is_(None),
        )
        .values(updated_at=WorkflowRecord.updated_at)
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[object], cancellation_guard).rowcount != 1:
        session.rollback()
        raise WorkflowConflict(
            "workflow-cancelled-during-analysis-approval",
            "The workflow was cancelled before analysis approval could be queued.",
        )
    session.commit()
    session.refresh(workflow)
    return workflow


def _analysis_decision_is_exact_replay(
    session: Session,
    workflow: WorkflowRecord,
    *,
    intent: AnalysisIntentRecord | None,
    approval: ApprovalRecord | None,
    task: TaskRecord | None,
    decision: str,
    payload_sha256: str,
    expected_revision: int,
) -> bool:
    if (
        intent is None
        or approval is None
        or task is None
        or intent.workflow_id != workflow.id
        or intent.task_id != task.id
        or task.workflow_id != workflow.id
        or approval.workflow_id != workflow.id
        or approval.task_id != task.id
        or approval.subject_type != "analysis-intent"
        or approval.subject_id != intent.id
        or approval.intent_hash != payload_sha256
        or intent.payload_sha256 != payload_sha256
        or intent.decision != decision
        or approval.user_decision != decision
    ):
        return False
    event_type = "analysis.approved" if decision == "approved" else "analysis.rejected"
    matching_events = [
        event
        for event in session.scalars(
            select(EventRecord).where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.task_id == task.id,
                EventRecord.event_type == event_type,
            )
        )
        if event.payload.get("approvalId") == approval.id
        and event.payload.get("analysisIntentId") == intent.id
        and event.payload.get("payloadSha256") == payload_sha256
        and event.payload.get("expectedWorkflowRevision") == expected_revision
    ]
    if len(matching_events) > 1:
        raise WorkflowConflict(
            "analysis-decision-event-conflict",
            "The analysis decision has duplicate workflow audit events.",
        )
    return len(matching_events) == 1


def request_cancel(
    session: Session,
    workflow: WorkflowRecord,
    *,
    expected_revision: int | None,
) -> WorkflowRecord:
    if workflow.status == "cancelled":
        return workflow
    if workflow.status == "completed":
        raise WorkflowConflict(
            "workflow-already-completed",
            "A completed workflow cannot be cancelled.",
        )
    if expected_revision is not None and workflow.row_version != expected_revision:
        raise WorkflowConflict(
            "workflow-revision-conflict",
            "The workflow changed before cancellation. Reload it and try again.",
            retryable=True,
        )
    if workflow.cancel_requested_at is None:
        now = utc_now()
        result = session.execute(
            update(WorkflowRecord)
            .where(
                WorkflowRecord.id == workflow.id,
                WorkflowRecord.row_version == workflow.row_version,
                WorkflowRecord.cancel_requested_at.is_(None),
            )
            .values(
                cancel_requested_at=now,
                row_version=WorkflowRecord.row_version + 1,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if cast(CursorResult[object], result).rowcount != 1:
            raise WorkflowConflict(
                "workflow-revision-conflict",
                "The workflow changed before cancellation. Reload it and try again.",
                retryable=True,
            )
        session.flush()
        session.refresh(workflow)
        append_workflow_events(
            session,
            workflow,
            [("workflow.cancel-requested", CancelEventData(requested=True), None, None)],
        )
    active_job = session.scalar(
        select(JobRecord).where(
            JobRecord.workflow_id == workflow.id,
            JobRecord.status == "leased",
        )
    )
    if active_job is None:
        now = utc_now()
        session.execute(
            update(JobRecord)
            .where(
                JobRecord.workflow_id == workflow.id,
                JobRecord.status == "queued",
            )
            .values(status="cancelled", finished_at=now, updated_at=now)
        )
        session.execute(
            update(TaskRecord)
            .where(
                TaskRecord.workflow_id == workflow.id,
                TaskRecord.status.in_(
                    [
                        "pending",
                        "queued",
                        "running",
                        "waiting-approval",
                        "blocked",
                        "failed",
                    ]
                ),
            )
            .values(status="cancelled", finished_at=now, updated_at=now)
        )
        transition_workflow(session, workflow, "cancelled")
    session.commit()
    session.refresh(workflow)
    return workflow


def accept_review_warnings(
    session: Session,
    workflow: WorkflowRecord,
    *,
    review_id: str,
    review_input_sha256: str,
    expected_revision: int,
) -> WorkflowRecord:
    review = session.get(ReviewRecord, review_id)
    existing_events = list(
        session.scalars(
            select(EventRecord).where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.event_type == "analysis.review-warnings-accepted",
            )
        )
    )
    exact_replay = [
        event
        for event in existing_events
        if event.payload.get("reviewId") == review_id
        and event.payload.get("reviewInputSha256") == review_input_sha256
        and event.payload.get("expectedWorkflowRevision") == expected_revision
        and event.payload.get("decision") == "accepted"
    ]
    if len(existing_events) > 1 or (existing_events and not exact_replay):
        raise WorkflowConflict(
            "review-warning-decision-conflict",
            "Review warnings already have a different final decision.",
        )
    if exact_replay:
        if workflow.status != "completed" or review is None:
            raise WorkflowConflict(
                "review-warning-acceptance-integrity-failed",
                "The recorded warning acceptance is not bound to a completed workflow.",
            )
        from .snapshots import workflow_snapshot

        workflow_snapshot(session, workflow)
        return workflow
    if workflow.row_version != expected_revision:
        raise WorkflowConflict(
            "workflow-revision-conflict",
            "The workflow changed before review warnings were accepted. Reload it and try again.",
            retryable=True,
        )
    from .snapshots import workflow_snapshot

    current_snapshot = workflow_snapshot(session, workflow)
    if (
        current_snapshot.latest_review is None
        or current_snapshot.latest_review.id != review_id
        or current_snapshot.latest_review.input_sha256 != review_input_sha256
        or "accept-review-warnings" not in current_snapshot.allowed_actions
    ):
        raise WorkflowConflict(
            "review-warning-acceptance-invalid",
            "The warning acceptance does not match the current canonical workflow snapshot.",
        )
    plan = session.scalar(
        select(PlanRecord).where(
            PlanRecord.workflow_id == workflow.id,
            PlanRecord.status == "approved",
        )
    )
    current_review = (
        session.scalar(
            select(ReviewRecord)
            .where(ReviewRecord.workflow_id == workflow.id, ReviewRecord.plan_id == plan.id)
            .order_by(ReviewRecord.created_at.desc(), ReviewRecord.id.desc())
        )
        if plan is not None
        else None
    )
    review_task = (
        session.get(TaskRecord, review.task_id)
        if review is not None and review.task_id is not None
        else None
    )
    try:
        review_result = (
            DatasetAnalysisReviewResult.model_validate(review.result_json)
            if review is not None
            else None
        )
    except ValidationError:
        review_result = None
    completed_tasks = (
        list(
            session.scalars(
                select(TaskRecord).where(TaskRecord.plan_id == plan.id)
            )
        )
        if plan is not None
        else []
    )
    completion_events = (
        [
            event
            for event in session.scalars(
                select(EventRecord).where(
                    EventRecord.workflow_id == workflow.id,
                    EventRecord.event_type == "review.completed",
                )
            )
            if event.payload.get("reviewId") == review_id
        ]
        if review is not None
        else []
    )
    review_job = (
        session.get(JobRecord, completion_events[0].job_id)
        if len(completion_events) == 1 and completion_events[0].job_id is not None
        else None
    )
    if (
        workflow.workflow_type != "dataset-analysis"
        or workflow.status != "reviewing"
        or workflow.cancel_requested_at is not None
        or plan is None
        or plan.status != "approved"
        or review is None
        or current_review is None
        or current_review.id != review.id
        or review.workflow_id != workflow.id
        or review.plan_id != plan.id
        or review.review_type != "deterministic-analysis-v1"
        or review.verdict != "passed-with-warnings"
        or review.input_sha256 != review_input_sha256
        or review_result is None
        or review_result.verdict != "passed-with-warnings"
        or review_task is None
        or review_task.plan_id != plan.id
        or review_task.workflow_id != workflow.id
        or review_task.step_key != "collect-artifacts"
        or review_task.status != "completed"
        or len(completed_tasks) != 4
        or any(task.status != "completed" for task in completed_tasks)
        or len(completion_events) != 1
        or review_job is None
        or review_job.workflow_id != workflow.id
        or review_job.kind != "review-workflow"
        or review_job.status != "succeeded"
        or review_job.input_sha256 != review.input_sha256
    ):
        raise WorkflowConflict(
            "review-warning-acceptance-invalid",
            "The warning acceptance does not match the current deterministic analysis review.",
        )
    append_workflow_events(
        session,
        workflow,
        [
            (
                "analysis.review-warnings-accepted",
                DatasetReviewWarningsAcceptedEventData(
                    review_id=review.id,
                    review_input_sha256=review.input_sha256,
                    expected_workflow_revision=expected_revision,
                    decision="accepted",
                ),
                review.task_id,
                None,
            )
        ],
    )
    transition_workflow(
        session,
        workflow,
        "completed",
        expected_revision=expected_revision,
    )
    session.commit()
    session.refresh(workflow)
    return workflow


def retry_workflow(
    session: Session,
    workflow: WorkflowRecord,
    *,
    task_id: str | None,
    expected_revision: int | None,
    idempotency_key: str,
) -> WorkflowRecord:
    request_payload_sha256 = _workflow_mutation_request_hash(
        action="retry",
        workflow_id=workflow.id,
        task_id=task_id,
        expected_revision=expected_revision,
    )
    workflow_id = workflow.id
    starting_revision = workflow.row_version
    try:
        return _retry_workflow_once(
            session,
            workflow,
            task_id=task_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            request_payload_sha256=request_payload_sha256,
        )
    except IntegrityError as error:
        session.rollback()
        replay = _workflow_mutation_replay(
            session,
            workflow_id=workflow_id,
            idempotency_key=idempotency_key,
            request_payload_sha256=request_payload_sha256,
        )
        if replay is not None:
            return replay
        if _durable_workflow_revision_changed(
            session,
            workflow_id=workflow_id,
            starting_revision=starting_revision,
        ):
            raise WorkflowConflict(
                "workflow-revision-conflict",
                "The workflow changed before this action was applied. Reload it and try again.",
                retryable=True,
            ) from error
        raise
    except WorkflowConflict as error:
        if error.code not in {"task-revision-conflict", "workflow-revision-conflict"}:
            raise
        session.rollback()
        replay = _workflow_mutation_replay(
            session,
            workflow_id=workflow_id,
            idempotency_key=idempotency_key,
            request_payload_sha256=request_payload_sha256,
        )
        if replay is not None:
            return replay
        raise


def _retry_workflow_once(
    session: Session,
    workflow: WorkflowRecord,
    *,
    task_id: str | None,
    expected_revision: int | None,
    idempotency_key: str,
    request_payload_sha256: str,
) -> WorkflowRecord:
    replay = _workflow_mutation_replay(
        session,
        workflow_id=workflow.id,
        idempotency_key=idempotency_key,
        request_payload_sha256=request_payload_sha256,
    )
    if replay is not None:
        return replay
    if workflow.status != "failed":
        raise WorkflowConflict(
            "workflow-not-retryable",
            "Only a deterministically failed workflow can be retried.",
        )
    if expected_revision is not None and workflow.row_version != expected_revision:
        raise WorkflowConflict(
            "workflow-revision-conflict",
            "The workflow changed before retry. Reload it and try again.",
            retryable=True,
        )
    failed_jobs = select(JobRecord).where(
        JobRecord.workflow_id == workflow.id,
        JobRecord.status == "failed",
        JobRecord.kind.in_(["generate-plan", "execute-task", "review-workflow"]),
    )
    if task_id is not None:
        failed_jobs = failed_jobs.where(JobRecord.task_id == task_id)
    latest_job = session.scalar(
        failed_jobs.order_by(JobRecord.finished_at.desc(), JobRecord.created_at.desc())
    )
    if latest_job is None or latest_job.status != "failed":
        raise WorkflowConflict(
            "failed-job-not-found",
            "No failed deterministic job is available to retry.",
        )
    task = session.get(TaskRecord, latest_job.task_id) if latest_job.task_id else None
    if task is not None and (task.workflow_id != workflow.id or task.status != "failed"):
        raise WorkflowConflict(
            "task-not-retryable",
            "The selected workflow step cannot be retried.",
        )
    kind = latest_job.kind
    current_hash = current_job_input_hash(session, workflow, kind=kind, task=task)
    if (
        latest_job.input_sha256 != current_hash
        and job_input_compatibility(session, workflow, latest_job, task) != "legacy"
    ):
        raise WorkflowConflict(
            "retry-input-changed",
            "The workflow inputs changed; create a revised plan instead of reusing this retry.",
        )
    if task is not None:
        task.retries += 1
        transition_task(session, task, "queued")
        target = "running"
    else:
        target = "reviewing" if kind == "review-workflow" else "planning"
    enqueue_job(
        session,
        workflow,
        kind=kind,
        task=task,
        operation_key=latest_job.operation_key,
        attempt=latest_job.attempt + 1,
        previous_job_id=latest_job.id,
        request_idempotency_key=idempotency_key,
        request_payload_sha256=request_payload_sha256,
        handler_version=latest_job.handler_version,
    )
    transition_workflow(
        session,
        workflow,
        target,
        expected_revision=workflow.row_version,
    )
    session.commit()
    session.refresh(workflow)
    return workflow


def resume_workflow(
    session: Session,
    workflow: WorkflowRecord,
    *,
    expected_revision: int | None,
    idempotency_key: str,
) -> WorkflowRecord:
    request_payload_sha256 = _workflow_mutation_request_hash(
        action="resume",
        workflow_id=workflow.id,
        task_id=None,
        expected_revision=expected_revision,
    )
    workflow_id = workflow.id
    starting_revision = workflow.row_version
    try:
        return _resume_workflow_once(
            session,
            workflow,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            request_payload_sha256=request_payload_sha256,
        )
    except IntegrityError as error:
        session.rollback()
        replay = _workflow_mutation_replay(
            session,
            workflow_id=workflow_id,
            idempotency_key=idempotency_key,
            request_payload_sha256=request_payload_sha256,
        )
        if replay is not None:
            return replay
        if _durable_workflow_revision_changed(
            session,
            workflow_id=workflow_id,
            starting_revision=starting_revision,
        ):
            raise WorkflowConflict(
                "workflow-revision-conflict",
                "The workflow changed before this action was applied. Reload it and try again.",
                retryable=True,
            ) from error
        raise
    except WorkflowConflict as error:
        if error.code not in {"task-revision-conflict", "workflow-revision-conflict"}:
            raise
        session.rollback()
        replay = _workflow_mutation_replay(
            session,
            workflow_id=workflow_id,
            idempotency_key=idempotency_key,
            request_payload_sha256=request_payload_sha256,
        )
        if replay is not None:
            return replay
        raise


def _resume_workflow_once(
    session: Session,
    workflow: WorkflowRecord,
    *,
    expected_revision: int | None,
    idempotency_key: str,
    request_payload_sha256: str,
) -> WorkflowRecord:
    replay = _workflow_mutation_replay(
        session,
        workflow_id=workflow.id,
        idempotency_key=idempotency_key,
        request_payload_sha256=request_payload_sha256,
    )
    if replay is not None:
        return replay
    analysis_replan = (
        workflow.workflow_type == "dataset-analysis"
        and workflow.status == "blocked"
        and workflow.blocking_code
        in {
            "analysis-execution-rejected",
            "analysis-repair-not-safe",
            "analysis-repair-limit-exceeded",
        }
    )
    if (
        workflow.status != "blocked"
        or (workflow.blocking_code != "no-ready-pdf" and not analysis_replan)
    ):
        raise WorkflowConflict(
            "workflow-not-resumable",
            "This workflow cannot be resumed without a revised plan.",
        )
    if expected_revision is not None and workflow.row_version != expected_revision:
        raise WorkflowConflict(
            "workflow-revision-conflict",
            "The workflow changed before resume. Reload it and try again.",
            retryable=True,
        )
    if analysis_replan:
        latest_plan = session.scalar(
            select(PlanRecord)
            .where(PlanRecord.workflow_id == workflow.id)
            .order_by(PlanRecord.version.desc())
        )
        next_version = (latest_plan.version + 1) if latest_plan is not None else 1
        enqueue_job(
            session,
            workflow,
            kind="generate-plan",
            operation_key=f"workflow:{workflow.id}:plan:{next_version}",
            request_idempotency_key=idempotency_key,
            request_payload_sha256=request_payload_sha256,
        )
        transition_workflow(
            session,
            workflow,
            "planning",
            expected_revision=workflow.row_version,
        )
        session.commit()
        session.refresh(workflow)
        return workflow
    ready_source = session.scalar(
        select(SourceRecord.id).where(
            SourceRecord.project_id == workflow.project_id,
            SourceRecord.source_kind == "pdf",
            SourceRecord.ingestion_status == "ready",
        )
    )
    if ready_source is None:
        raise WorkflowConflict(
            "no-ready-pdf",
            "Import and finish parsing at least one PDF before resuming.",
            retryable=True,
        )
    blocked_task = session.scalar(
        select(TaskRecord).where(
            TaskRecord.workflow_id == workflow.id,
            TaskRecord.task_type == "inspect-sources",
            TaskRecord.status == "blocked",
        )
    )
    if blocked_task is None:
        raise WorkflowConflict(
            "blocked-step-not-found",
            "The blocked source-inspection step could not be found.",
        )
    failed_job = session.scalar(
        select(JobRecord)
        .where(
            JobRecord.workflow_id == workflow.id,
            JobRecord.task_id == blocked_task.id,
            JobRecord.kind == "execute-task",
            JobRecord.status == "failed",
        )
        .order_by(JobRecord.attempt.desc())
    )
    if failed_job is None:
        raise WorkflowConflict(
            "failed-job-not-found",
            "The blocked source-inspection job could not be found.",
        )
    current_hash = current_job_input_hash(session, workflow, kind="execute-task", task=blocked_task)
    if (
        current_hash != failed_job.input_sha256
        and job_input_compatibility(session, workflow, failed_job, blocked_task) != "legacy"
    ):
        raise WorkflowConflict(
            "resume-input-changed",
            "The blocked step inputs changed; approve a revised plan instead.",
        )
    blocked_task.retries += 1
    transition_task(session, blocked_task, "queued")
    enqueue_job(
        session,
        workflow,
        kind="execute-task",
        task=blocked_task,
        operation_key=failed_job.operation_key,
        attempt=failed_job.attempt + 1,
        previous_job_id=failed_job.id,
        request_idempotency_key=idempotency_key,
        request_payload_sha256=request_payload_sha256,
    )
    transition_workflow(
        session,
        workflow,
        "running",
        expected_revision=workflow.row_version,
    )
    session.commit()
    session.refresh(workflow)
    return workflow


def _workflow_mutation_request_hash(
    *,
    action: str,
    workflow_id: str,
    task_id: str | None,
    expected_revision: int | None,
) -> str:
    canonical = json.dumps(
        {
            "action": action,
            "expectedWorkflowRevision": expected_revision,
            "taskId": task_id,
            "workflowId": workflow_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _workflow_mutation_replay(
    session: Session,
    *,
    workflow_id: str,
    idempotency_key: str,
    request_payload_sha256: str,
) -> WorkflowRecord | None:
    existing = session.scalar(
        select(JobRecord).where(
            JobRecord.request_idempotency_key == idempotency_key
        )
    )
    if existing is None:
        return None
    if (
        existing.workflow_id != workflow_id
        or existing.request_payload_sha256 != request_payload_sha256
    ):
        raise WorkflowConflict(
            "idempotency-key-reused",
            "This Idempotency-Key was already used with a different workflow request.",
        )
    replay = session.get(WorkflowRecord, workflow_id, populate_existing=True)
    if replay is None:
        raise RuntimeError("An idempotent workflow mutation references a missing workflow.")
    return replay


def _durable_workflow_revision_changed(
    session: Session,
    *,
    workflow_id: str,
    starting_revision: int,
) -> bool:
    durable = session.get(WorkflowRecord, workflow_id, populate_existing=True)
    return durable is not None and durable.row_version != starting_revision
