from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from ..analysis import canonical_analysis_payload
from ..config import settings
from ..fixed_analysis_policy import (
    FIXED_ANALYSIS_POLICY_ID,
    FixedAnalysisPolicyError,
    fixed_analysis_template_for_repair_attempt,
)
from ..models import (
    AnalysisIntentRecord,
    ApprovalRecord,
    EventRecord,
    PlanRecord,
    SourceRecord,
    TaskRecord,
    WorkflowRecord,
    utc_now,
)
from ..schemas import AnalysisIntentCreate, AnalysisIntentOut
from .contracts import canonical_workflow_analysis_payload
from .errors import (
    ANALYSIS_ACTION,
    ANALYSIS_APPROVAL_REASON,
    ANALYSIS_RISK_LEVEL,
    ANALYSIS_V1_SCHEMA,
    ANALYSIS_V3_SCHEMA,
    WORKFLOW_ANALYSIS_APPROVAL_REASON,
    AnalysisServiceError,
)
from .integrity import (
    approval_for_intent,
    assert_approval_record,
    assert_ready_dataset,
    assert_repair_lineage,
    assert_workflow_execution_inputs,
    assert_workflow_task_dataset_binding,
    intent_or_error,
    project_or_error,
    validate_code,
    validate_workflow_analysis_intent,
)


@dataclass(frozen=True, slots=True)
class WorkflowIntentBundle:
    intent: AnalysisIntentRecord
    approval: ApprovalRecord
    expected_workflow_revision: int


def create_standalone_analysis_intent(
    session: Session,
    project_id: str,
    payload: AnalysisIntentCreate,
) -> AnalysisIntentRecord:
    """Stage a legacy standalone intent without committing the caller's transaction."""

    project_or_error(session, project_id)
    dataset = session.get(SourceRecord, payload.dataset_source_id)
    if dataset is None or dataset.project_id != project_id:
        raise AnalysisServiceError(
            404,
            "Dataset source not found in this project",
            code="dataset-not-found",
        )
    assert_ready_dataset(dataset)
    validate_code(payload.code)

    _canonical, payload_sha256 = canonical_analysis_payload(
        dataset.id, payload.objective, payload.code
    )
    task_id = str(uuid.uuid4())
    intent_id = str(uuid.uuid4())
    task = TaskRecord(
        id=task_id,
        project_id=project_id,
        objective=payload.objective,
        task_type="python-data-analysis",
        inputs={
            "datasetSourceId": dataset.id,
            "objective": payload.objective,
            "code": payload.code,
            "payloadSha256": payload_sha256,
        },
        expected_outputs=["executed-notebook", "stdout", "stderr", "log", "artifacts"],
        acceptance_criteria=[
            "approved payload hash must exactly match executed payload",
            "runtime output hashes must be independently verified",
        ],
        permissions=["dataset:read", "python:execute", "run-artifacts:write"],
        status="waiting-execution-approval",
        timeout_seconds=settings.execution_timeout_seconds,
    )
    session.add(task)
    session.flush()
    intent = AnalysisIntentRecord(
        id=intent_id,
        task_id=task_id,
        project_id=project_id,
        dataset_source_id=dataset.id,
        objective=payload.objective,
        code=payload.code,
        payload_sha256=payload_sha256,
        status="waiting-approval",
    )
    session.add(intent)
    session.add(
        ApprovalRecord(
            id=str(uuid.uuid4()),
            task_id=task_id,
            subject_type="analysis-intent",
            subject_id=intent_id,
            payload_schema_version=ANALYSIS_V1_SCHEMA,
            intent_hash=payload_sha256,
            requested_action=ANALYSIS_ACTION,
            risk_level=ANALYSIS_RISK_LEVEL,
            reason=ANALYSIS_APPROVAL_REASON,
            affected_resources=[dataset.id, "runs/<run-id>"],
        )
    )
    session.add(
        EventRecord(
            id=str(uuid.uuid4()),
            project_id=project_id,
            event_type="analysis.intent.created",
            payload={
                "analysisIntentId": intent_id,
                "taskId": task_id,
                "datasetSourceId": dataset.id,
                "payloadSha256": payload_sha256,
            },
        )
    )
    session.flush()
    return intent


