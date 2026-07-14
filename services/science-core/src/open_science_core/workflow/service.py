from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Sequence

from pydantic import BaseModel
from sqlalchemy import Select, select, update
from sqlalchemy.orm import Session

from ..models import (
    AnswerRecord,
    ApprovalRecord,
    ClaimEvidenceRecord,
    ClaimRecord,
    EventRecord,
    EvidenceSpanRecord,
    JobRecord,
    PlanRecord,
    ProjectRecord,
    ReviewRecord,
    SourceRecord,
    TaskRecord,
    WorkflowRecord,
    utc_now,
)
from .schemas import (
    BlockingReasonOut,
    CancelEventData,
    CreatedEventData,
    DeterministicReviewResult,
    EvidenceRelationshipOut,
    MaterializedStepOut,
    PendingApprovalOut,
    PlanEventData,
    PlanSnapshotOut,
    PlanSpec,
    ResearchWorkflowSnapshot,
    ReviewSnapshotOut,
    StatusChangedEventData,
    TaskEventData,
    WorkflowClaimOut,
    WorkflowCreateIn,
    WorkflowEventData,
    WorkflowEventOut,
    WorkflowEventsOut,
    WorkflowResultOut,
    WorkflowStateOut,
)
from .state import task_transition_allowed, workflow_transition_allowed


