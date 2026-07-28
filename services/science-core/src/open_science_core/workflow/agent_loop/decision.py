from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...analysis_spec.schemas import AnalysisSpec, ScientificClarification
from .policy import AgentLoopContext, determine_allowed_actions, deterministic_action
from .prompts import (
    AGENT_NEXT_ACTION_PROMPT_VERSION,
    AGENT_NEXT_ACTION_SYSTEM_PROMPT,
    build_next_action_user_prompt,
)
from .schemas import (
    AgentAction,
    AgentDecision,
    AnalysisSpecDiff,
    StepObservation,
    agent_decision_sha256,
)


class NextActionGateway(Protocol):
    @property
    def configured(self) -> bool: ...

    @property
    def default_model(self) -> str | None: ...

    @property
    def endpoint_identity(self) -> str: ...

    async def complete_json_with_metadata(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, int]]: ...


class _ModelActionChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: AgentAction
    reason_code: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    reason: str = Field(min_length=1, max_length=4_000)
    target_step_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")


DecisionParseResult = Literal[
    "local-deterministic",
    "valid",
    "model-not-configured",
    "model-request-failed",
    "model-request-outcome-unknown",
    "model-output-invalid",
]


@dataclass(frozen=True, slots=True)
class AgentDecisionResult:
    decision: AgentDecision
    input_sha256: str
    output_sha256: str
    prompt_version: str
    generator: str
    model_used: str | None
    endpoint_identity: str | None
    used_model: bool
    parse_result: DecisionParseResult
    validation_errors: tuple[str, ...]
    token_usage: dict[str, int]


async def select_next_action(
    *,
    goal: str,
    observation: StepObservation,
    context: AgentLoopContext,
    current_analysis_spec: AnalysisSpec | None,
    plan_summary: dict[str, object] | None = None,
    answered_interactions: tuple[dict[str, object], ...] = (),
    research_context: dict[str, object] | None = None,
    gateway: NextActionGateway | None = None,
    model: str | None = None,
) -> AgentDecisionResult:
    """Select an action while retaining deterministic control-plane authority."""

    allowed = determine_allowed_actions(context, observation)
    selected_model = model or (gateway.default_model if gateway is not None else None)
    input_payload = _next_action_input_payload(
        goal=goal,
        observation=observation,
        context=context,
        current_analysis_spec=current_analysis_spec,
        plan_summary=plan_summary,
        answered_interactions=answered_interactions,
        research_context=research_context,
        model=selected_model,
    )
    input_sha256 = _sha256(input_payload)
    local_action = deterministic_action(context, observation)
    if local_action is not None:
        return _result(
            _materialize_decision(
                action=local_action,
                observation=observation,
                context=context,
                current_analysis_spec=current_analysis_spec,
                reason_code="deterministic-policy",
                reason="The deterministic action policy selected the only safe next action.",
            ),
            input_sha256=input_sha256,
            parse_result="local-deterministic",
        )

    if gateway is None or not gateway.configured or selected_model is None:
        fallback = _fallback_action(allowed)
        return _result(
            _materialize_decision(
                action=fallback,
                observation=observation,
                context=context,
                current_analysis_spec=current_analysis_spec,
                reason_code="model-not-configured",
                reason="The model selector is unavailable; the bounded local fallback was used.",
            ),
            input_sha256=input_sha256,
            parse_result="model-not-configured",
            validation_errors=("model-not-configured",),
        )

    prompt = build_next_action_user_prompt(
        goal=goal,
        plan_summary=plan_summary,
        analysis_spec=cast(dict[str, object] | None, input_payload["analysisSpec"]),
        observation=cast(dict[str, object], input_payload["observation"]),
        answered_interactions=answered_interactions,
        research_context=research_context,
        loop_counts=_count_payload(context),
        allowed_actions=sorted(allowed),
        supported_operations=cast(list[str], input_payload["supportedOperations"]),
    )
    try:
        raw, token_usage = await gateway.complete_json_with_metadata(
            AGENT_NEXT_ACTION_SYSTEM_PROMPT,
            prompt,
            selected_model,
        )
    except Exception:
        fallback = _fallback_action(allowed)
        return _result(
            _materialize_decision(
                action=fallback,
                observation=observation,
                context=context,
                current_analysis_spec=current_analysis_spec,
                reason_code="model-request-failed",
                reason="The model selector failed; the bounded local fallback was used.",
            ),
            input_sha256=input_sha256,
            parse_result="model-request-failed",
            model_used=selected_model,
            endpoint_identity=gateway.endpoint_identity or None,
            used_model=True,
            validation_errors=("model-request-failed",),
        )
    try:
        choice = _ModelActionChoice.model_validate(raw, strict=True)
        if choice.action not in allowed:
            raise ValueError("action-not-allowed")
        if choice.action in {"continue", "retry-step"}:
            if choice.target_step_key != context.next_step_key:
                raise ValueError("target-step-not-current")
        elif choice.target_step_key is not None:
            raise ValueError("target-step-not-allowed")
        decision = _materialize_decision(
            action=choice.action,
            observation=observation,
            context=context,
            current_analysis_spec=current_analysis_spec,
            reason_code=choice.reason_code,
            reason=choice.reason,
        )
    except (ValidationError, ValueError):
        fallback = _fallback_action(allowed)
        return _result(
            _materialize_decision(
                action=fallback,
                observation=observation,
                context=context,
                current_analysis_spec=current_analysis_spec,
                reason_code="model-output-invalid",
                reason="The model response violated the action policy; the bounded fallback was used.",
            ),
            input_sha256=input_sha256,
            parse_result="model-output-invalid",
            model_used=selected_model,
            endpoint_identity=gateway.endpoint_identity or None,
            used_model=True,
            validation_errors=("model-output-invalid",),
            token_usage=token_usage,
        )
    return _result(
        decision,
        input_sha256=input_sha256,
        parse_result="valid",
        model_used=selected_model,
        endpoint_identity=gateway.endpoint_identity or None,
        used_model=True,
        token_usage=token_usage,
    )


