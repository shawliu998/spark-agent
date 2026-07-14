from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ValidationError
from sqlalchemy import Select, select, update
from sqlalchemy.orm import Session

from ..analysis import sha256_file
from ..model_gateway import model_gateway
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
    SourcePageRecord,
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
    FrozenSourceDescriptor,
    MaterializedStepOut,
    PendingApprovalOut,
    PlanEventData,
    PlanSnapshotOut,
    PlanSpec,
    ResearchWorkflowSnapshot,
    RemoteDataApprovalEventData,
    ReviewEventData,
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


PLAN_HANDLER_VERSION = "research-plan-v2"
TASK_HANDLER_VERSION = "literature-synthesis-v2"
REVIEW_HANDLER_VERSION = "deterministic-claims-v2"
LEGACY_HANDLER_VERSIONS = {
    "generate-plan": "template-plan-v1",
    "execute-task": "local-literature-v1",
    "review-workflow": "deterministic-claims-v1",
}
MAX_JOB_ATTEMPTS = 3
LOCAL_PLAN_APPROVAL_REASON = (
    "Approve the displayed immutable local literature plan before it runs."
)
REMOTE_PASSAGE_APPROVAL_REASON = (
    "Approve this immutable plan and authorize sending selected-source-passages "
    "only from the listed frozen PDF sources to the configured remote model "
    "during synthesis."
)

TASK_PERMISSIONS_BY_TYPE: dict[str, list[str]] = {
    "inspect-sources": ["project-sources:read"],
    "extract-local-evidence": ["source-pages:read", "evidence:write"],
    "synthesize-extractive-claims": ["evidence:read", "claims:write"],
}


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


def plan_approval_hash(
    plan: PlanRecord,
    affected_resources: Sequence[str],
    *,
    schema_version: str = "workflow-plan-approval-v1",
    workflow_goal: str | None = None,
    risk_level: str | None = None,
    reason: str | None = None,
    subject_id: str | None = None,
    task_id: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "action": "approve-research-plan",
        "affectedResources": sorted(affected_resources),
        "planId": plan.id,
        "planSha256": plan.spec_sha256,
        "planVersion": plan.version,
        "schemaVersion": schema_version,
        "workflowId": plan.workflow_id,
    }
    if schema_version == "workflow-plan-approval-v2":
        if workflow_goal is None or risk_level is None or reason is None:
            raise ValueError(
                "workflow_goal, risk_level, and reason are required for a v2 approval hash"
            )
        payload.update(
            {
                "goalSha256": hashlib.sha256(
                    workflow_goal.encode("utf-8")
                ).hexdigest(),
                "planGenerator": plan.generator,
                "planModel": plan.model,
                "planPromptVersion": plan.prompt_version,
                "reason": reason,
                "requestedAction": "approve-research-plan",
                "riskLevel": risk_level,
                "subjectId": subject_id or plan.id,
                "subjectType": "plan",
                "taskId": task_id,
            }
        )
    elif schema_version != "workflow-plan-approval-v1":
        raise ValueError("unsupported workflow plan approval schema")
    return content_sha256(payload)


def expected_plan_approval_semantics(
    session: Session,
    workflow: WorkflowRecord,
    plan: PlanRecord,
) -> tuple[str, str, list[str]]:
    resources = [f"project:{workflow.project_id}"]
    if workflow.generation_mode == "local-deterministic":
        return "low", LOCAL_PLAN_APPROVAL_REASON, resources
    try:
        spec = PlanSpec.model_validate(plan.spec_json)
        frozen_sources = spec.steps[0].inputs.frozen_sources
    except (AttributeError, ValidationError):
        frozen_sources = None
    if not frozen_sources:
        raise WorkflowConflict(
            "remote-source-approval-missing",
            "The remote plan has no immutable source descriptors for approval.",
        )
    approval_event = session.scalar(
        select(EventRecord)
        .where(
            EventRecord.workflow_id == workflow.id,
            EventRecord.event_type == "remote-data.approved",
        )
        .order_by(EventRecord.sequence)
    )
    recorded_destination = approval_event.payload if approval_event is not None else {}
    if (
        recorded_destination.get("provider") != "openai-compatible"
        or recorded_destination.get("model") != plan.model
        or recorded_destination.get("dataCategories") != ["user-goal"]
    ):
        raise WorkflowConflict(
            "remote-gateway-approval-mismatch",
            "The remote plan destination differs from its recorded data approval.",
        )
    resources.extend(
        [
            f"remote-endpoint-host:{recorded_destination.get('endpointHost')}",
            "remote-endpoint-identity:"
            f"{recorded_destination.get('endpointIdentity')}",
            f"remote-model:{plan.model}",
        ]
    )
    resources.extend(
        f"source:{source.source_id}:sha256:{source.content_hash}:"
        "verified-passages:remote"
        for source in frozen_sources
    )
    return "medium", REMOTE_PASSAGE_APPROVAL_REASON, resources


