from __future__ import annotations

from typing import cast

import pytest
from pydantic import ValidationError

from open_science_core.analysis_spec.results import StructuredAnalysisResult
from open_science_core.analysis_spec.reviewer import (
    AnalysisReviewCheck,
    AnalysisSpecReview,
    ReviewStatus,
)
from open_science_core.analysis_spec.validator import (
    ExactCorrelationPreflight,
    ExactTwoGroupPreflight,
)
from open_science_core.workflow.agent_loop.observer import (
    ObservationContext,
    VerifiedFailureSummary,
    build_analysis_result_observation,
    build_correlation_preflight_observation,
    build_failure_observation,
    build_reviewer_observation,
    build_two_group_preflight_observation,
)


def _context(
    *,
    observation_type: str = "analysis-execution",
    task_id: str | None = "task-1",
) -> ObservationContext:
    return ObservationContext(
        workflow_id="workflow-1",
        plan_id="plan-1" if observation_type != "pre-plan" else None,
        task_id=task_id,
        observation_type=observation_type,  # type: ignore[arg-type]
        step_key="execute-analysis",
        attempt=1,
        source_job_id="job-1",
    )


def _correlation_result(*, warnings: list[str] | None = None) -> StructuredAnalysisResult:
    return StructuredAnalysisResult.model_validate(
        {
            "schemaVersion": "1",
            "objective": "Assess the association between x and y.",
            "operationType": "correlation",
            "datasetSourceId": "dataset-1",
            "datasetContentHash": "a" * 64,
            "datasetProfileHash": "b" * 64,
            "requestedMethod": "pearson",
            "resolvedMethod": "pearson",
            "methodSelectionReason": "Pearson was explicitly requested.",
            "sampleSummary": {"totalRows": 12, "analyzedRows": 10, "missingRows": 2},
            "result": {
                "type": "correlation",
                "xColumn": "x",
                "yColumn": "y",
                "sampleSize": 10,
                "missingPairs": 2,
                "correlation": 0.4,
                "pValue": 0.03,
                "confidenceInterval": (0.05, 0.65),
            },
            "warnings": warnings or [],
            "limitations": ["Correlation does not establish causation."],
        }
    )


def _review(verdict: str, status: str) -> AnalysisSpecReview:
    return AnalysisSpecReview.model_validate(
        {
            "schemaVersion": "1",
            "verdict": verdict,
            "checks": [
                AnalysisReviewCheck(
                    code="result-evidence-check",
                    category="results",
                    status=cast(ReviewStatus, status),
                    message="The deterministic result evidence was checked.",
                    artifact_id=None,
                )
            ],
            "requiredRevisions": (
                ["Revise the method output."] if verdict == "revision-required" else []
            ),
            "conclusion": "The deterministic review completed.",
        }
    )


def test_builds_successful_structured_result_observation_without_raw_logs() -> None:
    observation = build_analysis_result_observation(
        _context(),
        analysis_spec_id="spec-1",
        structured_result_id="result-1",
        run_id="run-1",
        result=_correlation_result(warnings=["Interpret the association cautiously."]),
        artifact_ids=["artifact-1", "artifact-2"],
    )

    facts = {fact.code: fact.value for fact in observation.facts}
    assert observation.status == "succeeded"
    assert observation.failure_category == "none"
    assert observation.run_id == "run-1"
    assert observation.recommended_actions == ["continue"]
    assert facts["resolved-method"] == "pearson"
    assert facts["complete-pair-count"] == 10
    assert facts["p-value-reported"] == 0.03
    assert facts["correlation-reported"] == 0.4
    serialized = observation.model_dump_json()
    assert "traceback" not in serialized.lower()
    assert "stderr" not in serialized.lower()


def test_multiple_structured_warnings_have_unique_bounded_codes() -> None:
    observation = build_analysis_result_observation(
        _context(),
        analysis_spec_id="spec-1",
        structured_result_id="result-1",
        run_id="run-1",
        result=_correlation_result(warnings=["First warning.", "Second warning."]),
        artifact_ids=[],
    )

    assert [warning.code for warning in observation.warnings] == [
        "structured-result-warning-1",
        "structured-result-warning-2",
    ]


