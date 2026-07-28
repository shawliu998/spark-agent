from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from open_science_core.workflow.agent_loop.decision import select_next_action
from open_science_core.workflow.agent_loop.policy import (
    MAX_AGENT_STEPS,
    MAX_ANALYSIS_SPEC_REVISIONS,
    MAX_CLARIFICATION_ROUNDS,
    MAX_INVALID_MODEL_DECISIONS,
    MAX_MODEL_DECISIONS,
    MAX_PLAN_REVISIONS,
    MAX_STEP_RETRIES,
    AgentLoopContext,
    AgentLoopCounts,
    completion_invariant_satisfied,
    determine_allowed_actions,
    deterministic_action,
    reached_loop_limits,
)
from open_science_core.workflow.agent_loop.schemas import (
    ObservationFact,
    StepObservation,
)


class _OverreachingGateway:
    configured = True
    default_model = "test-model"
    endpoint_identity = f"sha256:{'a' * 64}"

    async def complete_json_with_metadata(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        del system_prompt, user_prompt, model
        return (
            {
                "action": "continue",
                "reasonCode": "choose-unapproved-operation",
                "reason": "Choose an operation outside the server-provided eligible set.",
                "targetStepKey": "paper-discovery-malicious-provider",
            },
            {"inputTokens": 10, "outputTokens": 5},
        )


def _observation(
    *actions: str,
    status: str = "failed",
    failure_category: str = "runtime",
) -> StepObservation:
    return StepObservation.model_validate(
        {
            "schemaVersion": "1",
            "workflowId": "workflow-1",
            "planId": "plan-1",
            "taskId": "task-1",
            "sourceJobId": "job-1",
            "runId": "run-1",
            "reviewId": None,
            "observationType": "analysis-execution",
            "stepKey": "execute-analysis",
            "attempt": 1,
            "status": status,
            "facts": [
                ObservationFact(
                    code="bounded-fact",
                    statement="A verified structured fact is available.",
                    value=True,
                    source_type="run",
                    source_id="run-1",
                ).model_dump(mode="json", by_alias=True)
            ],
            "warnings": [],
            "unresolvedQuestions": [],
            "artifactIds": [],
            "failureCategory": failure_category,
            "recommendedActions": list(actions),
        }
    )


def _completion_context(**overrides: object) -> AgentLoopContext:
    values: dict[str, object] = {
        "run_completed": True,
        "structured_result_exists": True,
        "required_artifacts_exist": True,
        "analysis_spec_current": True,
        "analysis_intent_approved_and_current": True,
        "reviewer_verdict": "passed",
        "review_warnings_accepted": False,
        "unresolved_required_interaction": False,
        "pending_approval": False,
        "required_revision": False,
    }
    values.update(overrides)
    return AgentLoopContext(**values)  # type: ignore[arg-type]


def test_complete_requires_every_invariant() -> None:
    observation = _observation("complete", status="succeeded", failure_category="none")
    context = _completion_context()

    assert completion_invariant_satisfied(context)
    assert determine_allowed_actions(context, observation) == {"complete"}
    assert deterministic_action(context, observation) == "complete"


def test_literature_complete_requires_verified_frozen_result() -> None:
    observation = _observation("complete", status="succeeded", failure_category="none")
    context = AgentLoopContext(
        literature_result_verified=True,
        reviewer_verdict="passed",
    )

    assert completion_invariant_satisfied(context)
    assert determine_allowed_actions(context, observation) == {"complete"}
    assert not completion_invariant_satisfied(
        replace(context, literature_result_verified=False)
    )


def test_deterministic_completion_materializes_complete_decision() -> None:
    observation = _observation("complete", status="succeeded", failure_category="none")
    result = asyncio.run(
        select_next_action(
            goal="Synthesize the selected paper evidence.",
            observation=observation,
            context=AgentLoopContext(
                literature_result_verified=True,
                reviewer_verdict="passed",
            ),
            current_analysis_spec=None,
        )
    )

    assert result.decision.action == "complete"
    assert result.decision.reason_code == "deterministic-policy"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_completed", False),
        ("structured_result_exists", False),
        ("required_artifacts_exist", False),
        ("analysis_spec_current", False),
        ("analysis_intent_approved_and_current", False),
        ("reviewer_verdict", "revision-required"),
        ("unresolved_required_interaction", True),
        ("pending_approval", True),
        ("required_revision", True),
    ],
)
def test_incomplete_workflow_can_never_complete(field: str, value: object) -> None:
    observation = _observation("complete", status="succeeded", failure_category="none")
    context = replace(_completion_context(), **{field: value})

    assert not completion_invariant_satisfied(context)
    assert determine_allowed_actions(context, observation) == {"stop"}


