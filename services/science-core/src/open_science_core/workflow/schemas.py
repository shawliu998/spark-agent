from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

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
    "pending-review",
    "supported",
    "partially-supported",
    "contradicted",
    "insufficient-evidence",
    "not-applicable",
]
GenerationMode = Literal["local-deterministic", "remote-model-assisted"]

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


class FrozenSourceDescriptor(StrictApiModel):
    source_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=1_000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class InspectSourcesInput(StrictApiModel):
    source_kind: Literal["pdf"] = "pdf"
    # Retained only so an old local plan can still be parsed. New remote plans
    # use frozen_sources because IDs alone do not authorize immutable content.
    source_ids: list[str] | None = Field(default=None, max_length=100)
    frozen_sources: list[FrozenSourceDescriptor] | None = Field(
        default=None,
        max_length=100,
    )

    @field_validator("source_ids")
    @classmethod
    def validate_source_allowlist(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 36 for item in normalized):
            raise ValueError("source_ids must contain non-empty record identifiers")
        if len(set(normalized)) != len(normalized):
            raise ValueError("source_ids must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_frozen_sources(self) -> InspectSourcesInput:
        if self.source_ids is not None and self.frozen_sources is not None:
            raise ValueError("source_ids and frozen_sources are mutually exclusive")
        if self.frozen_sources is not None:
            source_ids = [source.source_id for source in self.frozen_sources]
            if len(set(source_ids)) != len(source_ids):
                raise ValueError("frozen_sources must contain unique source identifiers")
        return self


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


class ModelInspectStepProposal(StrictApiModel):
    type: Literal["inspect-sources"]
    objective: str = Field(min_length=1, max_length=2_000)


class ModelEvidenceStepProposal(StrictApiModel):
    type: Literal["extract-local-evidence"]
    objective: str = Field(min_length=1, max_length=2_000)
    query: str = Field(min_length=2, max_length=8_000)
    max_passages: int = Field(default=12, ge=1, le=40)
    max_per_source: int = Field(default=4, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()


class ModelSynthesisStepProposal(StrictApiModel):
    type: Literal["synthesize-extractive-claims"]
    objective: str = Field(min_length=1, max_length=2_000)
    max_claims: int = Field(default=8, ge=1, le=20)


class ModelPlanProposal(StrictApiModel):
    schema_version: Literal["1"] = "1"
    steps: list[
        ModelInspectStepProposal
        | ModelEvidenceStepProposal
        | ModelSynthesisStepProposal
    ] = Field(min_length=3, max_length=3)

    @field_validator("steps")
    @classmethod
    def validate_frozen_sequence(
        cls,
        value: list[
            ModelInspectStepProposal
            | ModelEvidenceStepProposal
            | ModelSynthesisStepProposal
        ],
    ) -> list[
        ModelInspectStepProposal
        | ModelEvidenceStepProposal
        | ModelSynthesisStepProposal
    ]:
        expected = [
            "inspect-sources",
            "extract-local-evidence",
            "synthesize-extractive-claims",
        ]
        if [step.type for step in value] != expected:
            raise ValueError("model plan must preserve the frozen three-step sequence")
        return value


class ModelClaimProposal(StrictApiModel):
    statement: str = Field(min_length=20, max_length=2_000)
    evidence_id: str = Field(min_length=1, max_length=36)
    passage: str = Field(min_length=20, max_length=20_000)

    @field_validator("statement", "passage")
    @classmethod
    def reject_surrounding_whitespace(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("extractive text must not contain surrounding whitespace")
        return value


class ModelSynthesisProposal(StrictApiModel):
    schema_version: Literal["1"] = "1"
    claims: list[ModelClaimProposal] = Field(min_length=1, max_length=20)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("unresolved_questions")
    @classmethod
    def validate_questions(cls, value: list[str]) -> list[str]:
        normalized = [" ".join(item.split()) for item in value]
        if any(not item or len(item) > 1_000 for item in normalized):
            raise ValueError("unresolved questions must be non-empty and at most 1000 characters")
        if any(not item.endswith(("?", "？")) for item in normalized):
            raise ValueError("unresolved questions must be explicitly phrased as questions")
        return normalized


class WorkflowCreateIn(StrictApiModel):
    goal: str = Field(min_length=2, max_length=8_000)
    workflow_type: Literal["literature-synthesis"] = "literature-synthesis"
    generation_mode: GenerationMode = "local-deterministic"
    remote_data_approved: StrictBool = False

    @field_validator("goal")
    @classmethod
    def reject_blank_goal(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def require_explicit_remote_approval(self) -> WorkflowCreateIn:
        if self.generation_mode == "remote-model-assisted" and not self.remote_data_approved:
            raise ValueError(
                "remote_data_approved must be true before the research goal is sent "
                "to the configured remote model"
            )
        if self.generation_mode == "local-deterministic" and self.remote_data_approved:
            raise ValueError(
                "remote_data_approved is only valid for remote-model-assisted generation"
            )
        return self


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
    generation_mode: GenerationMode
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
    generator: str
    model: str | None
    prompt_version: str | None
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
    source_title: str | None
    source_content_hash: str | None
    source_page_manifest_hash: str | None
    page_index: int
    page_label: str | None
    text: str
    bbox: BoundingBoxOut | None
    coordinate_space: Literal["normalized-rotated-top-left-v1"]
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
    generator: str
    model: str | None
    prompt_version: str | None
    integrity_status: Literal["verified-frozen-v2", "unfrozen"]
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
    schema_version: Literal["1", "2"] = "1"
    verdict: ReviewVerdict
    checks: list[ReviewCheck]
    claim_results: list[ClaimReviewResult]
    required_revisions: list[str]
    result_snapshot_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    result_snapshot: WorkflowResultOut | None = None

    @model_validator(mode="after")
    def validate_result_snapshot_binding(self) -> DeterministicReviewResult:
        if self.schema_version == "1" and (
            self.result_snapshot_sha256 is not None or self.result_snapshot is not None
        ):
            raise ValueError("review result schema 1 cannot contain a frozen result snapshot")
        if self.schema_version == "2" and (
            self.result_snapshot_sha256 is None or self.result_snapshot is None
        ):
            raise ValueError("review result schema 2 requires an immutable result snapshot")
        if (self.result_snapshot_sha256 is None) != (self.result_snapshot is None):
            raise ValueError("result snapshot and its hash must be stored together")
        return self


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
    generation_mode: GenerationMode = "local-deterministic"


class RemoteDataApprovalEventData(StrictApiModel):
    provider: Literal["openai-compatible"]
    endpoint_host: str = Field(min_length=1, max_length=253)
    endpoint_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model: str | None = Field(default=None, max_length=200)
    data_categories: list[Literal["user-goal"]]


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
    risk_level: str | None = None
    reason: str | None = None
    affected_resources: list[str] | None = None
    approval_schema_version: str | None = None


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
    | RemoteDataApprovalEventData
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