def test_builds_transient_failure_from_safe_summary_only() -> None:
    observation = build_failure_observation(
        _context(),
        VerifiedFailureSummary(
            run_id="run-1",
            error_code="runtime-temporarily-unavailable",
            user_message="The restricted runtime is temporarily unavailable.",
            failure_stage="execution",
            failure_category="runtime",
            artifact_ids=["artifact-1"],
            external_side_effects=False,
            safe_to_retry=True,
            requires_spec_revision=False,
            requires_user_input=False,
        ),
    )

    assert observation.status == "failed"
    assert observation.failure_category == "runtime"
    assert observation.recommended_actions == ["retry-step", "stop"]
    assert observation.artifact_ids == ["artifact-1"]
    assert observation.warnings[0].message == (
        "The restricted runtime is temporarily unavailable."
    )


def test_safe_failure_summary_rejects_raw_or_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        VerifiedFailureSummary.model_validate(
            {
                "runId": "run-1",
                "errorCode": "runtime-failed",
                "userMessage": "The run failed.",
                "failureStage": "execution",
                "failureCategory": "runtime",
                "artifactIds": [],
                "externalSideEffects": False,
                "safeToRetry": False,
                "requiresSpecRevision": False,
                "requiresUserInput": False,
                "stderr": "raw traceback must not enter an observation",
            }
        )


@pytest.mark.parametrize("field", ["workflow_id", "step_key", "source_job_id"])
def test_context_rejects_blank_identity(field: str) -> None:
    values = {
        "workflow_id": "workflow-1",
        "plan_id": "plan-1",
        "task_id": "task-1",
        "observation_type": "step-output",
        "step_key": "inspect-dataset",
        "attempt": 1,
        "source_job_id": "job-1",
    }
    values[field] = "  "
    with pytest.raises(ValueError):
        ObservationContext(**values)  # type: ignore[arg-type]


def test_two_group_preflight_recommends_revision_for_zero_variance() -> None:
    observation = build_two_group_preflight_observation(
        _context(observation_type="pre-plan", task_id=None),
        preflight_id="preflight-1",
        preflight=ExactTwoGroupPreflight(
            outcome_column="score",
            group_column="group",
            valid_counts={"treatment": 5, "control": 5},
            non_constant_groups={"treatment": True, "control": False},
        ),
        resolved_method="welch-t-test",
    )

    assert observation.status == "blocked"
    assert observation.failure_category == "method"
    assert observation.recommended_actions == ["revise-analysis-spec", "stop"]
    assert observation.task_id is None
    assert observation.observation_type == "pre-plan"


def test_two_group_preflight_stops_for_irrecoverable_small_sample() -> None:
    observation = build_two_group_preflight_observation(
        _context(observation_type="pre-plan", task_id=None),
        preflight_id="preflight-1",
        preflight=ExactTwoGroupPreflight(
            outcome_column="score",
            group_column="group",
            valid_counts={"treatment": 2, "control": 5},
            non_constant_groups={"treatment": True, "control": True},
        ),
        resolved_method="mann-whitney-u",
    )

    assert observation.failure_category == "input"
    assert observation.recommended_actions == ["stop"]


@pytest.mark.parametrize(
    ("pair_count", "status", "actions"),
    [(2, "blocked", ["stop"]), (3, "succeeded", ["continue"])],
)
def test_correlation_preflight_classifies_sample_count(
    pair_count: int,
    status: str,
    actions: list[str],
) -> None:
    observation = build_correlation_preflight_observation(
        _context(observation_type="pre-plan", task_id=None),
        preflight_id="preflight-1",
        preflight=ExactCorrelationPreflight(
            x_column="x",
            y_column="y",
            valid_pair_count=pair_count,
        ),
    )

    assert observation.status == status
    assert observation.recommended_actions == actions


@pytest.mark.parametrize(
    ("verdict", "check_status", "status", "actions"),
    [
        ("passed", "passed", "succeeded", ["complete"]),
        (
            "passed-with-warnings",
            "warning",
            "needs-review",
            ["complete", "request-clarification"],
        ),
        (
            "revision-required",
            "failed",
            "blocked",
            ["revise-analysis-spec", "request-clarification", "stop"],
        ),
        ("blocked", "failed", "blocked", ["request-clarification", "stop"]),
        ("failed", "failed", "failed", ["retry-step", "stop"]),
    ],
)
def test_reviewer_observation_maps_bounded_verdicts(
    verdict: str,
    check_status: str,
    status: str,
    actions: list[str],
) -> None:
    observation = build_reviewer_observation(
        _context(observation_type="review", task_id=None),
        review_id="review-1",
        review=_review(verdict, check_status),
        artifact_ids=["artifact-1"],
    )

    assert observation.status == status
    assert observation.recommended_actions == actions
    assert observation.review_id == "review-1"
    if verdict == "revision-required":
        facts = {fact.code: fact.value for fact in observation.facts}
        assert facts["required-revisions"] == ["Revise the method output."]
