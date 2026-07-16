from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, StrictBool, field_validator, model_validator

from ..schemas import ApiModel
from .schemas import (
    DatasetProfile,
    DatasetReviewWarningAcceptanceOut,
    PlanSnapshotOut,
    ReviewSnapshotOut,
    WorkflowAnalysisIntentOut,
    WorkflowAnalysisRunOut,
    WorkflowPendingApprovalOut,
    WorkflowResultOut,
)

AgentIntent = Literal[
    "literature-synthesis",
    "dataset-analysis",
    "mixed-research",
    "clarification-required",
    "unsupported",
]
ResolvedAgentWorkflowType = Literal[
    "literature-synthesis",
    "dataset-analysis",
    "mixed-research",
]
AgentWorkflowStatus = Literal[
    "routing",
    "waiting-clarification",
    "planning",
    "waiting-plan-approval",
    "running",
    "reviewing",
    "completed",
    "unsupported",
    "blocked",
    "failed",
    "cancelled",
]
InteractionRequestType = Literal[
    "single-choice",
    "multi-choice",
    "text",
    "number",
    "boolean",
    "column-selection",
    "method-confirmation",
    "assumption-confirmation",
]
InteractionStatus = Literal["pending", "answered", "superseded", "cancelled"]
AgentAllowedAction = Literal[
    "approve-plan",
    "approve-analysis",
    "reject-analysis",
    "respond-interaction",
    "accept-review-warnings",
    "cancel",
    "retry",
    "resume",
]


class StrictAgentModel(ApiModel):
    model_config = ConfigDict(extra="forbid")


class AgentRunCreateIn(StrictAgentModel):
    goal: str = Field(min_length=2, max_length=8_000)
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    mode: Literal["autonomous"] = "autonomous"
    remote_data_approved: StrictBool = False

    @field_validator("source_ids")
    @classmethod
    def validate_source_ids(cls, value: list[str]) -> list[str]:
        if any(not source_id or len(source_id) > 36 for source_id in value):
            raise ValueError("source IDs must be non-empty identifiers")
        if len(set(value)) != len(value):
            raise ValueError("source IDs must be unique")
        return value


class IntentDecisionOut(StrictAgentModel):
    id: str = Field(min_length=1, max_length=36)
    workflow_id: str = Field(min_length=1, max_length=36)
    intent: AgentIntent
    confidence: float = Field(ge=0, le=1)
    reasoning_summary: str = Field(min_length=1, max_length=2_000)
    selected_source_ids: list[str] = Field(max_length=100)
    missing_inputs: list[str] = Field(max_length=100)
    proposed_workflow_type: ResolvedAgentWorkflowType | None
    prompt_version: str = Field(min_length=1, max_length=100)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class UserResponseOut(StrictAgentModel):
    id: str = Field(min_length=1, max_length=36)
    interaction_id: str = Field(min_length=1, max_length=36)
    revision: int = Field(ge=1)
    response: Any
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


def _empty_interaction_options() -> list[dict[str, Any]]:
    return []


class InteractionRequestOut(StrictAgentModel):
    id: str = Field(min_length=1, max_length=36)
    workflow_id: str = Field(min_length=1, max_length=36)
    step_id: str | None = Field(default=None, max_length=36)
    request_type: InteractionRequestType
    question: str = Field(min_length=1, max_length=4_000)
    options: list[dict[str, Any]] = Field(
        default_factory=_empty_interaction_options,
        max_length=100,
    )
    required: bool
    status: InteractionStatus
    response_schema: dict[str, Any]
    workflow_revision: int = Field(ge=1)
    latest_response: UserResponseOut | None
    created_at: datetime
    answered_at: datetime | None

    @model_validator(mode="after")
    def validate_interaction_lifecycle(self) -> InteractionRequestOut:
        if (
            self.latest_response is not None
            and self.latest_response.interaction_id != self.id
        ):
            raise ValueError("the latest response must belong to the interaction")
        if self.status == "answered" and (
            self.latest_response is None or self.answered_at is None
        ):
            raise ValueError("an answered interaction must include its durable response")
        if self.status == "pending" and self.latest_response is not None:
            raise ValueError("a pending interaction cannot already include a response")
        return self