def next_action_input_sha256(
    *,
    goal: str,
    observation: StepObservation,
    context: AgentLoopContext,
    current_analysis_spec: AnalysisSpec | None,
    plan_summary: dict[str, object] | None = None,
    answered_interactions: tuple[dict[str, object], ...] = (),
    research_context: dict[str, object] | None = None,
    model: str | None,
) -> str:
    """Hash the exact bounded next-action input before any remote request."""

    return _sha256(
        _next_action_input_payload(
            goal=goal,
            observation=observation,
            context=context,
            current_analysis_spec=current_analysis_spec,
            plan_summary=plan_summary,
            answered_interactions=answered_interactions,
            research_context=research_context,
            model=model,
        )
    )


def recover_unknown_next_action(
    *,
    goal: str,
    observation: StepObservation,
    context: AgentLoopContext,
    current_analysis_spec: AnalysisSpec | None,
    plan_summary: dict[str, object] | None,
    answered_interactions: tuple[dict[str, object], ...],
    research_context: dict[str, object] | None = None,
    model: str,
    endpoint_identity: str,
) -> AgentDecisionResult:
    """Fail closed when a durable remote request may already have been sent."""

    allowed = determine_allowed_actions(context, observation)
    fallback = _fallback_action(allowed)
    input_sha256 = next_action_input_sha256(
        goal=goal,
        observation=observation,
        context=context,
        current_analysis_spec=current_analysis_spec,
        plan_summary=plan_summary,
        answered_interactions=answered_interactions,
        research_context=research_context,
        model=model,
    )
    return _result(
        _materialize_decision(
            action=fallback,
            observation=observation,
            context=context,
            current_analysis_spec=current_analysis_spec,
            reason_code="model-request-outcome-unknown",
            reason=(
                "A previous model request may have completed before the process stopped; "
                "the request was not repeated and the bounded fallback was used."
            ),
        ),
        input_sha256=input_sha256,
        parse_result="model-request-outcome-unknown",
        model_used=model,
        endpoint_identity=endpoint_identity,
        used_model=True,
        validation_errors=("model-request-outcome-unknown",),
    )


def _next_action_input_payload(
    *,
    goal: str,
    observation: StepObservation,
    context: AgentLoopContext,
    current_analysis_spec: AnalysisSpec | None,
    plan_summary: dict[str, object] | None,
    answered_interactions: tuple[dict[str, object], ...],
    research_context: dict[str, object] | None,
    model: str | None,
) -> dict[str, object]:
    allowed = determine_allowed_actions(context, observation)
    return {
        "goal": goal[:8_000],
        "planSummary": plan_summary,
        "analysisSpec": (
            current_analysis_spec.model_dump(mode="json", by_alias=True)
            if current_analysis_spec is not None
            else None
        ),
        "observation": observation.model_dump(mode="json", by_alias=True),
        "answeredInteractions": list(answered_interactions),
        "researchContext": research_context,
        "loopCounts": _count_payload(context),
        "allowedActions": sorted(allowed),
        "nextStepKey": context.next_step_key,
        "discoverySelection": (
            context.discovery_selection.model_dump(mode="json", by_alias=True)
            if context.discovery_selection is not None
            else None
        ),
        "supportedOperations": [
            "descriptive",
            "two-group-comparison",
            "correlation",
        ],
        "model": model,
    }