def assert_plan_integrity(plan: PlanRecord) -> None:
    if content_sha256(plan.spec_json) != plan.spec_sha256:
        raise WorkflowConflict(
            "plan-content-corrupt",
            "The stored plan content no longer matches its immutable hash.",
        )


def assert_plan_for_workflow(
    workflow: WorkflowRecord,
    plan: PlanRecord,
) -> PlanSpec:
    if plan.workflow_id != workflow.id:
        raise WorkflowConflict(
            "plan-ownership-invalid",
            "The workflow plan does not belong to this workflow.",
        )
    assert_plan_integrity(plan)
    try:
        spec = PlanSpec.model_validate(plan.spec_json)
    except ValidationError:
        raise WorkflowConflict(
            "plan-content-invalid",
            "The approved plan no longer matches the supported workflow schema.",
        ) from None
    if spec.goal != workflow.goal:
        raise WorkflowConflict(
            "plan-goal-mismatch",
            "The approved plan goal no longer matches the workflow goal.",
        )
    expected_provenance = (
        ("template-v1", None, "template-v1")
        if workflow.generation_mode == "local-deterministic"
        else ("remote-model-assisted-v1", plan.model, "remote-plan-v1")
    )
    if (
        (plan.generator, plan.model, plan.prompt_version) != expected_provenance
        or (
            workflow.generation_mode == "remote-model-assisted"
            and not plan.model
        )
    ):
        raise WorkflowConflict(
            "plan-provenance-invalid",
            "The approved plan provenance no longer matches the workflow generation mode.",
        )
    return spec


def assert_approved_plan_for_workflow(
    workflow: WorkflowRecord,
    plan: PlanRecord,
) -> PlanSpec:
    if plan.status != "approved":
        raise WorkflowConflict(
            "approved-plan-invalid",
            "The workflow step is not bound to an approved plan.",
        )
    return assert_plan_for_workflow(workflow, plan)


def assert_plan_approval_integrity(
    session: Session,
    workflow: WorkflowRecord,
    plan: PlanRecord,
) -> ApprovalRecord:
    approval = session.scalar(
        select(ApprovalRecord).where(
            ApprovalRecord.workflow_id == workflow.id,
            ApprovalRecord.plan_id == plan.id,
            ApprovalRecord.subject_type == "plan",
        )
    )
    if approval is None or approval.payload_schema_version not in {
        "workflow-plan-approval-v1",
        "workflow-plan-approval-v2",
    }:
        raise WorkflowConflict(
            "plan-approval-invalid",
            "The approved plan has no supported immutable approval record.",
        )
    expected_risk, expected_reason, expected_resources = (
        expected_plan_approval_semantics(session, workflow, plan)
    )
    if (
        approval.task_id is not None
        or approval.subject_type != "plan"
        or approval.subject_id != plan.id
        or approval.requested_action != "approve-research-plan"
        or approval.risk_level != expected_risk
        or approval.reason != expected_reason
        or approval.affected_resources != expected_resources
    ):
        raise WorkflowConflict(
            "plan-approval-semantics-invalid",
            "The displayed plan approval metadata no longer matches its fixed consent "
            "contract.",
        )
    decision_valid = (
        (plan.status == "approved" and approval.user_decision == "approved")
        or (plan.status == "pending-approval" and approval.user_decision is None)
    )
    if not decision_valid:
        raise WorkflowConflict(
            "plan-approval-state-invalid",
            "The plan status no longer matches its approval decision.",
        )
    expected_hash = plan_approval_hash(
        plan,
        approval.affected_resources,
        schema_version=approval.payload_schema_version,
        workflow_goal=(
            workflow.goal
            if approval.payload_schema_version == "workflow-plan-approval-v2"
            else None
        ),
        risk_level=(
            approval.risk_level
            if approval.payload_schema_version == "workflow-plan-approval-v2"
            else None
        ),
        reason=(
            approval.reason
            if approval.payload_schema_version == "workflow-plan-approval-v2"
            else None
        ),
        subject_id=(
            approval.subject_id
            if approval.payload_schema_version == "workflow-plan-approval-v2"
            else None
        ),
        task_id=(
            approval.task_id
            if approval.payload_schema_version == "workflow-plan-approval-v2"
            else None
        ),
    )
    if approval.intent_hash != expected_hash:
        raise WorkflowConflict(
            "approval-hash-mismatch",
            "The approved plan no longer matches its immutable approval payload.",
        )
    return approval


