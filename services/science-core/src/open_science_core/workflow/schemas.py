from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints, field_validator

from ..schemas import ApiModel, BoundingBoxOut


WorkflowStatus = Literal[
    "planning",
    "waiting-plan-approval",
    "running",
    "reviewing",
    "completed",
    "blocked",
    "failed",
    "cancelled",
]
PlanStatus = Literal["pending-approval", "approved", "rejected", "superseded"]
TaskStepType = Literal[
    "inspect-sources",
    "extract-local-evidence",
    "synthesize-extractive-claims",
]
TaskStatus = Literal[
    "pending",
    "queued",
    "running",
    "completed",
    "blocked",
    "failed",
    "cancelled",
]
AllowedAction = Literal["approve-plan", "cancel", "retry", "resume"]
ReviewVerdict = Literal["passed", "revision-required", "blocked", "failed"]
ClaimSupportStatus = Literal[
    "supported",
    "partially-supported",
    "contradicted",
    "insufficient-evidence",
    "not-applicable",
]

StepKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][a-z0-9-]*$",
    ),
]


class StrictApiModel(ApiModel):
    model_config = ConfigDict(extra="forbid")


class InspectSourcesInput(StrictApiModel):
    source_kind: Literal["pdf"] = "pdf"


class ExtractLocalEvidenceInput(StrictApiModel):
    query: str = Field(min_length=2, max_length=8_000)
    max_passages: int = Field(default=12, ge=1, le=40)
    max_per_source: int = Field(default=4, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()


class SynthesizeExtractiveClaimsInput(StrictApiModel):
    max_claims: int = Field(default=8, ge=1, le=20)


class SequentialStepSpec(StrictApiModel):
    key: StepKey
    type: TaskStepType
    objective: str = Field(min_length=1, max_length=2_000)
    inputs: InspectSourcesInput | ExtractLocalEvidenceInput | SynthesizeExtractiveClaimsInput
    expected_outputs: list[Literal["sources", "evidence", "claims", "evidence-map"]]
    acceptance_criteria: list[
        Literal[
            "at-least-one-ready-pdf",
            "at-least-one-verified-evidence",
            "at-least-one-claim",
            "every-claim-has-verified-evidence",
        ]
    ] = Field(min_length=1)


class PlanSpec(StrictApiModel):
    schema_version: Literal["1"] = "1"
    goal: str = Field(min_length=2, max_length=8_000)
    steps: list[SequentialStepSpec] = Field(min_length=3, max_length=3)

    @field_validator("steps")
    @classmethod
    def validate_frozen_sequence(
        cls, value: list[SequentialStepSpec]
    ) -> list[SequentialStepSpec]:
        expected = [
            "inspect-sources",
            "extract-local-evidence",
            "synthesize-extractive-claims",
        ]
        if [step.type for step in value] != expected:
            raise ValueError("the first workflow version requires the frozen three-step sequence")
        if len({step.key for step in value}) != len(value):
            raise ValueError("plan step keys must be unique")
        expected_input_types = (
            InspectSourcesInput,
            ExtractLocalEvidenceInput,
            SynthesizeExtractiveClaimsInput,
        )
        if any(not isinstance(step.inputs, kind) for step, kind in zip(value, expected_input_types)):
            raise ValueError("step input does not match its step type")
        return value


class WorkflowCreateIn(StrictApiModel):
    goal: str = Field(min_length=2, max_length=8_000)
    workflow_type: Literal["literature-synthesis"] = "literature-synthesis"

    @field_validator("goal")
    @classmethod
    def reject_blank_goal(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal must not be blank")
        return value.strip()


class ApprovePlanIn(StrictApiModel):
    approval_id: str = Field(min_length=1, max_length=36)
    plan_id: str = Field(min_length=1, max_length=36)
    plan_version: int = Field(ge=1)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_workflow_revision: int = Field(ge=1)


class WorkflowMutationIn(StrictApiModel):
    expected_workflow_revision: int | None = Field(default=None, ge=1)


class RetryWorkflowIn(WorkflowMutationIn):
    task_id: str | None = Field(default=None, max_length=36)


class BlockingReasonOut(ApiModel):
    code: str
    user_message: str
    retryable: bool


class WorkflowStateOut(ApiModel):
    id: str
    project_id: str
    workflow_type: Literal["literature-synthesis"]
    goal: str
    status: WorkflowStatus
    revision: int
    plan_version: int | None
    current_step_id: str | None
    retry_count: int
    blocking_reason: BlockingReasonOut | None
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class MaterializedStepOut(ApiModel):
    id: str
    key: str
    order_index: int
    type: TaskStepType
    objective: str
    status: TaskStatus
    retry_count: int
    started_at: datetime | None
    completed_at: datetime | None
    output_summary: str | None


class PlanSnapshotOut(ApiModel):
    id: str
    workflow_id: str
    version: int
    status: PlanStatus
    plan_sha256: str
    spec: PlanSpec
    steps: list[MaterializedStepOut]
    created_at: datetime
    approved_at: datetime | None


class PendingApprovalOut(ApiModel):
    id: str
    workflow_id: str
    plan_id: str
    task_id: str | None
    kind: Literal["plan"]
    status: Literal["waiting"]
    subject_type: str
    subject_id: str
    action: str
    payload_sha256: str
    risk_level: str
    reason: str
    affected_resources: list[str]
    created_at: datetime
    decided_at: datetime | None


class EvidenceRelationshipOut(ApiModel):
    evidence_id: str
    source_id: str
    page_index: int
    page_label: str | None
    text: str
    bbox: BoundingBoxOut | None
    coordinate_space: str
    quote_hash: str
    extraction_method: str
    confidence: float
    verified: bool
    relationship: Literal["supporting", "contradicting"]


class WorkflowClaimOut(ApiModel):
    id: str
    statement: str
    support_status: ClaimSupportStatus
    confidence: float
    evidence: list[EvidenceRelationshipOut]


class WorkflowResultOut(ApiModel):
    answer_id: str
    summary: str
    claims: list[WorkflowClaimOut]
    unresolved_questions: list[str]


class ReviewCheck(StrictApiModel):
    code: str = Field(min_length=1, max_length=100)
    status: Literal["passed", "failed"]
    message: str = Field(min_length=1, max_length=1_000)
    claim_id: str | None = None
    evidence_id: str | None = None


class ClaimReviewResult(StrictApiModel):
    claim_id: str
    status: ClaimSupportStatus
    evidence_ids: list[str]
    relationships: list[Literal["supporting", "contradicting"]]


class DeterministicReviewResult(StrictApiModel):
    schema_version: Literal["1"] = "1"
    verdict: ReviewVerdict
    checks: list[ReviewCheck]
    claim_results: list[ClaimReviewResult]
    required_revisions: list[str]


class ReviewSnapshotOut(ApiModel):
    id: str
    review_type: str
    verdict: ReviewVerdict
    input_sha256: str
    result: DeterministicReviewResult
    created_at: datetime


class ResearchWorkflowSnapshot(ApiModel):
    workflow: WorkflowStateOut
    plan: PlanSnapshotOut | None
    pending_approvals: list[PendingApprovalOut]
    result: WorkflowResultOut | None
    latest_review: ReviewSnapshotOut | None
    allowed_actions: list[AllowedAction]
    event_cursor: int


class CreatedEventData(StrictApiModel):
    workflow_type: Literal["literature-synthesis"]
    goal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class StatusChangedEventData(StrictApiModel):
    previous_status: WorkflowStatus
    status: WorkflowStatus
    reason_code: str | None = None


class PlanEventData(StrictApiModel):
    plan_id: str
    version: int
    plan_sha256: str


class ApprovalEventData(StrictApiModel):
    approval_id: str
    subject_type: str
    subject_id: str
    action: str
    payload_sha256: str


class TaskEventData(StrictApiModel):
    task_id: str
    step_key: str
    order_index: int
    status: TaskStatus
    output_count: int | None = None
    error_code: str | None = None


class JobEventData(StrictApiModel):
    job_id: str
    kind: str
    attempt: int
    error_code: str | None = None


class ReviewEventData(StrictApiModel):
    review_id: str
    verdict: ReviewVerdict
    claim_count: int


class CancelEventData(StrictApiModel):
    requested: bool


WorkflowEventData = (
    CreatedEventData
    | StatusChangedEventData
    | PlanEventData
    | ApprovalEventData
    | TaskEventData
    | JobEventData
    | ReviewEventData
    | CancelEventData
)


class WorkflowEventOut(ApiModel):
    id: str
    sequence: int
    type: str
    task_id: str | None
    job_id: str | None
    data: WorkflowEventData
    created_at: datetime


class WorkflowEventsOut(ApiModel):
    events: list[WorkflowEventOut]
    next_after: int
    has_more: bool
