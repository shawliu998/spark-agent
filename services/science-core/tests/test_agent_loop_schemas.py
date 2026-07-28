from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from open_science_core.analysis_spec.schemas import (
    AnalysisSpec,
    ScientificClarification,
)
from open_science_core.db import Base
from open_science_core.models import AgentDecisionRecord
from open_science_core.workflow.agent_loop.decision import next_action_input_sha256
from open_science_core.workflow.agent_loop.policy import AgentLoopContext
from open_science_core.workflow.agent_loop.prompts import build_next_action_user_prompt
from open_science_core.workflow.agent_loop.schemas import (
    AgentAction,
    AgentDecision,
    AnalysisSpecDiff,
    ObservationFact,
    StepObservation,
    agent_decision_sha256,
    step_observation_sha256,
)
from open_science_core.workflow.schemas import AgentDecisionEventData


def _observation(**updates: object) -> StepObservation:
    values: dict[str, object] = {
        "schema_version": "1",
        "workflow_id": "workflow-1",
        "plan_id": "plan-1",
        "task_id": "task-1",
        "source_job_id": "job-1",
        "run_id": "run-1",
        "review_id": None,
        "observation_type": "analysis-execution",
        "step_key": "execute-analysis",
        "attempt": 1,
        "status": "succeeded",
        "facts": [
            ObservationFact(
                code="method-used",
                statement="The analysis used Welch's t-test.",
                value="welch-t-test",
                source_type="structured-result",
                source_id="result-1",
            )
        ],
        "warnings": [],
        "unresolved_questions": [],
        "artifact_ids": ["artifact-1"],
        "failure_category": "none",
        "recommended_actions": ["continue"],
    }
    values.update(updates)
    return StepObservation.model_validate(values)


def _analysis_spec(*, method: str = "welch-t-test") -> AnalysisSpec:
    return AnalysisSpec.model_validate(
        {
            "schemaVersion": "1",
            "objective": "Compare score between treatment and control.",
            "datasetSourceId": "dataset-1",
            "datasetContentHash": "a" * 64,
            "datasetProfileHash": "b" * 64,
            "operation": {
                "type": "two-group-comparison",
                "outcomeColumn": "score",
                "groupColumn": "group",
                "groups": ("treatment", "control"),
                "method": method,
                "effectSize": (
                    "hedges-g" if method == "welch-t-test" else "rank-biserial"
                ),
                "checkAssumptions": True,
                "plot": "boxplot",
            },
            "missingValuePolicy": "drop-per-operation",
            "confidenceLevel": 0.95,
            "randomSeed": 0,
            "assumptions": ["Rows are independent."],
            "limitations": ["This analysis is observational."],
        }
    )


def test_observation_is_strict_canonical_and_alias_stable() -> None:
    observation = _observation()

    assert step_observation_sha256(observation) == step_observation_sha256(
        StepObservation.model_validate(
            observation.model_dump(mode="json", by_alias=True)
        )
    )
    assert observation.model_dump(mode="json", by_alias=True)["sourceJobId"] == "job-1"
    with pytest.raises(ValidationError):
        StepObservation.model_validate(
            {**observation.model_dump(mode="json"), "unexpected": True}
        )


def test_research_context_is_hashed_and_included_in_next_action_prompt() -> None:
    observation = _observation()
    context = AgentLoopContext(next_step_key="execute-analysis")
    first: dict[str, object] = {
        "id": "snapshot-1",
        "sha256": "a" * 64,
        "items": [{"id": "memory-1"}],
    }
    second: dict[str, object] = {
        "id": "snapshot-2",
        "sha256": "b" * 64,
        "items": [{"id": "memory-2"}],
    }
    first_hash = next_action_input_sha256(
        goal="Compare groups.",
        observation=observation,
        context=context,
        current_analysis_spec=None,
        plan_summary=None,
        answered_interactions=(),
        model="test-model",
        research_context=first,
    )
    second_hash = next_action_input_sha256(
        goal="Compare groups.",
        observation=observation,
        context=context,
        current_analysis_spec=None,
        plan_summary=None,
        answered_interactions=(),
        model="test-model",
        research_context=second,
    )
    assert first_hash != second_hash
    prompt = build_next_action_user_prompt(
        goal="Compare groups.",
        plan_summary=None,
        analysis_spec=None,
        observation=observation.model_dump(mode="json", by_alias=True),
        answered_interactions=(),
        research_context=first,
        loop_counts={"agentSteps": 0},
        allowed_actions=["continue"],
        supported_operations=["descriptive"],
    )
    assert '"researchContext":{"id":"snapshot-1"' in prompt


def test_decision_event_requires_paired_research_context_provenance() -> None:
    payload = {
        "observationId": "observation-1",
        "decisionId": "decision-1",
        "action": "stop",
        "expectedWorkflowRevision": 1,
        "reasonCode": "no-safe-action",
    }
    valid = AgentDecisionEventData.model_validate(
        {
            **payload,
            "researchContextSnapshotId": "snapshot-1",
            "researchContextSnapshotSha256": "a" * 64,
        }
    )
    assert valid.research_context_snapshot_id == "snapshot-1"
    with pytest.raises(ValidationError, match="must be recorded together"):
        AgentDecisionEventData.model_validate(
            {**payload, "researchContextSnapshotId": "snapshot-1"}
        )
    with pytest.raises(ValidationError):
        AgentDecisionEventData.model_validate(
            {**payload, "researchContextSnapshotSha256": "not-a-sha"}
        )