def _plan_job_envelope(
    session: Session,
    workflow: WorkflowRecord,
    plan: PlanRecord | None,
) -> dict[str, Any] | None:
    if plan is None:
        return None
    approval = session.scalar(
        select(ApprovalRecord).where(
            ApprovalRecord.workflow_id == workflow.id,
            ApprovalRecord.plan_id == plan.id,
            ApprovalRecord.subject_type == "plan",
        )
    )
    return {
        "approvalIntentHash": approval.intent_hash if approval is not None else None,
        "approvalSchemaVersion": (
            approval.payload_schema_version if approval is not None else None
        ),
        "goalSha256": hashlib.sha256(workflow.goal.encode("utf-8")).hexdigest(),
        "planGenerator": plan.generator,
        "planId": plan.id,
        "planModel": plan.model,
        "planPromptVersion": plan.prompt_version,
        "planSha256": plan.spec_sha256,
    }


def task_input_hash(task: TaskRecord) -> str:
    return content_sha256(
        {
            "inputs": task.inputs,
            "objective": task.objective,
            "stepKey": task.step_key,
            "stepType": task.task_type,
        }
    )


def assert_task_input_integrity(task: TaskRecord) -> None:
    if task.input_sha256 is None or task_input_hash(task) != task.input_sha256:
        raise WorkflowConflict(
            "task-input-corrupt",
            "The stored workflow step input no longer matches its immutable hash.",
        )


def task_materialization_hash(task: TaskRecord) -> str:
    return content_sha256(
        {
            "acceptanceCriteria": task.acceptance_criteria,
            "expectedOutputs": task.expected_outputs,
            "inputSha256": task.input_sha256,
            "inputs": task.inputs,
            "objective": task.objective,
            "orderIndex": task.order_index,
            "permissions": task.permissions,
            "planId": task.plan_id,
            "projectId": task.project_id,
            "riskLevel": task.risk_level,
            "stepKey": task.step_key,
            "stepType": task.task_type,
            "timeoutSeconds": task.timeout_seconds,
            "workflowId": task.workflow_id,
        }
    )