class InteractionRespondIn(StrictAgentModel):
    response: Any
    expected_workflow_revision: int = Field(ge=1)


class AgentStatusReasonOut(StrictAgentModel):
    code: str = Field(min_length=1, max_length=100)
    user_message: str = Field(min_length=1, max_length=2_000)


class AgentWorkflowStateOut(StrictAgentModel):
    id: str
    project_id: str
    workflow_type: ResolvedAgentWorkflowType | None
    goal: str
    source_ids: list[str]
    mode: Literal["autonomous"]
    generation_mode: Literal["local-deterministic", "remote-model-assisted"]
    status: AgentWorkflowStatus
    revision: int = Field(ge=1)
    plan_version: int | None = Field(default=None, ge=1)
    current_step_id: str | None
    retry_count: int = Field(ge=0)
    status_reason: AgentStatusReasonOut | None
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class AgentRunSnapshot(StrictAgentModel):
    workflow: AgentWorkflowStateOut
    intent_decision: IntentDecisionOut | None
    interactions: list[InteractionRequestOut]
    plan: PlanSnapshotOut | None
    pending_approvals: list[WorkflowPendingApprovalOut]
    result: WorkflowResultOut | None
    latest_review: ReviewSnapshotOut | None
    dataset_profile: DatasetProfile | None = None
    analysis_intent: WorkflowAnalysisIntentOut | None = None
    analysis_run: WorkflowAnalysisRunOut | None = None
    review_warning_acceptance: DatasetReviewWarningAcceptanceOut | None = None
    allowed_actions: list[AgentAllowedAction]
    event_cursor: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_agent_run_contract(self) -> AgentRunSnapshot:
        if any(item.workflow_id != self.workflow.id for item in self.interactions):
            raise ValueError("interactions must belong to the snapshot workflow")
        if (
            self.intent_decision is not None
            and self.intent_decision.workflow_id != self.workflow.id
        ):
            raise ValueError("the intent decision must belong to the snapshot workflow")
        if self.intent_decision is not None and not set(
            self.intent_decision.selected_source_ids
        ).issubset(self.workflow.source_ids):
            raise ValueError("the intent decision cannot select unbound sources")
        decision_required = self.workflow.workflow_type is not None or self.workflow.status in {
            "waiting-clarification",
            "unsupported",
        }
        if decision_required and self.intent_decision is None:
            raise ValueError("the workflow lifecycle requires a current intent decision")
        if (
            self.workflow.workflow_type is not None
            and self.intent_decision is not None
            and (
                self.intent_decision.intent != self.workflow.workflow_type
                or self.intent_decision.proposed_workflow_type
                != self.workflow.workflow_type
            )
        ):
            raise ValueError("the intent decision must match the resolved workflow type")
        if self.workflow.status == "waiting-clarification" and (
            self.intent_decision is None
            or self.intent_decision.intent != "clarification-required"
            or not any(item.status == "pending" for item in self.interactions)
        ):
            raise ValueError(
                "a waiting workflow requires a clarification decision and pending request"
            )
        if (
            self.workflow.status == "unsupported"
            and self.intent_decision is not None
            and self.intent_decision.intent not in {"mixed-research", "unsupported"}
        ):
            raise ValueError("an unsupported workflow requires an unsupported decision")
        if self.workflow.workflow_type is None and any(
            value is not None
            for value in (
                self.plan,
                self.result,
                self.latest_review,
                self.dataset_profile,
                self.analysis_intent,
                self.analysis_run,
                self.review_warning_acceptance,
            )
        ):
            raise ValueError("an unresolved agent run cannot expose workflow products")
        return self
