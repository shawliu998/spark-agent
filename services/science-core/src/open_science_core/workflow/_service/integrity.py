from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Sequence, cast

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import ApprovalRecord, EventRecord, PlanRecord, TaskRecord, WorkflowRecord
from ..schemas import (
    AUTONOMOUS_REMOTE_DATA_CATEGORIES,
    DatasetAnalysisPlanSpec,
    ExecuteAnalysisPlanStep,
    InspectSourcesInput,
    PlanSpec,
    ResearchPlanSpec,
    ResearchWorkflowCreateIn,
)

PLAN_HANDLER_VERSION = "research-plan-v2"


ROUTER_HANDLER_VERSION = "intent-router-v1"


TASK_HANDLER_VERSION = "literature-synthesis-v2"


REVIEW_HANDLER_VERSION = "deterministic-claims-v2"


LEGACY_HANDLER_VERSIONS = {
    "generate-plan": "template-plan-v1",
    "execute-task": "local-literature-v1",
    "review-workflow": "deterministic-claims-v1",
}


MAX_JOB_ATTEMPTS = 3


LOCAL_PLAN_APPROVAL_REASON = "Approve the displayed immutable local literature plan before it runs."


REMOTE_PASSAGE_APPROVAL_REASON = (
    "Approve this immutable plan and authorize sending selected-source-passages "
    "only from the listed frozen PDF sources to the configured remote model "
    "during synthesis."
)


DATASET_PLAN_APPROVAL_REASON = (
    "Approve this immutable plan to inspect the bound dataset and prepare analysis code. "
    "Python execution will require a separate approval."
)