def test_passed_warnings_require_recorded_user_acceptance() -> None:
    observation = _observation("complete", status="needs-review", failure_category="none")

    assert determine_allowed_actions(
        _completion_context(reviewer_verdict="passed-with-warnings"), observation
    ) == {"stop"}
    assert determine_allowed_actions(
        _completion_context(
            reviewer_verdict="passed-with-warnings",
            review_warnings_accepted=True,
        ),
        observation,
    ) == {"complete"}


def test_continue_requires_success_next_step_and_no_pending_gate() -> None:
    observation = _observation("continue", status="succeeded", failure_category="none")

    assert determine_allowed_actions(
        AgentLoopContext(next_step_key="collect-artifacts"), observation
    ) == {"continue"}
    assert determine_allowed_actions(AgentLoopContext(), observation) == {"stop"}
    assert determine_allowed_actions(
        AgentLoopContext(next_step_key="collect-artifacts", pending_approval=True),
        observation,
    ) == {"stop"}


def test_model_cannot_select_operation_outside_exact_server_target() -> None:
    observation = _observation(
        "continue",
        "stop",
        status="succeeded",
        failure_category="none",
    )
    result = asyncio.run(
        select_next_action(
            goal="Search only the approved operations.",
            observation=observation,
            context=AgentLoopContext(
                next_step_key="paper-discovery-query-approved-crossref"
            ),
            current_analysis_spec=None,
            gateway=_OverreachingGateway(),
        )
    )

    assert result.parse_result == "model-output-invalid"
    assert result.decision.action == "stop"
    assert result.decision.target_step_key is None


def test_transient_infrastructure_failure_allows_bounded_retry() -> None:
    observation = _observation("retry-step", "stop")
    context = AgentLoopContext(
        failure_code="runtime-temporarily-unavailable",
        failure_is_transient=True,
    )

    assert determine_allowed_actions(context, observation) == {"retry-step", "stop"}
    assert deterministic_action(context, observation) is None


@pytest.mark.parametrize("code", ["connector-unavailable", "rate-limited"])
def test_adapter_granted_discovery_failures_allow_bounded_retry(code: str) -> None:
    assert determine_allowed_actions(
        AgentLoopContext(failure_code=code, failure_is_transient=True),
        _observation("retry-step", "stop"),
    ) == {"retry-step", "stop"}


@pytest.mark.parametrize(
    "context",
    [
        AgentLoopContext(
            failure_code="analysis-column-missing",
            failure_is_transient=True,
        ),
        AgentLoopContext(
            failure_code="runtime-temporarily-unavailable",
            failure_is_transient=False,
        ),
        AgentLoopContext(
            failure_code="runtime-temporarily-unavailable",
            failure_is_transient=True,
            terminal_result_exists=True,
        ),
        AgentLoopContext(
            counts=AgentLoopCounts(step_retries=MAX_STEP_RETRIES),
            failure_code="runtime-temporarily-unavailable",
            failure_is_transient=True,
        ),
    ],
)
def test_retry_rejects_non_transient_unknown_terminal_or_exhausted_failure(
    context: AgentLoopContext,
) -> None:
    assert determine_allowed_actions(context, _observation("retry-step")) == {"stop"}


def test_method_failure_cannot_be_disguised_as_retry() -> None:
    observation = _observation(
        "retry-step",
        "revise-analysis-spec",
        "stop",
        failure_category="method",
    )
    context = AgentLoopContext(
        failure_code="runtime-timeout",
        failure_is_transient=True,
        spec_revision_is_valid=True,
    )

    assert determine_allowed_actions(context, observation) == {
        "revise-analysis-spec",
        "stop",
    }