def create_workflow_analysis_intent(
    session: Session,
    *,
    expected_workflow_id: str,
    task_id: str,
    code: str,
    expected_outputs: Sequence[str],
    expected_workflow_revision: int,
    previous_intent_id: str | None = None,
    error_summary: dict[str, Any] | None = None,
    code_diff: str | None = None,
    intent_id: str | None = None,
) -> WorkflowIntentBundle:
    """Stage a workflow-bound v2 intent and exact approval record.

    Workflow event sequencing and workflow/task CAS transitions deliberately stay
    with the workflow handler so they can be committed atomically with its job.
    """

    workflow = session.get(WorkflowRecord, expected_workflow_id)
    if workflow is None:
        raise AnalysisServiceError(404, "Workflow not found", code="workflow-not-found")
    if workflow.row_version != expected_workflow_revision:
        raise AnalysisServiceError(
            409,
            "The workflow changed before the analysis intent was prepared",
            code="workflow-revision-conflict",
        )
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise AnalysisServiceError(404, "Analysis task not found", code="task-not-found")
    plan = session.get(PlanRecord, task.plan_id) if task.plan_id is not None else None
    if plan is None or plan.workflow_id != workflow.id or plan.status != "approved":
        raise AnalysisServiceError(
            409,
            "Analysis task is not bound to the approved workflow plan",
            code="analysis-binding-invalid",
        )
    dataset = (
        session.get(SourceRecord, workflow.dataset_source_id)
        if workflow.dataset_source_id is not None
        else None
    )
    assert_workflow_task_dataset_binding(workflow, task, dataset)
    assert dataset is not None
    outputs = list(expected_outputs)
    assert_workflow_execution_inputs(task, outputs)
    repair_attempt = 0
    previous: AnalysisIntentRecord | None = None
    if previous_intent_id is not None:
        previous = session.get(AnalysisIntentRecord, previous_intent_id)
        if previous is None:
            raise AnalysisServiceError(
                409,
                "The prior analysis intent is missing",
                code="analysis-lineage-invalid",
            )
        repair_attempt = (previous.repair_attempt or 0) + 1
    try:
        policy_template = fixed_analysis_template_for_repair_attempt(repair_attempt)
    except FixedAnalysisPolicyError as error:
        raise AnalysisServiceError(
            409,
            "Analysis repair lineage is invalid",
            code="analysis-lineage-invalid",
        ) from error
    validate_code(
        code,
        policy_profile_id=FIXED_ANALYSIS_POLICY_ID,
        policy_template=policy_template,
    )
    selected_intent_id = intent_id or str(uuid.uuid4())
    intent = AnalysisIntentRecord(
        id=selected_intent_id,
        task_id=task.id,
        project_id=workflow.project_id,
        workflow_id=workflow.id,
        plan_step_id="execute-analysis",
        previous_intent_id=previous_intent_id,
        dataset_source_id=dataset.id,
        dataset_content_hash=dataset.content_hash,
        objective=task.objective,
        code=code,
        expected_outputs=outputs,
        timeout_seconds=task.timeout_seconds,
        risk_level=ANALYSIS_RISK_LEVEL,
        repair_attempt=repair_attempt,
        error_summary=error_summary,
        code_diff=code_diff,
        payload_sha256="0" * 64,
        status="waiting-approval",
    )
    assert_repair_lineage(session, intent, previous)
    _canonical, payload_sha256 = canonical_workflow_analysis_payload(
        project_id=workflow.project_id,
        workflow_id=workflow.id,
        plan_id=plan.id,
        task_id=task.id,
        analysis_intent_id=intent.id,
        plan_step_id="execute-analysis",
        dataset_source_id=dataset.id,
        dataset_content_hash=dataset.content_hash,
        objective=task.objective,
        expected_outputs=outputs,
        timeout_seconds=task.timeout_seconds,
        code=code,
        code_diff=code_diff,
        error_summary=error_summary,
        previous_intent_id=previous_intent_id,
        repair_attempt=repair_attempt,
        expected_workflow_revision=expected_workflow_revision,
        schema_version=ANALYSIS_V3_SCHEMA,
        policy_profile_id=FIXED_ANALYSIS_POLICY_ID,
        policy_template=policy_template,
    )
    intent.payload_sha256 = payload_sha256
    session.add(intent)
    session.flush()
    approval = ApprovalRecord(
        id=str(uuid.uuid4()),
        task_id=task.id,
        workflow_id=workflow.id,
        plan_id=task.plan_id,
        subject_type="analysis-intent",
        subject_id=intent.id,
        payload_schema_version=ANALYSIS_V3_SCHEMA,
        intent_hash=payload_sha256,
        requested_action=ANALYSIS_ACTION,
        risk_level=ANALYSIS_RISK_LEVEL,
        reason=WORKFLOW_ANALYSIS_APPROVAL_REASON,
        affected_resources=[
            f"source:{dataset.id}:sha256:{dataset.content_hash}",
            "runs/<run-id>",
        ],
    )
    session.add(approval)
    session.flush()
    validate_workflow_analysis_intent(
        session,
        intent,
        expected_workflow_id=workflow.id,
        expected_workflow_revision=expected_workflow_revision,
        require_approval=True,
        require_current_revision=True,
    )
    return WorkflowIntentBundle(
        intent=intent,
        approval=approval,
        expected_workflow_revision=expected_workflow_revision,
    )