def _materialize_decision(
    *,
    action: AgentAction,
    observation: StepObservation,
    context: AgentLoopContext,
    current_analysis_spec: AnalysisSpec | None,
    reason_code: str,
    reason: str,
) -> AgentDecision:
    if action in {"continue", "retry-step"}:
        if context.next_step_key is None:
            action = "stop"
        else:
            return AgentDecision(
                schema_version="1",
                action=action,
                reason_code=reason_code,
                reason=reason,
                target_step_key=context.next_step_key,
                requires_user_confirmation=False,
            )
    if action == "request-clarification":
        questions = observation.unresolved_questions
        if not questions:
            action = "stop"
        else:
            requests = [
                ScientificClarification(
                    type=_clarification_type(item.code),
                    question=item.question,
                    options=[],
                )
                for item in questions
            ]
            return AgentDecision(
                schema_version="1",
                action="request-clarification",
                reason_code=reason_code,
                reason=reason,
                clarification_requests=requests,
                requires_user_confirmation=False,
            )
    if action == "revise-analysis-spec":
        proposal = safe_analysis_spec_revision(current_analysis_spec)
        if proposal is None:
            action = "stop"
        else:
            proposed, diff = proposal
            return AgentDecision(
                schema_version="1",
                action="revise-analysis-spec",
                reason_code=reason_code,
                reason=reason,
                proposed_analysis_spec=proposed,
                analysis_spec_diff=diff,
                requires_user_confirmation=True,
            )
    if action in {"complete", "stop"}:
        return AgentDecision(
            schema_version="1",
            action=action,
            reason_code=reason_code,
            reason=reason,
            requires_user_confirmation=False,
        )
    return AgentDecision(
        schema_version="1",
        action="stop",
        reason_code=reason_code if action == "stop" else "no-safe-action",
        reason=reason if action == "stop" else "No safe bounded action could be materialized.",
        requires_user_confirmation=False,
    )


def safe_analysis_spec_revision(
    current: AnalysisSpec | None,
) -> tuple[AnalysisSpec, AnalysisSpecDiff] | None:
    if current is None:
        return None
    operation = current.operation
    if operation.type == "two-group-comparison" and operation.method == "welch-t-test":
        proposed_operation = operation.model_copy(
            update={"method": "mann-whitney-u", "effect_size": "rank-biserial"}
        )
        proposed = current.model_copy(update={"operation": proposed_operation})
        fields = ["operation.method", "operation.effectSize"]
        previous = {
            "operation.method": "welch-t-test",
            "operation.effectSize": operation.effect_size,
        }
        values = {
            "operation.method": "mann-whitney-u",
            "operation.effectSize": "rank-biserial",
        }
    elif operation.type == "correlation" and operation.method == "pearson":
        proposed_operation = operation.model_copy(update={"method": "spearman"})
        proposed = current.model_copy(update={"operation": proposed_operation})
        fields = ["operation.method"]
        previous = {"operation.method": "pearson"}
        values = {"operation.method": "spearman"}
    else:
        return None
    return proposed, AnalysisSpecDiff(
        changed_fields=fields,
        previous_values=previous,
        proposed_values=values,
        reason="The verified observation indicates that the current parametric method is unsuitable.",
    )


def _fallback_action(allowed: set[AgentAction]) -> AgentAction:
    for action in ("request-clarification", "revise-analysis-spec", "stop"):
        if cast(AgentAction, action) in allowed:
            return cast(AgentAction, action)
    return "stop"


def _clarification_type(
    code: str,
) -> Literal[
    "outcome-column",
    "group-column",
    "group-values",
    "x-column",
    "y-column",
    "analysis-objective",
    "method-confirmation",
    "independence-assumption",
    "missing-value-policy",
]:
    for value in (
        "outcome-column",
        "group-column",
        "group-values",
        "x-column",
        "y-column",
        "method-confirmation",
        "independence-assumption",
        "missing-value-policy",
    ):
        if value in code:
            return cast(Any, value)
    return "analysis-objective"


def _count_payload(context: AgentLoopContext) -> dict[str, int]:
    counts = context.counts
    return {
        "agentSteps": counts.agent_steps,
        "planRevisions": counts.plan_revisions,
        "analysisSpecRevisions": counts.analysis_spec_revisions,
        "stepRetries": counts.step_retries,
        "clarificationRounds": counts.clarification_rounds,
        "modelDecisions": counts.model_decisions,
        "invalidModelDecisions": counts.invalid_model_decisions,
    }


def _sha256(value: object) -> str:
    raw = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _result(
    decision: AgentDecision,
    *,
    input_sha256: str,
    parse_result: DecisionParseResult,
    model_used: str | None = None,
    endpoint_identity: str | None = None,
    used_model: bool = False,
    validation_errors: tuple[str, ...] = (),
    token_usage: dict[str, int] | None = None,
) -> AgentDecisionResult:
    return AgentDecisionResult(
        decision=decision,
        input_sha256=input_sha256,
        output_sha256=agent_decision_sha256(decision),
        prompt_version=AGENT_NEXT_ACTION_PROMPT_VERSION,
        generator=("model-assisted-v1" if used_model else "deterministic-policy-v1"),
        model_used=model_used,
        endpoint_identity=endpoint_identity,
        used_model=used_model,
        parse_result=parse_result,
        validation_errors=validation_errors,
        token_usage=token_usage or {},
    )


__all__ = (
    "AgentDecisionResult",
    "next_action_input_sha256",
    "recover_unknown_next_action",
    "safe_analysis_spec_revision",
    "select_next_action",
)