def assert_task_matches_approved_plan(
    workflow: WorkflowRecord,
    plan: PlanRecord,
    task: TaskRecord,
) -> None:
    spec = assert_approved_plan_for_workflow(workflow, plan)
    assert_task_input_integrity(task)
    if (
        task.workflow_id != workflow.id
        or task.project_id != workflow.project_id
        or task.plan_id != plan.id
        or task.order_index is None
        or task.order_index < 0
        or task.order_index >= len(spec.steps)
    ):
        raise WorkflowConflict(
            "task-plan-ownership-invalid",
            "The workflow step does not belong to the approved plan and project.",
        )
    step = spec.steps[task.order_index]
    expected_inputs = _model_payload(step.inputs)
    expected_input_sha256 = content_sha256(
        {
            "inputs": expected_inputs,
            "objective": step.objective,
            "stepKey": step.key,
            "stepType": step.type,
        }
    )
    expected_permissions = TASK_PERMISSIONS_BY_TYPE[step.type]
    if (
        task.step_key != step.key
        or task.task_type != step.type
        or task.objective != step.objective
        or task.inputs != expected_inputs
        or task.input_sha256 != expected_input_sha256
        or task.expected_outputs != list(step.expected_outputs)
        or task.acceptance_criteria != list(step.acceptance_criteria)
        or task.permissions != expected_permissions
        or task.risk_level != "low"
        or task.timeout_seconds != 120
    ):
        raise WorkflowConflict(
            "task-plan-mismatch",
            "The stored workflow step no longer matches its approved plan step.",
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
    handler_version: str | None = None,
) -> dict[str, Any]:
    selected_handler_version = handler_version or handler_version_for(kind)
    if kind == "generate-plan":
        payload = {
            "goalSha256": hashlib.sha256(workflow.goal.encode("utf-8")).hexdigest(),
            "handlerVersion": selected_handler_version,
            "kind": kind,
            "workflowId": workflow.id,
        }
        if selected_handler_version != LEGACY_HANDLER_VERSIONS["generate-plan"]:
            payload["generationMode"] = workflow.generation_mode
        return payload
    if kind == "review-workflow":
        legacy_handler = (
            selected_handler_version == LEGACY_HANDLER_VERSIONS["review-workflow"]
        )
        approved_plan = None
        if not legacy_handler:
            approved_plan = session.scalar(
                select(PlanRecord).where(
                    PlanRecord.workflow_id == workflow.id,
                    PlanRecord.status == "approved",
                )
            )
        answers = list(
            session.scalars(
                select(AnswerRecord)
                .where(AnswerRecord.workflow_id == workflow.id)
                .order_by(AnswerRecord.created_at)
            )
        )
        if not legacy_handler:
            answers.sort(key=lambda answer: (answer.created_at, answer.id))
        claims: list[dict[str, Any]] = []
        answer_inputs: list[dict[str, Any]] = []
        for answer in answers:
            answer_inputs.append(
                {
                    "answerId": answer.id,
                    "projectId": answer.project_id,
                    "questionSha256": hashlib.sha256(
                        answer.question.encode("utf-8")
                    ).hexdigest(),
                    "summarySha256": hashlib.sha256(
                        answer.answer.encode("utf-8")
                    ).hexdigest(),
                    "taskId": answer.task_id,
                    "unresolvedQuestionsSha256": content_sha256(
                        answer.unresolved_questions
                    ),
                    "generator": answer.generator,
                    "model": answer.model,
                    "promptVersion": answer.prompt_version,
                    "metadataSha256": content_sha256(answer.metadata_json),
                    "workflowId": answer.workflow_id,
                }
            )
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
                    evidence_input = {
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
                    if not legacy_handler:
                        evidence_input.update(
                            {
                                "bboxSha256": content_sha256(evidence.bbox)
                                if evidence is not None
                                else None,
                                "confidence": evidence.confidence
                                if evidence is not None
                                else None,
                                "coordinateSpace": evidence.coordinate_space
                                if evidence is not None
                                else None,
                                "extractionMethod": evidence.extraction_method
                                if evidence is not None
                                else None,
                                "pageLabel": evidence.page_label
                                if evidence is not None
                                else None,
                            }
                        )
                    evidence_inputs.append(evidence_input)
                claim_input = {
                    "claimId": claim.id,
                    "statementSha256": hashlib.sha256(
                        claim.statement.encode("utf-8")
                    ).hexdigest(),
                    "evidence": sorted(
                        evidence_inputs,
                        key=lambda item: item["evidenceId"],
                    ),
                }
                if not legacy_handler:
                    claim_input.update(
                        {
                            "claimType": claim.claim_type,
                            "confidence": claim.confidence,
                            "reviewStatus": claim.review_status,
                        }
                    )
                claims.append(claim_input)
        if not legacy_handler:
            claims.sort(key=lambda claim: claim["claimId"])
        payload = {
            "claims": claims,
            "handlerVersion": selected_handler_version,
            "kind": kind,
            "workflowId": workflow.id,
        }
        if not legacy_handler:
            payload["answers"] = answer_inputs
            payload["planEnvelope"] = _plan_job_envelope(
                session,
                workflow,
                approved_plan,
            )
        return payload
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
    payload = {
        "handlerVersion": selected_handler_version,
        "kind": kind,
        "previousOutputs": [item.outputs for item in previous],
        "taskId": task.id,
        "taskInputSha256": task.input_sha256,
        "taskType": task.task_type,
        "workflowId": workflow.id,
    }
    if selected_handler_version != LEGACY_HANDLER_VERSIONS["execute-task"]:
        plan = session.get(PlanRecord, task.plan_id) if task.plan_id is not None else None
        payload.update(
            {
                "planEnvelope": _plan_job_envelope(session, workflow, plan),
                "taskMaterializationSha256": task_materialization_hash(task),
            }
        )
    return payload


def current_job_input_hash(
    session: Session,
    workflow: WorkflowRecord,
    *,
    kind: str,
    task: TaskRecord | None,
) -> str:
    return content_sha256(_job_input_payload(session, workflow, kind=kind, task=task))


def handler_version_for(kind: str) -> str:
    return {
        "generate-plan": PLAN_HANDLER_VERSION,
        "execute-task": TASK_HANDLER_VERSION,
        "review-workflow": REVIEW_HANDLER_VERSION,
    }[kind]


def job_input_hash_for_handler_version(
    session: Session,
    workflow: WorkflowRecord,
    *,
    kind: str,
    task: TaskRecord | None,
    handler_version: str,
) -> str:
    return content_sha256(
        _job_input_payload(
            session,
            workflow,
            kind=kind,
            task=task,
            handler_version=handler_version,
        )
    )


def job_input_compatibility(
    session: Session,
    workflow: WorkflowRecord,
    job: JobRecord,
    task: TaskRecord | None,
) -> str | None:
    current_version = handler_version_for(job.kind)
    if job.handler_version == current_version:
        expected_hash = current_job_input_hash(
            session, workflow, kind=job.kind, task=task
        )
        return "current" if job.input_sha256 == expected_hash else None
    legacy_version = LEGACY_HANDLER_VERSIONS.get(job.kind)
    if (
        legacy_version is None
        or job.handler_version != legacy_version
        or workflow.generation_mode != "local-deterministic"
    ):
        return None
    expected_hash = job_input_hash_for_handler_version(
        session,
        workflow,
        kind=job.kind,
        task=task,
        handler_version=legacy_version,
    )
    return "legacy" if job.input_sha256 == expected_hash else None


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
    handler_version: str | None = None,
) -> JobRecord:
    selected_handler_version = handler_version or handler_version_for(kind)
    allowed_versions = {
        handler_version_for(kind),
        LEGACY_HANDLER_VERSIONS[kind],
    }
    if selected_handler_version not in allowed_versions:
        raise WorkflowConflict(
            "unsupported-handler-version",
            "The workflow job handler version is not supported.",
        )
    if (
        selected_handler_version == LEGACY_HANDLER_VERSIONS[kind]
        and workflow.generation_mode != "local-deterministic"
    ):
        raise WorkflowConflict(
            "legacy-handler-mode-invalid",
            "Previous workflow handlers may only resume local deterministic workflows.",
        )
    input_hash = job_input_hash_for_handler_version(
        session,
        workflow,
        kind=kind,
        task=task,
        handler_version=selected_handler_version,
    )
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
        handler_version=selected_handler_version,
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
    if (
        payload.generation_mode == "remote-model-assisted"
        and not model_gateway.configured
    ):
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
        goal=payload.goal,
        generation_mode=payload.generation_mode,
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
    events: list[tuple[str, WorkflowEventData, str | None, str | None]] = [
        (
            "workflow.created",
            CreatedEventData(
                workflow_type="literature-synthesis",
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
                    endpoint_host=model_gateway.endpoint_host,
                    endpoint_identity=model_gateway.endpoint_identity,
                    model=model_gateway.default_model,
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


def materialize_plan_tasks(
    session: Session, workflow: WorkflowRecord, plan: PlanRecord
) -> list[TaskRecord]:
    assert_plan_for_workflow(workflow, plan)
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
            permissions=TASK_PERMISSIONS_BY_TYPE[step.type],
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
            if approval.payload_schema_version == "workflow-plan-approval-v2"
            else None
        ),
        risk_level=(
            approval.risk_level
            if approval.payload_schema_version == "workflow-plan-approval-v2"
            else None
        ),
        reason=(
            approval.reason
            if approval.payload_schema_version == "workflow-plan-approval-v2"
            else None
        ),
        subject_id=(
            approval.subject_id
            if approval.payload_schema_version == "workflow-plan-approval-v2"
            else None
        ),
        task_id=(
            approval.task_id
            if approval.payload_schema_version == "workflow-plan-approval-v2"
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
    session.refresh(plan)
    session.refresh(approval)
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
    if (
        current_hash != failed_job.input_sha256
        and job_input_compatibility(session, workflow, failed_job, blocked_task)
        != "legacy"
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


def _result_source_descriptors(
    session: Session,
    workflow: WorkflowRecord,
) -> list[FrozenSourceDescriptor]:
    inspect_task = session.scalar(
        select(TaskRecord).where(
            TaskRecord.workflow_id == workflow.id,
            TaskRecord.order_index == 0,
        )
    )
    raw_descriptors = (
        inspect_task.outputs.get("sourceDescriptors")
        if inspect_task is not None
        else None
    )
    if not isinstance(raw_descriptors, list):
        return []
    try:
        return [
            FrozenSourceDescriptor.model_validate(item)
            for item in raw_descriptors
        ]
    except ValidationError:
        return []


def _source_page_manifest_hash(
    session: Session,
    source_id: str,
) -> tuple[str, int] | None:
    pages = list(
        session.scalars(
            select(SourcePageRecord)
            .where(SourcePageRecord.source_id == source_id)
            .order_by(SourcePageRecord.page_index)
        )
    )
    if not pages:
        return None
    return (
        content_sha256(
            [
                {
                    "height": page.height,
                    "pageIndex": page.page_index,
                    "pageLabel": page.page_label,
                    "text": page.text,
                    "width": page.width,
                    "words": page.words,
                }
                for page in pages
            ]
        ),
        len(pages),
    )


def _assert_result_sources_current(
    session: Session,
    workflow: WorkflowRecord,
    descriptors: list[FrozenSourceDescriptor],
) -> None:
    project = session.get(ProjectRecord, workflow.project_id)
    if (
        project is None
        or not descriptors
        or len({item.source_id for item in descriptors}) != len(descriptors)
    ):
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The reviewed result no longer has a verifiable immutable source set.",
        )
    project_root = Path(project.project_path).resolve()
    for descriptor in descriptors:
        source = session.get(SourceRecord, descriptor.source_id)
        page_manifest = (
            _source_page_manifest_hash(session, descriptor.source_id)
            if source is not None
            else None
        )
        file_matches = False
        if source is not None:
            raw_path = Path(source.local_path)
            if not raw_path.is_symlink():
                try:
                    path = raw_path.resolve(strict=True)
                    path.relative_to(project_root)
                    file_matches = (
                        path.is_file()
                        and sha256_file(path) == descriptor.content_hash
                    )
                except (OSError, ValueError):
                    file_matches = False
        if not (
            source is not None
            and source.project_id == workflow.project_id
            and source.source_kind == "pdf"
            and source.ingestion_status == "ready"
            and source.title == descriptor.title
            and source.content_hash == descriptor.content_hash
            and page_manifest is not None
            and page_manifest[0] == descriptor.page_manifest_hash
            and source.page_count in {None, page_manifest[1]}
            and file_matches
        ):
            raise WorkflowConflict(
                "workflow-result-integrity-failed",
                "A reviewed citation source no longer matches its frozen file and page "
                "fingerprints.",
            )


def build_workflow_result(
    session: Session,
    workflow: WorkflowRecord,
    *,
    integrity_status: Literal["verified-frozen-v2", "unfrozen"] = "unfrozen",
    review_completed: bool = False,
) -> WorkflowResultOut | None:
    answer = session.scalar(
        select(AnswerRecord)
        .where(AnswerRecord.workflow_id == workflow.id)
        .order_by(AnswerRecord.created_at.desc())
    )
    if answer is None:
        return None
    source_descriptors = {
        descriptor.source_id: descriptor
        for descriptor in _result_source_descriptors(session, workflow)
    }
    claims = list(
        session.scalars(
            select(ClaimRecord)
            .where(ClaimRecord.answer_id == answer.id)
            .order_by(ClaimRecord.id)
        )
    )
    claim_order = answer.metadata_json.get("claimOrder")
    if isinstance(claim_order, list):
        ordered_ids = [item for item in claim_order if isinstance(item, str)]
        claims_by_id = {claim.id: claim for claim in claims}
        if (
            len(ordered_ids) == len(claims)
            and len(set(ordered_ids)) == len(ordered_ids)
            and set(ordered_ids) == set(claims_by_id)
        ):
            claims = [claims_by_id[claim_id] for claim_id in ordered_ids]
    claim_outputs: list[WorkflowClaimOut] = []
    for claim in claims:
        links = list(
            session.scalars(
                select(ClaimEvidenceRecord)
                .where(ClaimEvidenceRecord.claim_id == claim.id)
                .order_by(
                    ClaimEvidenceRecord.evidence_id,
                    ClaimEvidenceRecord.relationship_kind,
                )
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
            descriptor = source_descriptors.get(evidence.source_id)
            evidence_outputs.append(
                EvidenceRelationshipOut(
                    evidence_id=evidence.id,
                    source_id=evidence.source_id,
                    source_title=(descriptor.title if descriptor is not None else None),
                    source_content_hash=(
                        descriptor.content_hash if descriptor is not None else None
                    ),
                    source_page_manifest_hash=(
                        descriptor.page_manifest_hash if descriptor is not None else None
                    ),
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
        }.get(
            claim.review_status,
            "insufficient-evidence" if review_completed else "pending-review",
        )
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
        generator=answer.generator,
        model=answer.model,
        prompt_version=answer.prompt_version,
        integrity_status=integrity_status,
        claims=claim_outputs,
        unresolved_questions=answer.unresolved_questions,
    )


def workflow_result_hash(result: WorkflowResultOut) -> str:
    return content_sha256(
        result.model_dump(mode="json", by_alias=True, exclude_none=False)
    )


def _validated_review_result(
    session: Session,
    workflow: WorkflowRecord,
    plan: PlanRecord | None,
    review: ReviewRecord | None,
) -> DeterministicReviewResult | None:
    if review is None:
        if workflow.status == "completed":
            raise WorkflowConflict(
                "workflow-result-integrity-failed",
                "The completed workflow has no deterministic review result.",
            )
        return None
    try:
        result = DeterministicReviewResult.model_validate(review.result_json)
    except ValidationError:
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The stored deterministic review result is invalid.",
        ) from None
    expected_schema = {
        "deterministic-claims-v1": "1",
        "deterministic-claims-v2": "2",
    }.get(review.review_type)
    if expected_schema is None or result.schema_version != expected_schema:
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The deterministic review type does not match its result schema.",
        )
    if review.verdict != result.verdict:
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The deterministic review verdict does not match its stored result.",
        )
    if workflow.status == "completed" and result.verdict != "passed":
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The completed workflow is not bound to a passed deterministic review.",
        )
    if (
        plan is None
        or review.plan_id != plan.id
        or plan.workflow_id != workflow.id
        or plan.status != "approved"
    ):
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The deterministic review is not bound to the approved workflow plan.",
        )
    matching_events = [
        event
        for event in session.scalars(
            select(EventRecord).where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.event_type == "review.completed",
            )
        )
        if isinstance(event.payload, dict)
        and event.payload.get("reviewId") == review.id
    ]
    if len(matching_events) != 1:
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The deterministic review has no unique completion event.",
        )
    completion_event = matching_events[0]
    try:
        completion_data = ReviewEventData.model_validate(completion_event.payload)
    except ValidationError:
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The deterministic review completion event is invalid.",
        ) from None
    expected_handler = {
        "1": LEGACY_HANDLER_VERSIONS["review-workflow"],
        "2": REVIEW_HANDLER_VERSION,
    }[result.schema_version]
    review_job = (
        session.get(JobRecord, completion_event.job_id)
        if completion_event.job_id is not None
        else None
    )
    if (
        completion_event.task_id is not None
        or completion_data.verdict != review.verdict
        or review_job is None
        or review_job.workflow_id != workflow.id
        or review_job.kind != "review-workflow"
        or review_job.task_id is not None
        or review_job.status != "succeeded"
        or review_job.handler_version != expected_handler
        or review_job.input_sha256 != review.input_sha256
    ):
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The deterministic review does not match its completed execution job.",
        )
    approval = session.scalar(
        select(ApprovalRecord).where(
            ApprovalRecord.workflow_id == workflow.id,
            ApprovalRecord.plan_id == plan.id,
            ApprovalRecord.subject_type == "plan",
        )
    )
    expected_approval_schema = (
        "workflow-plan-approval-v1"
        if result.schema_version == "1"
        else "workflow-plan-approval-v2"
    )
    if approval is None or approval.payload_schema_version != expected_approval_schema:
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The deterministic review schema does not match its plan approval provenance.",
        )
    if result.schema_version == "1":
        creation_events = list(
            session.scalars(
                select(EventRecord).where(
                    EventRecord.workflow_id == workflow.id,
                    EventRecord.event_type == "workflow.created",
                )
            )
        )
        approval_events = [
            event
            for event in session.scalars(
                select(EventRecord).where(
                    EventRecord.workflow_id == workflow.id,
                    EventRecord.event_type == "approval.requested",
                )
            )
            if isinstance(event.payload, dict)
            and event.payload.get("approvalId") == approval.id
        ]
        inspect_inputs = plan.spec_json.get("steps", [{}])[0].get("inputs", {})
        legacy_jobs = list(
            session.scalars(
                select(JobRecord).where(JobRecord.workflow_id == workflow.id)
            )
        )
        try:
            expected_review_hash = job_input_hash_for_handler_version(
                session,
                workflow,
                kind="review-workflow",
                task=None,
                handler_version=LEGACY_HANDLER_VERSIONS["review-workflow"],
            )
        except (AttributeError, TypeError, ValueError):
            expected_review_hash = None
        if (
            workflow.generation_mode != "local-deterministic"
            or len(creation_events) != 1
            or not isinstance(creation_events[0].payload, dict)
            or "generationMode" in creation_events[0].payload
            or len(approval_events) != 1
            or any(
                key in approval_events[0].payload
                for key in {
                    "riskLevel",
                    "reason",
                    "affectedResources",
                    "approvalSchemaVersion",
                }
            )
            or not isinstance(inspect_inputs, dict)
            or "sourceIds" in inspect_inputs
            or "frozenSources" in inspect_inputs
            or any(
                job.handler_version != LEGACY_HANDLER_VERSIONS.get(job.kind)
                for job in legacy_jobs
            )
            or review_job.input_sha256 != expected_review_hash
        ):
            raise WorkflowConflict(
                "workflow-result-integrity-failed",
                "The schema 1 review has no complete legacy execution provenance.",
            )
    return result