TASK_PERMISSIONS_BY_TYPE: dict[str, list[str]] = {
    "inspect-sources": ["project-sources:read"],
    "extract-local-evidence": ["source-pages:read", "evidence:write"],
    "synthesize-extractive-claims": ["evidence:read", "claims:write"],
    "dataset-inspection": ["dataset:read", "dataset-profile:write"],
    "prepare-analysis": ["dataset-profile:read", "analysis-intent:write"],
    "python-data-analysis": ["dataset:read", "python:execute", "run-artifacts:write"],
    "collect-artifacts": ["run-artifacts:read"],
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


def model_payload(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


def workflow_create_hash(payload: ResearchWorkflowCreateIn) -> str:
    return content_sha256(model_payload(payload))


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
    dataset_source_id: str | None = None,
    dataset_content_hash: str | None = None,
    expected_workflow_revision: int | None = None,
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
    if schema_version in {"workflow-plan-approval-v2", "workflow-plan-approval-v3"}:
        if workflow_goal is None or risk_level is None or reason is None:
            raise ValueError(
                "workflow_goal, risk_level, and reason are required for a v2 approval hash"
            )
        payload.update(
            {
                "goalSha256": hashlib.sha256(workflow_goal.encode("utf-8")).hexdigest(),
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
        if schema_version == "workflow-plan-approval-v3":
            if (
                dataset_source_id is None
                or dataset_content_hash is None
                or expected_workflow_revision is None
            ):
                raise ValueError(
                    "dataset identity and expected workflow revision are required for v3"
                )
            payload.update(
                {
                    "datasetSourceId": dataset_source_id,
                    "datasetContentHash": dataset_content_hash,
                    "expectedWorkflowRevision": expected_workflow_revision,
                    "workflowType": "dataset-analysis",
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
    if workflow.workflow_type == "dataset-analysis":
        if workflow.dataset_source_id is None or workflow.dataset_content_hash is None:
            raise WorkflowConflict(
                "dataset-plan-approval-invalid",
                "The dataset plan has no immutable dataset identity.",
            )
        return (
            "medium",
            DATASET_PLAN_APPROVAL_REASON,
            [
                f"project:{workflow.project_id}",
                (
                    f"source:{workflow.dataset_source_id}:"
                    f"sha256:{workflow.dataset_content_hash}"
                ),
            ],
        )
    resources = [f"project:{workflow.project_id}"]
    if workflow.generation_mode == "local-deterministic":
        return "low", LOCAL_PLAN_APPROVAL_REASON, resources
    try:
        spec = PlanSpec.model_validate(plan.spec_json)
        inspect_inputs = cast(InspectSourcesInput, spec.steps[0].inputs)
        frozen_sources = inspect_inputs.frozen_sources
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
    approved_categories = (
        list(AUTONOMOUS_REMOTE_DATA_CATEGORIES)
        if workflow.creation_mode == "autonomous"
        else ["user-goal"]
    )
    if (
        recorded_destination.get("provider") != "openai-compatible"
        or recorded_destination.get("model") != plan.model
        or recorded_destination.get("dataCategories") != approved_categories
    ):
        raise WorkflowConflict(
            "remote-gateway-approval-mismatch",
            "The remote plan destination differs from its recorded data approval.",
        )
    resources.extend(
        [
            f"remote-endpoint-host:{recorded_destination.get('endpointHost')}",
            f"remote-endpoint-identity:{recorded_destination.get('endpointIdentity')}",
            f"remote-model:{plan.model}",
        ]
    )
    resources.extend(
        f"source:{source.source_id}:sha256:{source.content_hash}:verified-passages:remote"
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
) -> ResearchPlanSpec:
    if plan.workflow_id != workflow.id:
        raise WorkflowConflict(
            "plan-ownership-invalid",
            "The workflow plan does not belong to this workflow.",
        )
    assert_plan_integrity(plan)
    plan_type = DatasetAnalysisPlanSpec if workflow.workflow_type == "dataset-analysis" else PlanSpec
    try:
        spec = plan_type.model_validate(plan.spec_json)
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
    if isinstance(spec, DatasetAnalysisPlanSpec):
        if (
            spec.dataset_source_id != workflow.dataset_source_id
            or spec.dataset_content_hash != workflow.dataset_content_hash
        ):
            raise WorkflowConflict(
                "plan-dataset-mismatch",
                "The approved plan no longer matches the workflow dataset identity.",
            )
        expected_provenance = ("dataset-template-v1", None, "dataset-template-v1")
    else:
        expected_provenance = (
            ("template-v1", None, "template-v1")
            if workflow.generation_mode == "local-deterministic"
            else ("remote-model-assisted-v1", plan.model, "remote-plan-v1")
        )
    if (plan.generator, plan.model, plan.prompt_version) != expected_provenance or (
        workflow.generation_mode == "remote-model-assisted" and not plan.model
    ):
        raise WorkflowConflict(
            "plan-provenance-invalid",
            "The approved plan provenance no longer matches the workflow generation mode.",
        )
    return spec


def assert_approved_plan_for_workflow(
    workflow: WorkflowRecord,
    plan: PlanRecord,
) -> ResearchPlanSpec:
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
        "workflow-plan-approval-v3",
    }:
        raise WorkflowConflict(
            "plan-approval-invalid",
            "The approved plan has no supported immutable approval record.",
        )
    expected_risk, expected_reason, expected_resources = expected_plan_approval_semantics(
        session, workflow, plan
    )
    if approval.payload_schema_version == "workflow-plan-approval-v3":
        approval_revision = _dataset_plan_approval_revision(approval)
        if plan.status == "pending-approval" and approval_revision != workflow.row_version:
            raise WorkflowConflict(
                "plan-approval-semantics-invalid",
                "The dataset plan approval no longer matches the workflow revision.",
            )
        expected_resources = [
            *expected_resources,
            f"workflow-revision:{approval_revision}",
        ]
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
            "The displayed plan approval metadata no longer matches its fixed consent contract.",
        )
    decision_valid = (plan.status == "approved" and approval.user_decision == "approved") or (
        plan.status == "pending-approval" and approval.user_decision is None
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
            _dataset_plan_approval_revision(approval)
            if approval.payload_schema_version == "workflow-plan-approval-v3"
            else None
        ),
    )
    if approval.intent_hash != expected_hash:
        raise WorkflowConflict(
            "approval-hash-mismatch",
            "The approved plan no longer matches its immutable approval payload.",
        )
    return approval


def _dataset_plan_approval_revision(approval: ApprovalRecord) -> int:
    prefix = "workflow-revision:"
    values = [
        resource.removeprefix(prefix)
        for resource in approval.affected_resources
        if resource.startswith(prefix)
    ]
    if len(values) != 1 or not values[0].isdigit() or int(values[0]) < 1:
        raise WorkflowConflict(
            "plan-approval-semantics-invalid",
            "The dataset plan approval is missing its workflow revision binding.",
        )
    return int(values[0])


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


def plan_step_materialization(
    spec: ResearchPlanSpec,
    order_index: int,
) -> tuple[list[str], str, int]:
    if isinstance(spec, DatasetAnalysisPlanSpec):
        step = spec.steps[order_index]
        expected_outputs: list[str] = (
            [str(item) for item in step.inputs.expected_outputs]
            if isinstance(step, ExecuteAnalysisPlanStep)
            else [str(item) for item in step.expected_artifacts]
        )
        timeout_seconds = (
            step.inputs.timeout_seconds if isinstance(step, ExecuteAnalysisPlanStep) else 120
        )
        return expected_outputs, step.risk_level, timeout_seconds
    step = spec.steps[order_index]
    return list(step.expected_outputs), "low", 120


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
    expected_inputs = model_payload(step.inputs)
    expected_input_sha256 = content_sha256(
        {
            "inputs": expected_inputs,
            "objective": step.objective,
            "stepKey": step.key,
            "stepType": step.type,
        }
    )
    expected_permissions = TASK_PERMISSIONS_BY_TYPE[step.type]
    expected_outputs, expected_risk_level, expected_timeout = plan_step_materialization(
        spec,
        task.order_index,
    )
    if (
        task.step_key != step.key
        or task.task_type != step.type
        or task.objective != step.objective
        or task.inputs != expected_inputs
        or task.input_sha256 != expected_input_sha256
        or task.expected_outputs != expected_outputs
        or task.acceptance_criteria != list(step.acceptance_criteria)
        or task.permissions != expected_permissions
        or task.risk_level != expected_risk_level
        or task.timeout_seconds != expected_timeout
    ):
        raise WorkflowConflict(
            "task-plan-mismatch",
            "The stored workflow step no longer matches its approved plan step.",
        )
