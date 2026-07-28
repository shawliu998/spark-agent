from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from ..schemas import DiscoverySelectionProjection
from .schemas import AgentAction, StepObservation

MAX_AGENT_STEPS = 8
MAX_PLAN_REVISIONS = 2
MAX_ANALYSIS_SPEC_REVISIONS = 2
MAX_STEP_RETRIES = 2
MAX_CLARIFICATION_ROUNDS = 3
MAX_MODEL_DECISIONS = 5
MAX_INVALID_MODEL_DECISIONS = 2

ReviewerVerdict = Literal[
    "passed",
    "passed-with-warnings",
    "revision-required",
    "blocked",
    "failed",
]

_TRANSIENT_RETRY_CODES = frozenset(
    {
        "artifact-collection-timeout",
        "lease-expired",
        "model-transport-failed",
        "runtime-temporarily-unavailable",
        "runtime-timeout",
        "worker-interrupted",
        "connector-unavailable",
        "rate-limited",
    }
)
_JUDGMENT_ACTIONS: frozenset[AgentAction] = frozenset(
    {"request-clarification", "revise-analysis-spec"}
)


@dataclass(frozen=True, slots=True)
class AgentLoopCounts:
    agent_steps: int = 0
    plan_revisions: int = 0
    analysis_spec_revisions: int = 0
    step_retries: int = 0
    clarification_rounds: int = 0
    model_decisions: int = 0
    invalid_model_decisions: int = 0

    def __post_init__(self) -> None:
        if any(
            count < 0
            for count in (
                self.agent_steps,
                self.plan_revisions,
                self.analysis_spec_revisions,
                self.step_retries,
                self.clarification_rounds,
                self.model_decisions,
                self.invalid_model_decisions,
            )
        ):
            raise ValueError("agent loop counts must be non-negative")


@dataclass(frozen=True, slots=True)
class AgentLoopContext:
    counts: AgentLoopCounts = AgentLoopCounts()
    next_step_key: str | None = None
    discovery_selection: DiscoverySelectionProjection | None = None
    run_completed: bool = False
    structured_result_exists: bool = False
    required_artifacts_exist: bool = False
    analysis_spec_current: bool = False
    analysis_intent_approved_and_current: bool = False
    literature_result_verified: bool = False
    reviewer_verdict: ReviewerVerdict | None = None
    review_warnings_accepted: bool = False
    unresolved_required_interaction: bool = False
    pending_approval: bool = False
    required_revision: bool = False
    failure_code: str | None = None
    failure_is_transient: bool = False
    terminal_result_exists: bool = False
    spec_revision_is_valid: bool = False
    clarification_is_available: bool = False
    capability_unsupported: bool = False
    input_is_irrecoverable: bool = False

    def __post_init__(self) -> None:
        if self.next_step_key is not None and not self.next_step_key.strip():
            raise ValueError("next step key must be non-empty when present")
        if (
            self.discovery_selection is not None
            and self.discovery_selection.selected_step_key != self.next_step_key
        ):
            raise ValueError("Discovery selection must match the current next step")
        if self.failure_code is not None and not self.failure_code.strip():
            raise ValueError("failure code must be non-empty when present")


def reached_loop_limits(counts: AgentLoopCounts) -> tuple[str, ...]:
    reached: list[str] = []
    if counts.agent_steps >= MAX_AGENT_STEPS:
        reached.append("agent-steps")
    if counts.plan_revisions >= MAX_PLAN_REVISIONS:
        reached.append("plan-revisions")
    if counts.analysis_spec_revisions >= MAX_ANALYSIS_SPEC_REVISIONS:
        reached.append("analysis-spec-revisions")
    if counts.step_retries >= MAX_STEP_RETRIES:
        reached.append("step-retries")
    if counts.clarification_rounds >= MAX_CLARIFICATION_ROUNDS:
        reached.append("clarification-rounds")
    if counts.model_decisions >= MAX_MODEL_DECISIONS:
        reached.append("model-decisions")
    if counts.invalid_model_decisions >= MAX_INVALID_MODEL_DECISIONS:
        reached.append("invalid-model-decisions")
    return tuple(reached)


