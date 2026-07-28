from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, StrictBool, field_validator, model_validator

from ..schemas import ApiModel
from .agent_loop.policy import (
    MAX_AGENT_STEPS,
    MAX_ANALYSIS_SPEC_REVISIONS,
    MAX_CLARIFICATION_ROUNDS,
    MAX_INVALID_MODEL_DECISIONS,
    MAX_MODEL_DECISIONS,
    MAX_PLAN_REVISIONS,
    MAX_STEP_RETRIES,
)
from .agent_loop.schemas import AgentDecision, StepObservation
from .schemas import (
    AnalysisSpecSnapshotOut,
    DatasetProfile,
    DatasetReviewWarningAcceptanceOut,
    PlanSnapshotOut,
    ReviewSnapshotOut,
    StructuredAnalysisResultSnapshotOut,
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
    "approve-agent-decision",
    "reject-agent-decision",
    "respond-interaction",
    "accept-review-warnings",
    "cancel",
    "retry",
    "resume",
]
IntentParseResult = Literal[
    "valid",
    "model-not-configured",
    "model-request-failed",
    "model-request-outcome-unknown",
    "model-output-invalid",
    "deterministic-capability-guard",
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
    generator: str = Field(min_length=1, max_length=100)
    used_model: bool
    model: str | None = Field(default=None, min_length=1, max_length=200)
    endpoint_identity: str | None = Field(default=None, min_length=1, max_length=500)
    prompt_version: str = Field(min_length=1, max_length=100)
    parse_result: IntentParseResult
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime

    @model_validator(mode="after")
    def validate_router_provenance(self) -> IntentDecisionOut:
        if self.used_model and (self.model is None or self.endpoint_identity is None):
            raise ValueError("model-assisted decisions require model destination provenance")
        if not self.used_model and (
            self.model is not None or self.endpoint_identity is not None
        ):
            raise ValueError("local decisions cannot claim model destination provenance")
        return self


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
    step_id: str | None = Field(default=None, max_length=100)
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


class AgentDecisionResolveIn(StrictAgentModel):
    decision: Literal["approved", "rejected"]
    decision_output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
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


class StepObservationOut(StepObservation):
    id: str = Field(min_length=1, max_length=36)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator: str = Field(min_length=1, max_length=100)
    prompt_version: str | None = Field(default=None, min_length=1, max_length=100)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    model_invocation_id: str | None = Field(default=None, min_length=1, max_length=36)
    created_at: datetime

    @model_validator(mode="after")
    def validate_observer_provenance(self) -> StepObservationOut:
        if (self.model is None) != (self.model_invocation_id is None):
            raise ValueError("model observations require complete invocation provenance")
        return self


AgentDecisionStatus = Literal[
    "proposed",
    "waiting-user-confirmation",
    "applied",
    "superseded",
    "rejected",
    "failed",
]


class AgentDecisionOut(AgentDecision):
    id: str = Field(min_length=1, max_length=36)
    workflow_id: str = Field(min_length=1, max_length=36)
    observation_id: str = Field(min_length=1, max_length=36)
    decision_revision: int = Field(ge=1)
    status: AgentDecisionStatus
    expected_workflow_revision: int = Field(ge=1)
    generator: str = Field(min_length=1, max_length=100)
    prompt_version: str | None = Field(default=None, min_length=1, max_length=100)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    model_invocation_id: str | None = Field(default=None, min_length=1, max_length=36)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    research_context_snapshot_id: str | None = Field(
        default=None, min_length=1, max_length=36
    )
    research_context_snapshot_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    applied_at: datetime | None
    created_at: datetime

    @model_validator(mode="after")
    def validate_decision_record_lifecycle(self) -> AgentDecisionOut:
        if (self.model is None) != (self.model_invocation_id is None):
            raise ValueError("model decisions require complete invocation provenance")
        if (self.status == "applied") != (self.applied_at is not None):
            raise ValueError("only an applied decision may include applied_at")
        if (self.research_context_snapshot_id is None) != (
            self.research_context_snapshot_sha256 is None
        ):
            raise ValueError("research context snapshot identity and hash must be paired")
        return self


class AgentDecisionSummaryOut(StrictAgentModel):
    id: str = Field(min_length=1, max_length=36)
    observation_id: str = Field(min_length=1, max_length=36)
    action: Literal[
        "continue",
        "request-clarification",
        "revise-analysis-spec",
        "retry-step",
        "complete",
        "stop",
    ]
    reason: str = Field(min_length=1, max_length=4_000)
    status: AgentDecisionStatus
    requires_user_confirmation: bool
    research_context_snapshot_id: str | None = Field(
        default=None, min_length=1, max_length=36
    )
    research_context_snapshot_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    created_at: datetime
    applied_at: datetime | None

    @model_validator(mode="after")
    def validate_research_context_provenance(self) -> AgentDecisionSummaryOut:
        if (self.research_context_snapshot_id is None) != (
            self.research_context_snapshot_sha256 is None
        ):
            raise ValueError("research context snapshot identity and hash must be paired")
        return self


class AgentLoopLimitUsageOut(StrictAgentModel):
    count: int = Field(ge=0)
    limit: int = Field(gt=0)
    reached: bool

    @model_validator(mode="after")
    def validate_reached_flag(self) -> AgentLoopLimitUsageOut:
        if self.reached != (self.count >= self.limit):
            raise ValueError("agent loop reached flag must match count and limit")
        return self


class AgentLoopLimitStateOut(StrictAgentModel):
    agent_steps: AgentLoopLimitUsageOut
    plan_revisions: AgentLoopLimitUsageOut
    analysis_spec_revisions: AgentLoopLimitUsageOut
    step_retries: AgentLoopLimitUsageOut
    clarification_rounds: AgentLoopLimitUsageOut
    model_decisions: AgentLoopLimitUsageOut
    invalid_model_decisions: AgentLoopLimitUsageOut


def _zero_agent_loop_limits() -> AgentLoopLimitStateOut:
    def usage(limit: int) -> AgentLoopLimitUsageOut:
        return AgentLoopLimitUsageOut(count=0, limit=limit, reached=False)

    return AgentLoopLimitStateOut(
        agent_steps=usage(MAX_AGENT_STEPS),
        plan_revisions=usage(MAX_PLAN_REVISIONS),
        analysis_spec_revisions=usage(MAX_ANALYSIS_SPEC_REVISIONS),
        step_retries=usage(MAX_STEP_RETRIES),
        clarification_rounds=usage(MAX_CLARIFICATION_ROUNDS),
        model_decisions=usage(MAX_MODEL_DECISIONS),
        invalid_model_decisions=usage(MAX_INVALID_MODEL_DECISIONS),
    )


def _empty_agent_decision_history() -> list[AgentDecisionSummaryOut]:
    return []


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
    analysis_spec: AnalysisSpecSnapshotOut | None = None
    structured_result: StructuredAnalysisResultSnapshotOut | None = None
    review_warning_acceptance: DatasetReviewWarningAcceptanceOut | None = None
    latest_observation: StepObservationOut | None = None
    pending_decision: AgentDecisionOut | None = None
    decision_history: list[AgentDecisionSummaryOut] = Field(
        default_factory=_empty_agent_decision_history
    )
    agent_loop_limits: AgentLoopLimitStateOut = Field(
        default_factory=_zero_agent_loop_limits
    )
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
        if (
            self.latest_observation is not None
            and self.latest_observation.workflow_id != self.workflow.id
        ):
            raise ValueError("the latest observation must belong to the snapshot workflow")
        if (
            self.pending_decision is not None
            and self.pending_decision.workflow_id != self.workflow.id
        ):
            raise ValueError("the pending agent decision must belong to the snapshot workflow")
        waiting_decision = bool(
            self.pending_decision is not None
            and self.pending_decision.status == "waiting-user-confirmation"
            and self.pending_decision.requires_user_confirmation
        )
        decision_actions = {
            "approve-agent-decision",
            "reject-agent-decision",
        }.intersection(self.allowed_actions)
        if waiting_decision != bool(decision_actions):
            raise ValueError(
                "decision resolution actions must exactly follow a pending user confirmation"
            )
        if decision_actions and decision_actions != {
            "approve-agent-decision",
            "reject-agent-decision",
        }:
            raise ValueError("a pending decision must expose both resolution actions")
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
                self.analysis_spec,
                self.structured_result,
                self.review_warning_acceptance,
            )
        ):
            raise ValueError("an unresolved agent run cannot expose workflow products")
        return self