PLAN_HANDLER_VERSION = "template-plan-v1"
TASK_HANDLER_VERSION = "local-literature-v1"
REVIEW_HANDLER_VERSION = "deterministic-claims-v1"
MAX_JOB_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class WorkflowConflict(RuntimeError):
    code: str
    user_message: str
    retryable: bool = False

    def __str__(self) -> str:
        return self.user_message


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def content_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _model_payload(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


def workflow_create_hash(payload: WorkflowCreateIn) -> str:
    return content_sha256(_model_payload(payload))


def plan_approval_hash(plan: PlanRecord, affected_resources: Sequence[str]) -> str:
    return content_sha256(
        {
            "action": "approve-research-plan",
            "affectedResources": sorted(affected_resources),
            "planId": plan.id,
            "planSha256": plan.spec_sha256,
            "planVersion": plan.version,
            "schemaVersion": "workflow-plan-approval-v1",
            "workflowId": plan.workflow_id,
        }
    )


def assert_plan_integrity(plan: PlanRecord) -> None:
    if content_sha256(plan.spec_json) != plan.spec_sha256:
        raise WorkflowConflict(
            "plan-content-corrupt",
            "The stored plan content no longer matches its immutable hash.",
        )


def append_workflow_events(
    session: Session,
    workflow: WorkflowRecord,
    entries: Sequence[
        tuple[str, WorkflowEventData, str | None, str | None]
    ],
) -> list[EventRecord]:
    if not entries:
        return []
    sequence_result = session.execute(
        update(WorkflowRecord)
        .where(WorkflowRecord.id == workflow.id)
        .values(event_sequence=WorkflowRecord.event_sequence + len(entries))
        .returning(WorkflowRecord.event_sequence)
        .execution_options(synchronize_session=False)
    ).scalar_one()
    first_sequence = sequence_result - len(entries) + 1
    records: list[EventRecord] = []
    for offset, (event_type, data, task_id, job_id) in enumerate(entries):
        record = EventRecord(
            id=str(uuid.uuid4()),
            project_id=workflow.project_id,
            workflow_id=workflow.id,
            task_id=task_id,
            job_id=job_id,
            sequence=first_sequence + offset,
            event_type=event_type,
            payload=_model_payload(data),
        )
        records.append(record)
        session.add(record)
    return records


def transition_workflow(
    session: Session,
    workflow: WorkflowRecord,
    target: str,
    *,
    expected_revision: int | None = None,
    reason_code: str | None = None,
    blocking_message: str | None = None,
    retryable: bool = False,
) -> WorkflowRecord:
    current = workflow.status
    if current == target:
        return workflow
    if not workflow_transition_allowed(current, target):
        raise WorkflowConflict(
            "invalid-workflow-transition",
            f"Workflow cannot move from {current} to {target}.",
        )
    revision = workflow.row_version if expected_revision is None else expected_revision
    now = utc_now()
    values: dict[str, Any] = {
        "status": target,
        "row_version": WorkflowRecord.row_version + 1,
        "updated_at": now,
        "blocking_code": reason_code if target == "blocked" else None,
        "blocking_message": blocking_message if target == "blocked" else None,
    }
    if target in {"completed", "cancelled"}:
        values["finished_at"] = now
    elif current in {"failed", "blocked"}:
        values["finished_at"] = None
    result = session.execute(
        update(WorkflowRecord)
        .where(
            WorkflowRecord.id == workflow.id,
            WorkflowRecord.row_version == revision,
            WorkflowRecord.status == current,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.expire_all()
        raise WorkflowConflict(
            "workflow-revision-conflict",
            "The workflow changed before this action was applied. Reload it and try again.",
            retryable=True,
        )
    session.flush()
    session.refresh(workflow)
    append_workflow_events(
        session,
        workflow,
        [
            (
                "workflow.status-changed",
                StatusChangedEventData(
                    previous_status=current,
                    status=target,
                    reason_code=reason_code,
                ),
                None,
                None,
            )
        ],
    )
    if target == "blocked" and reason_code:
        # Retryability is intentionally present only in the public snapshot. The
        # durable blocker itself is a stable code plus user-safe message.
        workflow.last_error_code = reason_code if retryable else workflow.last_error_code
    return workflow


def transition_task(session: Session, task: TaskRecord, target: str) -> TaskRecord:
    current = task.status
    if current == target:
        return task
    if not task_transition_allowed(current, target):
        raise WorkflowConflict(
            "invalid-task-transition",
            f"Task cannot move from {current} to {target}.",
        )
    now = utc_now()
    values: dict[str, Any] = {
        "status": target,
        "row_version": TaskRecord.row_version + 1,
        "updated_at": now,
    }
    if target == "running" and task.started_at is None:
        values["started_at"] = now
    if target in {"completed", "failed", "blocked", "cancelled"}:
        values["finished_at"] = now
    if target == "queued":
        values["finished_at"] = None
    result = session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.id == task.id,
            TaskRecord.row_version == task.row_version,
            TaskRecord.status == current,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.expire_all()
        raise WorkflowConflict(
            "task-revision-conflict",
            "The workflow step changed before this action was applied.",
            retryable=True,
        )
    session.flush()
    session.refresh(task)
    return task


def _job_input_payload(
    session: Session,
    workflow: WorkflowRecord,
    *,
    kind: str,
    task: TaskRecord | None,
) -> dict[str, Any]:
    if kind == "generate-plan":
        return {
            "goalSha256": hashlib.sha256(workflow.goal.encode("utf-8")).hexdigest(),
            "handlerVersion": PLAN_HANDLER_VERSION,
            "kind": kind,
            "workflowId": workflow.id,
        }
    if kind == "review-workflow":
        answers = list(
            session.scalars(
                select(AnswerRecord)
                .where(AnswerRecord.workflow_id == workflow.id)
                .order_by(AnswerRecord.created_at)
            )
        )
        claims: list[dict[str, Any]] = []
        for answer in answers:
            for claim in session.scalars(
                select(ClaimRecord).where(ClaimRecord.answer_id == answer.id)
            ):
                links = list(
                    session.scalars(
                        select(ClaimEvidenceRecord).where(
                            ClaimEvidenceRecord.claim_id == claim.id
                        )
                    )
                )
                evidence_inputs: list[dict[str, Any]] = []
                for link in links:
                    evidence = session.get(EvidenceSpanRecord, link.evidence_id)
                    evidence_inputs.append(
                        {
                            "evidenceId": link.evidence_id,
                            "relationship": link.relationship_kind,
                            "sourceId": evidence.source_id if evidence is not None else None,
                            "pageIndex": evidence.page_index if evidence is not None else None,
                            "textSha256": hashlib.sha256(evidence.text.encode("utf-8")).hexdigest()
                            if evidence is not None
                            else None,
                            "quoteHash": evidence.quote_hash if evidence is not None else None,
                            "verified": evidence.verified if evidence is not None else None,
                        }
                    )
                claims.append(
                    {
                        "claimId": claim.id,
                        "statementSha256": hashlib.sha256(
                            claim.statement.encode("utf-8")
                        ).hexdigest(),
                        "evidence": sorted(evidence_inputs, key=lambda item: item["evidenceId"]),
                    }
                )
        return {
            "claims": claims,
            "handlerVersion": REVIEW_HANDLER_VERSION,
            "kind": kind,
            "workflowId": workflow.id,
        }
    if task is None:
        raise ValueError("execute-task jobs require a task")
    previous = list(
        session.scalars(
            select(TaskRecord)
            .where(
                TaskRecord.workflow_id == workflow.id,
                TaskRecord.plan_id == task.plan_id,
                TaskRecord.order_index < task.order_index,
            )
            .order_by(TaskRecord.order_index)
        )
    )
    return {
        "handlerVersion": TASK_HANDLER_VERSION,
        "kind": kind,
        "previousOutputs": [item.outputs for item in previous],
        "taskId": task.id,
        "taskInputSha256": task.input_sha256,
        "taskType": task.task_type,
        "workflowId": workflow.id,
    }


def current_job_input_hash(
    session: Session,
    workflow: WorkflowRecord,
    *,
    kind: str,
    task: TaskRecord | None,
) -> str:
    return content_sha256(_job_input_payload(session, workflow, kind=kind, task=task))


def enqueue_job(
    session: Session,
    workflow: WorkflowRecord,
    *,
    kind: str,
    operation_key: str,
    task: TaskRecord | None = None,
    attempt: int = 1,
    previous_job_id: str | None = None,
    request_idempotency_key: str | None = None,
    delay_seconds: float = 0,
) -> JobRecord:
    input_hash = current_job_input_hash(session, workflow, kind=kind, task=task)
    handler_version = {
        "generate-plan": PLAN_HANDLER_VERSION,
        "execute-task": TASK_HANDLER_VERSION,
        "review-workflow": REVIEW_HANDLER_VERSION,
    }[kind]
    existing = session.scalar(
        select(JobRecord).where(
            JobRecord.operation_key == operation_key,
            JobRecord.attempt == attempt,
        )
    )
    if existing is not None:
        if existing.input_sha256 != input_hash:
            raise WorkflowConflict(
                "job-input-conflict",
                "A job with this identity already exists for different inputs.",
            )
        return existing
    job = JobRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        task_id=task.id if task is not None else None,
        kind=kind,
        operation_key=operation_key,
        attempt=attempt,
        input_sha256=input_hash,
        handler_version=handler_version,
        status="queued",
        available_at=utc_now() + timedelta(seconds=max(0, delay_seconds)),
        request_idempotency_key=request_idempotency_key,
        previous_job_id=previous_job_id,
    )
    session.add(job)
    session.flush()
    return job


def start_workflow(
    session: Session,
    project: ProjectRecord,
    payload: WorkflowCreateIn,
    idempotency_key: str,
) -> WorkflowRecord:
    payload_hash = workflow_create_hash(payload)
    existing = session.scalar(
        select(WorkflowRecord).where(
            WorkflowRecord.project_id == project.id,
            WorkflowRecord.create_idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.create_payload_sha256 != payload_hash:
            raise WorkflowConflict(
                "idempotency-key-reused",
                "This Idempotency-Key was already used with a different workflow request.",
            )
        return existing
    workflow = WorkflowRecord(
        id=str(uuid.uuid4()),
        project_id=project.id,
        create_idempotency_key=idempotency_key,
        create_payload_sha256=payload_hash,
        workflow_type=payload.workflow_type,
        goal=payload.goal,
        status="planning",
        row_version=1,
        event_sequence=0,
    )
    session.add(workflow)
    session.flush()
    enqueue_job(
        session,
        workflow,
        kind="generate-plan",
        operation_key=f"workflow:{workflow.id}:plan:1",
    )
    append_workflow_events(
        session,
        workflow,
        [
            (
                "workflow.created",
                CreatedEventData(
                    workflow_type="literature-synthesis",
                    goal_sha256=hashlib.sha256(workflow.goal.encode("utf-8")).hexdigest(),
                ),
                None,
                None,
            )
        ],
    )
    session.commit()
    session.refresh(workflow)
    return workflow


def materialize_plan_tasks(
    session: Session, workflow: WorkflowRecord, plan: PlanRecord
) -> list[TaskRecord]:
    assert_plan_integrity(plan)
    existing = list(
        session.scalars(
            select(TaskRecord)
            .where(TaskRecord.plan_id == plan.id)
            .order_by(TaskRecord.order_index)
        )
    )
    if existing:
        return existing
    spec = PlanSpec.model_validate(plan.spec_json)
    tasks: list[TaskRecord] = []
    permissions_by_type: dict[str, list[str]] = {
        "inspect-sources": ["project-sources:read"],
        "extract-local-evidence": ["source-pages:read", "evidence:write"],
        "synthesize-extractive-claims": ["evidence:read", "claims:write"],
    }
    for order_index, step in enumerate(spec.steps):
        inputs = _model_payload(step.inputs)
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
            expected_outputs=list(step.expected_outputs),
            outputs={},
            acceptance_criteria=list(step.acceptance_criteria),
            permissions=permissions_by_type[step.type],
            risk_level="low",
            status="pending",
            row_version=1,
            retries=0,
            timeout_seconds=120,
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
    assert_plan_integrity(plan)
    if plan.spec_sha256 != plan_sha256:
        raise WorkflowConflict(
            "plan-hash-mismatch",
            "The displayed plan no longer matches the stored plan.",
        )
    expected_approval_hash = plan_approval_hash(plan, approval.affected_resources)
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
    if approval_result.rowcount != 1:
        raise WorkflowConflict(
            "approval-revision-conflict",
            "The approval changed before the decision was recorded.",
            retryable=True,
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
    return workflow


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
        if result.rowcount != 1:
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
                TaskRecord.status.in_(["pending", "queued", "running", "blocked", "failed"]),
            )
            .values(status="cancelled", finished_at=now, updated_at=now)
        )
        transition_workflow(session, workflow, "cancelled")
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
    if latest_job.input_sha256 != current_hash:
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
    if workflow.status != "blocked" or workflow.blocking_code != "no-ready-pdf":
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
    current_hash = current_job_input_hash(
        session, workflow, kind="execute-task", task=blocked_task
    )
    if current_hash != failed_job.input_sha256:
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


def _task_output_summary(task: TaskRecord) -> str | None:
    if not task.outputs:
        return None
    for key, noun in (
        ("sourceIds", "source"),
        ("evidenceIds", "evidence passage"),
        ("claimIds", "claim"),
    ):
        value = task.outputs.get(key)
        if isinstance(value, list):
            suffix = "" if len(value) == 1 else "s"
            return f"{len(value)} {noun}{suffix}"
    return "Output recorded"


def _allowed_actions(
    workflow: WorkflowRecord,
    pending_approvals: Sequence[ApprovalRecord],
    jobs: Sequence[JobRecord],
) -> list[str]:
    if workflow.cancel_requested_at is not None and workflow.status != "cancelled":
        return []
    actions: list[str] = []
    if workflow.status == "waiting-plan-approval" and pending_approvals:
        actions.append("approve-plan")
    if workflow.status not in {"completed", "cancelled"}:
        actions.append("cancel")
    if workflow.status == "failed" and any(job.status == "failed" for job in jobs):
        actions.append("retry")
    if workflow.status == "blocked" and workflow.blocking_code == "no-ready-pdf":
        actions.append("resume")
    return actions


def _result_snapshot(session: Session, workflow: WorkflowRecord) -> WorkflowResultOut | None:
    answer = session.scalar(
        select(AnswerRecord)
        .where(AnswerRecord.workflow_id == workflow.id)
        .order_by(AnswerRecord.created_at.desc())
    )
    if answer is None:
        return None
    claims = list(
        session.scalars(
            select(ClaimRecord)
            .where(ClaimRecord.answer_id == answer.id)
            .order_by(ClaimRecord.id)
        )
    )
    claim_outputs: list[WorkflowClaimOut] = []
    for claim in claims:
        links = list(
            session.scalars(
                select(ClaimEvidenceRecord).where(ClaimEvidenceRecord.claim_id == claim.id)
            )
        )
        evidence_outputs: list[EvidenceRelationshipOut] = []
        for link in links:
            evidence = session.get(EvidenceSpanRecord, link.evidence_id)
            if evidence is None:
                continue
            relationship = (
                "contradicting" if link.relationship_kind == "contradicting" else "supporting"
            )
            evidence_outputs.append(
                EvidenceRelationshipOut(
                    evidence_id=evidence.id,
                    source_id=evidence.source_id,
                    page_index=evidence.page_index,
                    page_label=evidence.page_label,
                    text=evidence.text,
                    bbox=evidence.bbox,
                    coordinate_space=evidence.coordinate_space,
                    quote_hash=evidence.quote_hash,
                    extraction_method=evidence.extraction_method,
                    confidence=evidence.confidence,
                    verified=evidence.verified,
                    relationship=relationship,
                )
            )
        support_status = {
            "verified": "supported",
            "rejected": "contradicted",
        }.get(claim.review_status, "insufficient-evidence")
        claim_outputs.append(
            WorkflowClaimOut(
                id=claim.id,
                statement=claim.statement,
                support_status=support_status,
                confidence=claim.confidence,
                evidence=evidence_outputs,
            )
        )
    return WorkflowResultOut(
        answer_id=answer.id,
        summary=answer.answer,
        claims=claim_outputs,
        unresolved_questions=answer.unresolved_questions,
    )


def workflow_snapshot(session: Session, workflow: WorkflowRecord) -> ResearchWorkflowSnapshot:
    plan = session.scalar(
        select(PlanRecord)
        .where(PlanRecord.workflow_id == workflow.id)
        .order_by(PlanRecord.version.desc())
    )
    tasks = list(
        session.scalars(
            select(TaskRecord)
            .where(TaskRecord.workflow_id == workflow.id)
            .order_by(TaskRecord.order_index)
        )
    )
    jobs = list(
        session.scalars(
            select(JobRecord)
            .where(JobRecord.workflow_id == workflow.id)
            .order_by(JobRecord.created_at)
        )
    )
    approvals = list(
        session.scalars(
            select(ApprovalRecord)
            .where(
                ApprovalRecord.workflow_id == workflow.id,
                ApprovalRecord.user_decision.is_(None),
            )
            .order_by(ApprovalRecord.created_at)
        )
    )
    review = session.scalar(
        select(ReviewRecord)
        .where(ReviewRecord.workflow_id == workflow.id)
        .order_by(ReviewRecord.created_at.desc())
    )
    current_task = next(
        (task for task in tasks if task.status in {"running", "queued"}),
        next((task for task in tasks if task.status == "pending"), None),
    )
    retry_count = sum(max(0, job.attempt - 1) for job in jobs)
    blocker = (
        BlockingReasonOut(
            code=workflow.blocking_code or "blocked",
            user_message=workflow.blocking_message or "The workflow is blocked.",
            retryable=workflow.blocking_code == "no-ready-pdf",
        )
        if workflow.status == "blocked"
        else None
    )
    plan_out = None
    if plan is not None:
        plan_tasks = [task for task in tasks if task.plan_id == plan.id]
        plan_out = PlanSnapshotOut(
            id=plan.id,
            workflow_id=plan.workflow_id,
            version=plan.version,
            status=plan.status,
            plan_sha256=plan.spec_sha256,
            spec=PlanSpec.model_validate(plan.spec_json),
            steps=[
                MaterializedStepOut(
                    id=task.id,
                    key=task.step_key or "",
                    order_index=task.order_index or 0,
                    type=task.task_type,
                    objective=task.objective,
                    status=task.status,
                    retry_count=task.retries,
                    started_at=task.started_at,
                    completed_at=task.finished_at,
                    output_summary=_task_output_summary(task),
                )
                for task in plan_tasks
            ],
            created_at=plan.created_at,
            approved_at=plan.approved_at,
        )
    review_out = None
    if review is not None:
        review_out = ReviewSnapshotOut(
            id=review.id,
            review_type=review.review_type,
            verdict=review.verdict,
            input_sha256=review.input_sha256,
            result=DeterministicReviewResult.model_validate(review.result_json),
            created_at=review.created_at,
        )
    return ResearchWorkflowSnapshot(
        workflow=WorkflowStateOut(
            id=workflow.id,
            project_id=workflow.project_id,
            workflow_type="literature-synthesis",
            goal=workflow.goal,
            status=workflow.status,
            revision=workflow.row_version,
            plan_version=plan.version if plan is not None else None,
            current_step_id=current_task.id if current_task is not None else None,
            retry_count=retry_count,
            blocking_reason=blocker,
            cancel_requested_at=workflow.cancel_requested_at,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
            completed_at=workflow.finished_at,
        ),
        plan=plan_out,
        pending_approvals=[
            PendingApprovalOut(
                id=approval.id,
                workflow_id=workflow.id,
                plan_id=approval.plan_id or "",
                task_id=approval.task_id,
                kind="plan",
                status="waiting",
                subject_type=approval.subject_type or "plan",
                subject_id=approval.subject_id or approval.plan_id or "",
                action=approval.requested_action,
                payload_sha256=approval.intent_hash,
                risk_level=approval.risk_level,
                reason=approval.reason,
                affected_resources=approval.affected_resources,
                created_at=approval.created_at,
                decided_at=approval.decided_at,
            )
            for approval in approvals
        ],
        result=_result_snapshot(session, workflow),
        latest_review=review_out,
        allowed_actions=_allowed_actions(workflow, approvals, jobs),
        event_cursor=workflow.event_sequence,
    )


def list_workflows(
    session: Session,
    project_id: str,
    *,
    active_only: bool,
    limit: int,
) -> list[WorkflowRecord]:
    query: Select[tuple[WorkflowRecord]] = select(WorkflowRecord).where(
        WorkflowRecord.project_id == project_id
    )
    if active_only:
        query = query.where(WorkflowRecord.status.not_in(["completed", "cancelled"]))
    return list(
        session.scalars(query.order_by(WorkflowRecord.updated_at.desc()).limit(limit))
    )


def workflow_events(
    session: Session,
    workflow: WorkflowRecord,
    *,
    after: int,
    limit: int,
) -> WorkflowEventsOut:
    records = list(
        session.scalars(
            select(EventRecord)
            .where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.sequence > after,
            )
            .order_by(EventRecord.sequence)
            .limit(limit + 1)
        )
    )
    has_more = len(records) > limit
    page = records[:limit]
    events = [
        WorkflowEventOut(
            id=record.id,
            sequence=record.sequence or 0,
            type=record.event_type,
            task_id=record.task_id,
            job_id=record.job_id,
            data=record.payload,
            created_at=record.created_at,
        )
        for record in page
    ]
    return WorkflowEventsOut(
        events=events,
        next_after=events[-1].sequence if events else after,
        has_more=has_more,
    )


def latest_active_job(session: Session, workflow_id: str) -> JobRecord | None:
    return session.scalar(
        select(JobRecord)
        .where(
            JobRecord.workflow_id == workflow_id,
            JobRecord.status.in_(["queued", "leased"]),
        )
        .order_by(JobRecord.created_at.desc())
    )


def retry_delay_seconds(attempt: int) -> float:
    return float(min(30, 2 ** max(0, attempt - 1)))