def test_spec_revision_requires_safe_current_spec_and_revision_budget() -> None:
    observation = _observation("revise-analysis-spec", failure_category="review")

    assert determine_allowed_actions(
        AgentLoopContext(spec_revision_is_valid=True, required_revision=True), observation
    ) == {"revise-analysis-spec"}
    assert determine_allowed_actions(AgentLoopContext(), observation) == {"stop"}
    assert determine_allowed_actions(
        AgentLoopContext(
            counts=AgentLoopCounts(analysis_spec_revisions=MAX_ANALYSIS_SPEC_REVISIONS),
            spec_revision_is_valid=True,
            required_revision=True,
        ),
        observation,
    ) == {"stop"}
    assert determine_allowed_actions(
        AgentLoopContext(
            counts=AgentLoopCounts(plan_revisions=MAX_PLAN_REVISIONS),
            spec_revision_is_valid=True,
            required_revision=True,
        ),
        observation,
    ) == {"stop"}


@pytest.mark.parametrize(
    "context",
    [
        AgentLoopContext(
            spec_revision_is_valid=True,
            required_revision=True,
            pending_approval=True,
        ),
        AgentLoopContext(
            spec_revision_is_valid=True,
            required_revision=True,
            unresolved_required_interaction=True,
        ),
        AgentLoopContext(spec_revision_is_valid=True, required_revision=False),
    ],
)
def test_review_revision_never_bypasses_existing_human_gates(
    context: AgentLoopContext,
) -> None:
    observation = _observation(
        "revise-analysis-spec",
        "stop",
        failure_category="review",
    )

    assert determine_allowed_actions(context, observation) == {"stop"}


def test_request_clarification_requires_available_question_and_budget() -> None:
    observation = _observation("request-clarification", "stop", failure_category="review")

    assert determine_allowed_actions(
        AgentLoopContext(clarification_is_available=True), observation
    ) == {"request-clarification", "stop"}
    assert determine_allowed_actions(AgentLoopContext(), observation) == {"stop"}
    assert determine_allowed_actions(
        AgentLoopContext(
            counts=AgentLoopCounts(clarification_rounds=MAX_CLARIFICATION_ROUNDS),
            clarification_is_available=True,
        ),
        observation,
    ) == {"stop"}


@pytest.mark.parametrize(
    "context",
    [
        AgentLoopContext(capability_unsupported=True),
        AgentLoopContext(input_is_irrecoverable=True),
    ],
)
def test_unsupported_or_irrecoverable_input_forces_stop(
    context: AgentLoopContext,
) -> None:
    observation = _observation(
        "continue",
        "request-clarification",
        "revise-analysis-spec",
        "retry-step",
        "complete",
        "stop",
        failure_category="unsupported",
    )
    assert determine_allowed_actions(context, observation) == {"stop"}


@pytest.mark.parametrize(
    "counts",
    [
        AgentLoopCounts(agent_steps=MAX_AGENT_STEPS),
        AgentLoopCounts(plan_revisions=MAX_PLAN_REVISIONS),
        AgentLoopCounts(analysis_spec_revisions=MAX_ANALYSIS_SPEC_REVISIONS),
        AgentLoopCounts(step_retries=MAX_STEP_RETRIES),
        AgentLoopCounts(clarification_rounds=MAX_CLARIFICATION_ROUNDS),
        AgentLoopCounts(model_decisions=MAX_MODEL_DECISIONS),
        AgentLoopCounts(invalid_model_decisions=MAX_INVALID_MODEL_DECISIONS),
    ],
)
def test_global_hard_limit_forces_stop(counts: AgentLoopCounts) -> None:
    observation = _observation("continue", "stop", status="succeeded", failure_category="none")
    context = AgentLoopContext(counts=counts, next_step_key="collect-artifacts")

    assert determine_allowed_actions(context, observation) == {"stop"}
    assert deterministic_action(context, observation) == "stop"


def test_reports_every_limit_from_persisted_counts() -> None:
    reached = reached_loop_limits(
        AgentLoopCounts(
            agent_steps=MAX_AGENT_STEPS,
            plan_revisions=MAX_PLAN_REVISIONS,
            analysis_spec_revisions=MAX_ANALYSIS_SPEC_REVISIONS,
            step_retries=MAX_STEP_RETRIES,
            clarification_rounds=MAX_CLARIFICATION_ROUNDS,
            model_decisions=MAX_MODEL_DECISIONS,
            invalid_model_decisions=MAX_INVALID_MODEL_DECISIONS,
        )
    )

    assert reached == (
        "agent-steps",
        "plan-revisions",
        "analysis-spec-revisions",
        "step-retries",
        "clarification-rounds",
        "model-decisions",
        "invalid-model-decisions",
    )


def test_negative_persisted_count_is_invalid() -> None:
    with pytest.raises(ValueError):
        AgentLoopCounts(step_retries=-1)