def test_observation_rejects_duplicate_facts_and_invalid_scope() -> None:
    fact = _observation().facts[0]
    with pytest.raises(ValidationError, match="codes must be unique"):
        _observation(facts=[fact, fact])
    with pytest.raises(ValidationError, match="may omit task_id"):
        _observation(task_id=None)
    with pytest.raises(ValidationError, match="failure_category"):
        _observation(status="failed", failure_category="none")


def test_review_observation_may_omit_task_but_not_plan() -> None:
    observation = _observation(
        task_id=None,
        run_id=None,
        review_id="review-1",
        observation_type="review",
        step_key="review-analysis",
    )
    assert observation.task_id is None
    with pytest.raises(ValidationError, match="may omit plan_id"):
        _observation(
            plan_id=None,
            task_id=None,
            run_id=None,
            review_id="review-1",
            observation_type="review",
            step_key="review-analysis",
        )


@pytest.mark.parametrize("action", ["continue", "retry-step"])
def test_targeted_automatic_decisions_are_valid(action: str) -> None:
    decision = AgentDecision.model_validate(
        {
            "schemaVersion": "1",
            "action": action,
            "reasonCode": "next-step-ready",
            "reason": "The bounded next step is ready.",
            "targetStepKey": "review-analysis",
            "clarificationRequests": [],
            "proposedAnalysisSpec": None,
            "analysisSpecDiff": None,
            "requiresUserConfirmation": False,
        }
    )
    assert len(agent_decision_sha256(decision)) == 64


def test_request_clarification_requires_a_request_and_automatic_interaction() -> None:
    request = ScientificClarification(
        type="outcome-column",
        question="Which column is the outcome?",
        options=[],
    )
    valid = AgentDecision(
        schema_version="1",
        action="request-clarification",
        reason_code="outcome-ambiguous",
        reason="Multiple numeric columns could be the outcome.",
        clarification_requests=[request],
        requires_user_confirmation=False,
    )
    assert valid.clarification_requests == [request]
    with pytest.raises(ValidationError):
        AgentDecision(
            schema_version="1",
            action="request-clarification",
            reason_code="outcome-ambiguous",
            reason="More information is required.",
            clarification_requests=[],
            requires_user_confirmation=False,
        )


def test_spec_revision_requires_a_real_diff_and_confirmation() -> None:
    previous = _analysis_spec()
    proposed = _analysis_spec(method="mann-whitney-u")
    diff = AnalysisSpecDiff(
        changed_fields=["operation.method", "operation.effectSize"],
        previous_values={
            "operation.method": "welch-t-test",
            "operation.effectSize": "hedges-g",
        },
        proposed_values={
            "operation.method": "mann-whitney-u",
            "operation.effectSize": "rank-biserial",
        },
        reason="One group has zero variance.",
    )
    decision = AgentDecision(
        schema_version="1",
        action="revise-analysis-spec",
        reason_code="welch-group-variance-zero",
        reason="The approved method is not suitable for the observed data.",
        proposed_analysis_spec=proposed,
        analysis_spec_diff=diff,
        requires_user_confirmation=True,
    )
    assert proposed != previous
    assert decision.analysis_spec_diff == diff
    with pytest.raises(ValidationError, match="requires a proposed spec and diff"):
        AgentDecision(
            schema_version="1",
            action="revise-analysis-spec",
            reason_code="welch-group-variance-zero",
            reason="The method must change.",
            proposed_analysis_spec=proposed,
            requires_user_confirmation=True,
        )


@pytest.mark.parametrize("action", ["complete", "stop"])
def test_terminal_decisions_reject_pending_work(action: AgentAction) -> None:
    valid = AgentDecision(
        schema_version="1",
        action=action,
        reason_code="review-passed" if action == "complete" else "unsupported",
        reason="The bounded workflow reached a terminal decision.",
        requires_user_confirmation=False,
    )
    assert valid.action == action
    with pytest.raises(ValidationError, match="cannot include pending work"):
        AgentDecision(
            schema_version="1",
            action=action,
            reason_code="invalid-terminal-shape",
            reason="Terminal decisions cannot queue another step.",
            target_step_key="execute-analysis",
            requires_user_confirmation=False,
        )


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentDecision.model_validate(
            {
                "schemaVersion": "1",
                "action": "invent-tool",
                "reasonCode": "invalid-action",
                "reason": "This must never be accepted.",
                "requiresUserConfirmation": False,
            }
        )


def test_non_revision_decision_persists_optional_json_as_sql_null() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            AgentDecisionRecord(
                id="decision-1",
                workflow_id="workflow-1",
                observation_id="observation-1",
                schema_version="1",
                decision_revision=1,
                expected_workflow_revision=1,
                action="complete",
                reason_code="review-passed",
                reason="The deterministic review passed.",
                target_step_key=None,
                proposed_analysis_spec_json=None,
                proposed_analysis_spec_sha256=None,
                analysis_spec_diff_json=None,
                clarification_requests_json=[],
                requires_user_confirmation=False,
                generator="deterministic-policy",
                prompt_version=None,
                model=None,
                model_invocation_id=None,
                input_sha256="a" * 64,
                output_sha256="b" * 64,
                status="proposed",
                applied_at=None,
            )
        )
        session.commit()
        stored = session.execute(
            text(
                "SELECT proposed_analysis_spec_json, analysis_spec_diff_json "
                "FROM agent_decisions WHERE id = 'decision-1'"
            )
        ).one()
        assert stored == (None, None)
    engine.dispose()