def completion_invariant_satisfied(context: AgentLoopContext) -> bool:
    review_allows_completion = context.reviewer_verdict == "passed" or (
        context.reviewer_verdict == "passed-with-warnings"
        and context.review_warnings_accepted
    )
    dataset_result_verified = (
        context.run_completed
        and context.structured_result_exists
        and context.required_artifacts_exist
        and context.analysis_spec_current
        and context.analysis_intent_approved_and_current
    )
    return (
        (dataset_result_verified or context.literature_result_verified)
        and review_allows_completion
        and not context.unresolved_required_interaction
        and not context.pending_approval
        and not context.required_revision
    )


def determine_allowed_actions(
    context: AgentLoopContext,
    observation: StepObservation,
) -> set[AgentAction]:
    """Intersect Observation recommendations with deterministic capability rules."""

    recommended = set(observation.recommended_actions)
    if not recommended:
        return {"stop"}
    if context.capability_unsupported or observation.failure_category == "unsupported":
        return {"stop"}
    if context.input_is_irrecoverable:
        return {"stop"}

    # Every configured cap is a hard persisted loop boundary. The coordinator
    # records which boundary was reached before applying the required stop.
    if reached_loop_limits(context.counts):
        return {"stop"}

    allowed: set[AgentAction] = set()
    if "continue" in recommended and _continue_allowed(context, observation):
        allowed.add("continue")
    if "request-clarification" in recommended and _clarification_allowed(context, observation):
        allowed.add("request-clarification")
    if "revise-analysis-spec" in recommended and _spec_revision_allowed(
        context, observation
    ):
        allowed.add("revise-analysis-spec")
    if "retry-step" in recommended and _retry_allowed(context, observation):
        allowed.add("retry-step")
    if "complete" in recommended and completion_invariant_satisfied(context):
        allowed.add("complete")
    if "stop" in recommended:
        allowed.add("stop")
    if not allowed:
        allowed.add("stop")
    return allowed


def _continue_allowed(
    context: AgentLoopContext,
    observation: StepObservation,
) -> bool:
    return (
        observation.status == "succeeded"
        and observation.failure_category == "none"
        and context.next_step_key is not None
        and not context.unresolved_required_interaction
        and not context.pending_approval
        and not context.required_revision
    )


def _clarification_allowed(
    context: AgentLoopContext,
    observation: StepObservation,
) -> bool:
    return (
        context.counts.clarification_rounds < MAX_CLARIFICATION_ROUNDS
        and (
            context.clarification_is_available
            or bool(observation.unresolved_questions)
        )
    )


def _retry_allowed(
    context: AgentLoopContext,
    observation: StepObservation,
) -> bool:
    if (
        context.counts.step_retries >= MAX_STEP_RETRIES
        or context.terminal_result_exists
        or not context.failure_is_transient
        or context.failure_code not in _TRANSIENT_RETRY_CODES
    ):
        return False
    return observation.failure_category in {"runtime", "artifact", "unknown"}


def _spec_revision_allowed(
    context: AgentLoopContext,
    observation: StepObservation,
) -> bool:
    return (
        context.spec_revision_is_valid
        and context.counts.plan_revisions < MAX_PLAN_REVISIONS
        and context.counts.analysis_spec_revisions < MAX_ANALYSIS_SPEC_REVISIONS
        and not context.unresolved_required_interaction
        and not context.pending_approval
        and (
            observation.failure_category == "method"
            or (
                observation.failure_category == "review"
                and context.required_revision
            )
        )
    )


def deterministic_action(
    context: AgentLoopContext,
    observation: StepObservation,
) -> AgentAction | None:
    """Return the only safe action when policy resolves without model judgment."""

    allowed = determine_allowed_actions(context, observation)
    if len(allowed) == 1:
        return next(iter(allowed))
    priority: tuple[AgentAction, ...] = (
        "complete",
        "continue",
        "retry-step",
        "stop",
    )
    deterministic = [action for action in priority if action in allowed]
    judgment_actions = allowed.intersection(_JUDGMENT_ACTIONS)
    if len(deterministic) == 1 and not judgment_actions:
        return cast(AgentAction, deterministic[0])
    return None


__all__ = (
    "MAX_AGENT_STEPS",
    "MAX_ANALYSIS_SPEC_REVISIONS",
    "MAX_CLARIFICATION_ROUNDS",
    "MAX_INVALID_MODEL_DECISIONS",
    "MAX_MODEL_DECISIONS",
    "MAX_PLAN_REVISIONS",
    "MAX_STEP_RETRIES",
    "AgentLoopContext",
    "AgentLoopCounts",
    "completion_invariant_satisfied",
    "determine_allowed_actions",
    "deterministic_action",
    "reached_loop_limits",
)