def decide_standalone_analysis_intent(
    session: Session,
    intent_id: str,
    decision: str,
) -> AnalysisIntentRecord:
    intent = intent_or_error(session, intent_id)
    if intent.workflow_id is not None:
        raise AnalysisServiceError(
            409,
            "Workflow analysis approval requires the workflow-scoped endpoint",
            code="workflow-analysis-endpoint-required",
        )
    return _decide_analysis_intent(session, intent, decision, manage_task_status=True)


def decide_workflow_analysis_intent(
    session: Session,
    intent_id: str,
    decision: str,
    *,
    expected_workflow_id: str,
    expected_workflow_revision: int,
) -> AnalysisIntentRecord:
    intent = intent_or_error(session, intent_id)
    validate_workflow_analysis_intent(
        session,
        intent,
        expected_workflow_id=expected_workflow_id,
        expected_workflow_revision=expected_workflow_revision,
        require_approval=True,
        require_current_revision=True,
    )
    return _decide_analysis_intent(session, intent, decision, manage_task_status=False)


def _decide_analysis_intent(
    session: Session,
    intent: AnalysisIntentRecord,
    decision: str,
    *,
    manage_task_status: bool,
) -> AnalysisIntentRecord:
    if decision not in {"approved", "rejected"}:
        raise AnalysisServiceError(422, "Invalid analysis decision", code="invalid-decision")
    decided_at = utc_now()
    decision_result = session.execute(
        update(AnalysisIntentRecord)
        .where(
            AnalysisIntentRecord.id == intent.id,
            AnalysisIntentRecord.status == "waiting-approval",
            AnalysisIntentRecord.decision.is_(None),
        )
        .values(decision=decision, status=decision, updated_at=decided_at)
    )
    if cast(CursorResult[object], decision_result).rowcount != 1:
        session.expire_all()
        current = intent_or_error(session, intent.id)
        if current.decision == decision:
            return current
        raise AnalysisServiceError(
            409,
            "Analysis intent already has a final decision",
            code="analysis-decision-final",
        )

    session.expire(intent)
    intent = intent_or_error(session, intent.id)
    approval = approval_for_intent(session, intent)
    task = session.get(TaskRecord, intent.task_id)
    if approval is None or task is None:
        raise AnalysisServiceError(
            500,
            "Analysis approval audit record is missing",
            code="analysis-audit-missing",
        )
    approval.user_decision = decision
    approval.decided_at = decided_at
    assert_approval_record(session, intent, approval)
    if manage_task_status:
        task.status = "waiting-execution" if decision == "approved" else "rejected"
    if intent.workflow_id is None:
        session.add(
            EventRecord(
                id=str(uuid.uuid4()),
                project_id=intent.project_id,
                event_type=f"analysis.intent.{decision}",
                payload={
                    "analysisIntentId": intent.id,
                    "taskId": intent.task_id,
                    "payloadSha256": intent.payload_sha256,
                },
            )
        )
    session.flush()
    return intent


def analysis_intent_out(intent: AnalysisIntentRecord) -> AnalysisIntentOut:
    return AnalysisIntentOut(
        id=intent.id,
        task_id=intent.task_id,
        project_id=intent.project_id,
        dataset_source_id=intent.dataset_source_id,
        objective=intent.objective,
        code=intent.code,
        payload_sha256=intent.payload_sha256,
        risk_level="high",
        affected_resources=[intent.dataset_source_id, "runs/<run-id>"],
        status=intent.status,
        decision=intent.decision,
        created_at=intent.created_at,
        updated_at=intent.updated_at,
    )