def _reviewed_result_snapshot(
    session: Session,
    workflow: WorkflowRecord,
    review: ReviewRecord | None,
    review_result: DeterministicReviewResult | None,
) -> WorkflowResultOut | None:
    if review is None:
        return build_workflow_result(
            session,
            workflow,
            review_completed=False,
        )
    if review_result is None:
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The stored deterministic review result is unavailable.",
        )
    if review_result.verdict != "passed":
        return build_workflow_result(
            session,
            workflow,
            review_completed=True,
        )
    if review_result.schema_version == "1":
        return build_workflow_result(
            session,
            workflow,
            review_completed=True,
        )
    live_result = build_workflow_result(
        session,
        workflow,
        integrity_status="verified-frozen-v2",
        review_completed=True,
    )
    frozen_result = review_result.result_snapshot
    frozen_hash = review_result.result_snapshot_sha256
    if (
        frozen_result is None
        or frozen_hash is None
        or workflow_result_hash(frozen_result) != frozen_hash
        or live_result is None
        or workflow_result_hash(live_result) != frozen_hash
    ):
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The published workflow result changed after deterministic review.",
        )
    descriptor_by_id = {
        descriptor.source_id: descriptor
        for descriptor in _result_source_descriptors(session, workflow)
    }
    cited_source_ids = {
        evidence.source_id
        for claim in frozen_result.claims
        for evidence in claim.evidence
    }
    if not cited_source_ids.issubset(descriptor_by_id):
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "A reviewed citation has no matching frozen source descriptor.",
        )
    _assert_result_sources_current(
        session,
        workflow,
        [descriptor_by_id[source_id] for source_id in sorted(cited_source_ids)],
    )
    return frozen_result


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
        validated_plan_spec = assert_plan_for_workflow(workflow, plan)
        if plan.status in {"pending-approval", "approved"}:
            assert_plan_approval_integrity(session, workflow, plan)
        plan_tasks = [task for task in tasks if task.plan_id == plan.id]
        plan_out = PlanSnapshotOut(
            id=plan.id,
            workflow_id=plan.workflow_id,
            version=plan.version,
            status=plan.status,
            plan_sha256=plan.spec_sha256,
            generator=plan.generator,
            model=plan.model,
            prompt_version=plan.prompt_version,
            spec=validated_plan_spec,
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
    parsed_review_result = _validated_review_result(
        session,
        workflow,
        plan,
        review,
    )
    review_out = None
    if review is not None:
        if parsed_review_result is None:
            raise WorkflowConflict(
                "workflow-result-integrity-failed",
                "The stored deterministic review result is unavailable.",
            )
        review_out = ReviewSnapshotOut(
            id=review.id,
            review_type=review.review_type,
            verdict=review.verdict,
            input_sha256=review.input_sha256,
            result=parsed_review_result,
            created_at=review.created_at,
        )
    return ResearchWorkflowSnapshot(
        workflow=WorkflowStateOut(
            id=workflow.id,
            project_id=workflow.project_id,
            workflow_type="literature-synthesis",
            goal=workflow.goal,
            generation_mode=workflow.generation_mode,
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
        result=_reviewed_result_snapshot(
            session,
            workflow,
            review,
            parsed_review_result,
        ),
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
