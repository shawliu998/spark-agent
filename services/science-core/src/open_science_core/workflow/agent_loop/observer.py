from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ...analysis_spec.results import StructuredAnalysisResult
from ...analysis_spec.reviewer import AnalysisSpecReview
from ...analysis_spec.validator import ExactCorrelationPreflight, ExactTwoGroupPreflight
from .schemas import (
    AgentAction,
    ObservationFact,
    ObservationWarning,
    StepObservation,
)

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


@dataclass(frozen=True, slots=True)
class ObservationContext:
    workflow_id: str
    plan_id: str | None
    task_id: str | None
    observation_type: Literal["pre-plan", "step-output", "analysis-execution", "review"]
    step_key: str
    attempt: int
    source_job_id: str

    def __post_init__(self) -> None:
        if not self.workflow_id.strip() or not self.step_key.strip() or not self.source_job_id.strip():
            raise ValueError("observation identity fields must be non-empty")
        if self.plan_id is not None and not self.plan_id.strip():
            raise ValueError("observation plan ID must be non-empty when present")
        if self.task_id is not None and not self.task_id.strip():
            raise ValueError("observation task ID must be non-empty when present")
        if self.attempt < 1:
            raise ValueError("observation attempt must be positive")


class VerifiedFailureSummary(BaseModel):
    """Bounded user-safe failure evidence read from durable workflow records."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_id: NonEmptyText | None = None
    error_code: NonEmptyText
    user_message: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
    ]
    failure_stage: NonEmptyText
    failure_category: Literal[
        "input",
        "method",
        "runtime",
        "artifact",
        "review",
        "unsupported",
        "unknown",
    ]
    artifact_ids: list[NonEmptyText] = Field(default_factory=list, max_length=100)
    external_side_effects: bool
    safe_to_retry: bool
    requires_spec_revision: bool
    requires_user_input: bool


def _fact(
    code: str,
    statement: str,
    value: object,
    *,
    source_type: str,
    source_id: str,
) -> ObservationFact:
    return ObservationFact.model_validate(
        {
            "code": code,
            "statement": statement,
            "value": value,
            "source_type": source_type,
            "source_id": source_id,
        }
    )


def _warning(
    code: str,
    message: str,
    *,
    severity: Literal["info", "warning", "error"],
    source_id: str | None,
) -> ObservationWarning:
    return ObservationWarning(
        code=code,
        message=message,
        severity=severity,
        source_id=source_id,
    )


def _observation(
    context: ObservationContext,
    *,
    status: Literal["succeeded", "failed", "blocked", "needs-review"],
    facts: list[ObservationFact],
    warnings: list[ObservationWarning],
    artifact_ids: list[str],
    failure_category: Literal[
        "none",
        "input",
        "method",
        "runtime",
        "artifact",
        "review",
        "unsupported",
        "unknown",
    ],
    recommended_actions: list[AgentAction],
    run_id: str | None = None,
    review_id: str | None = None,
) -> StepObservation:
    return StepObservation(
        schema_version="1",
        workflow_id=context.workflow_id,
        plan_id=context.plan_id,
        task_id=context.task_id,
        run_id=run_id,
        review_id=review_id,
        observation_type=context.observation_type,
        step_key=context.step_key,
        attempt=context.attempt,
        source_job_id=context.source_job_id,
        status=status,
        facts=facts,
        warnings=warnings,
        unresolved_questions=[],
        artifact_ids=artifact_ids,
        failure_category=failure_category,
        recommended_actions=recommended_actions,
    )


def build_analysis_result_observation(
    context: ObservationContext,
    *,
    analysis_spec_id: str,
    structured_result_id: str,
    run_id: str,
    result: StructuredAnalysisResult,
    artifact_ids: list[str],
) -> StepObservation:
    """Build facts only from a schema-validated persisted structured result."""

    if not analysis_spec_id.strip() or not structured_result_id.strip() or not run_id.strip():
        raise ValueError("analysis result source IDs must be non-empty")
    operation_result = result.result
    facts = [
        _fact(
            "analysis-spec-used",
            "The run used the current approved AnalysisSpec.",
            analysis_spec_id,
            source_type="analysis-spec",
            source_id=analysis_spec_id,
        ),
        _fact(
            "operation-executed",
            "The structured result records the executed operation.",
            result.operation_type,
            source_type="structured-result",
            source_id=structured_result_id,
        ),
        _fact(
            "requested-method",
            "The structured result records the requested method.",
            result.requested_method,
            source_type="structured-result",
            source_id=structured_result_id,
        ),
        _fact(
            "resolved-method",
            "The structured result records the resolved method.",
            result.resolved_method,
            source_type="structured-result",
            source_id=structured_result_id,
        ),
        _fact(
            "dataset-content-hash",
            "The result is bound to the immutable dataset content hash.",
            result.dataset_content_hash,
            source_type="structured-result",
            source_id=structured_result_id,
        ),
        _fact(
            "dataset-profile-hash",
            "The result is bound to the validated dataset profile hash.",
            result.dataset_profile_hash,
            source_type="structured-result",
            source_id=structured_result_id,
        ),
        _fact(
            "sample-summary",
            "The structured result records analyzed and missing row counts.",
            result.sample_summary.model_dump(mode="json", by_alias=True),
            source_type="structured-result",
            source_id=structured_result_id,
        ),
    ]
    if operation_result.type == "descriptive":
        facts.append(
            _fact(
                "column-sample-counts",
                "The structured result records sample and missing counts per column.",
                {
                    item.column: {
                        "sampleSize": item.sample_size,
                        "missingCount": item.missing_count,
                    }
                    for item in operation_result.columns
                },
                source_type="structured-result",
                source_id=structured_result_id,
            )
        )
    elif operation_result.type == "two-group-comparison":
        facts.extend(
            [
                _fact(
                    "group-sample-sizes",
                    "The structured result records valid observations per selected group.",
                    operation_result.sample_sizes,
                    source_type="structured-result",
                    source_id=structured_result_id,
                ),
                _fact(
                    "p-value-reported",
                    "The structured result reports a finite p-value.",
                    operation_result.p_value,
                    source_type="structured-result",
                    source_id=structured_result_id,
                ),
                _fact(
                    "effect-size-reported",
                    "The structured result reports the method-compatible effect size.",
                    {
                        "name": operation_result.effect_size_name,
                        "value": operation_result.effect_size,
                    },
                    source_type="structured-result",
                    source_id=structured_result_id,
                ),
                _fact(
                    "confidence-interval-reported",
                    "The structured result reports an effect-size confidence interval.",
                    list(operation_result.confidence_interval),
                    source_type="structured-result",
                    source_id=structured_result_id,
                ),
            ]
        )
    else:
        facts.extend(
            [
                _fact(
                    "complete-pair-count",
                    "The structured result records the complete-pair sample size.",
                    operation_result.sample_size,
                    source_type="structured-result",
                    source_id=structured_result_id,
                ),
                _fact(
                    "p-value-reported",
                    "The structured result reports a finite p-value.",
                    operation_result.p_value,
                    source_type="structured-result",
                    source_id=structured_result_id,
                ),
                _fact(
                    "correlation-reported",
                    "The structured result reports the correlation coefficient.",
                    operation_result.correlation,
                    source_type="structured-result",
                    source_id=structured_result_id,
                ),
            ]
        )
        if operation_result.confidence_interval is not None:
            facts.append(
                _fact(
                    "confidence-interval-reported",
                    "The structured result reports a correlation confidence interval.",
                    list(operation_result.confidence_interval),
                    source_type="structured-result",
                    source_id=structured_result_id,
                )
            )
    warnings = [
        _warning(
            f"structured-result-warning-{index}",
            message,
            severity="warning",
            source_id=structured_result_id,
        )
        for index, message in enumerate(result.warnings, start=1)
    ]
    return _observation(
        context,
        status="succeeded",
        facts=facts,
        warnings=warnings,
        artifact_ids=artifact_ids,
        failure_category="none",
        recommended_actions=["continue"],
        run_id=run_id,
    )


def build_failure_observation(
    context: ObservationContext,
    failure: VerifiedFailureSummary,
) -> StepObservation:
    source_id = failure.run_id or context.workflow_id
    facts = [
        _fact(
            "failure-code",
            "The durable error summary records a bounded failure code.",
            failure.error_code,
            source_type="run" if failure.run_id is not None else "workflow",
            source_id=source_id,
        ),
        _fact(
            "failure-stage",
            "The failure occurred at the recorded workflow stage.",
            failure.failure_stage,
            source_type="run" if failure.run_id is not None else "workflow",
            source_id=source_id,
        ),
        _fact(
            "external-side-effects",
            "The durable failure summary records whether external side effects occurred.",
            failure.external_side_effects,
            source_type="run" if failure.run_id is not None else "workflow",
            source_id=source_id,
        ),
        _fact(
            "safe-to-retry",
            "The durable failure classification records whether retry is safe.",
            failure.safe_to_retry,
            source_type="run" if failure.run_id is not None else "workflow",
            source_id=source_id,
        ),
    ]
    actions: list[AgentAction] = []
    if failure.safe_to_retry:
        actions.append("retry-step")
    if failure.requires_spec_revision:
        actions.append("revise-analysis-spec")
    if failure.requires_user_input:
        actions.append("request-clarification")
    actions.append("stop")
    return _observation(
        context,
        status="failed",
        facts=facts,
        warnings=[
            _warning(
                failure.error_code,
                failure.user_message,
                severity="error",
                source_id=failure.run_id,
            )
        ],
        artifact_ids=list(failure.artifact_ids),
        failure_category=failure.failure_category,
        recommended_actions=list(dict.fromkeys(actions)),
        run_id=failure.run_id,
    )


def build_discovery_observation(
    context: ObservationContext,
    *,
    invocation_id: str,
    query_id: str,
    provider: str,
    returned_count: int,
    novel_candidate_count: int,
    duplicate_count: int,
    candidate_set_sha256: str | None,
    remaining_approved_operations: int,
    consecutive_no_novelty: int,
    error_code: str | None,
    retry_safe: bool,
    outcome_unknown: bool,
    stop_reached: bool,
) -> StepObservation:
    """Convert only durable discovery counters into an Agent-loop observation."""

    facts = [
        _fact("discovery-query", "The approved discovery query was executed.", query_id, source_type="workflow", source_id=context.task_id or context.workflow_id),
        _fact("discovery-provider", "The approved paper provider was executed.", provider, source_type="workflow", source_id=context.task_id or context.workflow_id),
        _fact("discovery-returned-count", "The invocation recorded its bounded returned count.", returned_count, source_type="workflow", source_id=context.task_id or context.workflow_id),
        _fact("discovery-novel-count", "The invocation recorded newly observed candidates.", novel_candidate_count, source_type="workflow", source_id=context.task_id or context.workflow_id),
        _fact("discovery-duplicate-count", "The invocation recorded duplicate candidates.", duplicate_count, source_type="workflow", source_id=context.task_id or context.workflow_id),
        _fact("discovery-remaining-operations", "The approved discovery plan records remaining eligible operations.", remaining_approved_operations, source_type="workflow", source_id=context.task_id or context.workflow_id),
        _fact("discovery-consecutive-no-novelty", "The durable invocation sequence records consecutive operations without new candidates.", consecutive_no_novelty, source_type="workflow", source_id=context.task_id or context.workflow_id),
    ]
    if candidate_set_sha256 is not None:
        facts.append(_fact("discovery-candidate-set", "The invocation recorded the canonical candidate-set hash.", candidate_set_sha256, source_type="workflow", source_id=context.task_id or context.workflow_id))
    if outcome_unknown:
        return _observation(context, status="blocked", facts=facts, warnings=[_warning(error_code or "outcome-unknown", "The paper-search outcome is unknown and will not be replayed.", severity="error", source_id=invocation_id)], artifact_ids=[], failure_category="unknown", recommended_actions=["stop"])
    if error_code is not None:
        actions: list[AgentAction] = ["stop"]
        if retry_safe:
            actions.insert(0, "retry-step")
        return _observation(context, status="failed", facts=facts, warnings=[_warning(error_code, "The approved paper-search operation failed.", severity="error", source_id=invocation_id)], artifact_ids=[], failure_category="runtime" if retry_safe else "unsupported", recommended_actions=actions)
    if stop_reached:
        return _observation(context, status="needs-review", facts=facts, warnings=[_warning("discovery-scope-complete", "Discovery reached its approved stop policy; import or select PDF sources before continuing.", severity="info", source_id=invocation_id)], artifact_ids=[], failure_category="none", recommended_actions=["stop"])
    return _observation(context, status="succeeded", facts=facts, warnings=[], artifact_ids=[], failure_category="none", recommended_actions=["continue"])


def build_two_group_preflight_observation(
    context: ObservationContext,
    *,
    preflight_id: str,
    preflight: ExactTwoGroupPreflight,
    resolved_method: Literal["welch-t-test", "mann-whitney-u"],
) -> StepObservation:
    if not preflight_id.strip():
        raise ValueError("preflight source ID must be non-empty")
    sample_too_small = any(count < 3 for count in preflight.valid_counts.values())
    zero_variance = resolved_method == "welch-t-test" and any(
        not value for value in preflight.non_constant_groups.values()
    )
    facts = [
        _fact(
            "preflight-group-counts",
            "Exact preflight records valid observations for each selected group.",
            dict(preflight.valid_counts),
            source_type="preflight",
            source_id=preflight_id,
        ),
        _fact(
            "preflight-group-variance",
            "Exact preflight records whether each selected group is non-constant.",
            dict(preflight.non_constant_groups),
            source_type="preflight",
            source_id=preflight_id,
        ),
    ]
    if sample_too_small:
        return _observation(
            context,
            status="blocked",
            facts=facts,
            warnings=[
                _warning(
                    "two-group-sample-too-small",
                    "Each selected group requires at least three valid observations.",
                    severity="error",
                    source_id=preflight_id,
                )
            ],
            artifact_ids=[],
            failure_category="input",
            recommended_actions=["stop"],
        )
    if zero_variance:
        return _observation(
            context,
            status="blocked",
            facts=facts,
            warnings=[
                _warning(
                    "welch-group-variance-zero",
                    "Welch's t-test requires non-zero variance in both selected groups.",
                    severity="error",
                    source_id=preflight_id,
                )
            ],
            artifact_ids=[],
            failure_category="method",
            recommended_actions=["revise-analysis-spec", "stop"],
        )
    return _observation(
        context,
        status="succeeded",
        facts=facts,
        warnings=[],
        artifact_ids=[],
        failure_category="none",
        recommended_actions=["continue"],
    )


def build_correlation_preflight_observation(
    context: ObservationContext,
    *,
    preflight_id: str,
    preflight: ExactCorrelationPreflight,
) -> StepObservation:
    if not preflight_id.strip():
        raise ValueError("preflight source ID must be non-empty")
    facts = [
        _fact(
            "preflight-complete-pairs",
            "Exact preflight records the number of complete numeric pairs.",
            preflight.valid_pair_count,
            source_type="preflight",
            source_id=preflight_id,
        )
    ]
    if preflight.valid_pair_count < 3:
        return _observation(
            context,
            status="blocked",
            facts=facts,
            warnings=[
                _warning(
                    "correlation-sample-too-small",
                    "Correlation requires at least three complete numeric pairs.",
                    severity="error",
                    source_id=preflight_id,
                )
            ],
            artifact_ids=[],
            failure_category="input",
            recommended_actions=["stop"],
        )
    return _observation(
        context,
        status="succeeded",
        facts=facts,
        warnings=[],
        artifact_ids=[],
        failure_category="none",
        recommended_actions=["continue"],
    )


def build_reviewer_observation(
    context: ObservationContext,
    *,
    review_id: str,
    review: AnalysisSpecReview,
    artifact_ids: list[str],
) -> StepObservation:
    if not review_id.strip():
        raise ValueError("review source ID must be non-empty")
    facts = [
        _fact(
            "reviewer-verdict",
            "The deterministic Reviewer recorded its verdict.",
            review.verdict,
            source_type="review",
            source_id=review_id,
        ),
        _fact(
            "reviewer-checks",
            "The deterministic Reviewer recorded bounded check outcomes.",
            [
                {"code": check.code, "status": check.status}
                for check in review.checks
            ],
            source_type="review",
            source_id=review_id,
        ),
    ]
    if review.required_revisions:
        facts.append(
            _fact(
                "required-revisions",
                "The deterministic Reviewer recorded required revisions.",
                review.required_revisions,
                source_type="review",
                source_id=review_id,
            )
        )
    warnings = [
        _warning(
            check.code,
            check.message,
            severity="warning" if check.status == "warning" else "error",
            source_id=check.artifact_id or review_id,
        )
        for check in review.checks
        if check.status != "passed"
    ]
    if review.verdict == "passed":
        status: Literal["succeeded", "failed", "blocked", "needs-review"] = "succeeded"
        failure_category: Literal[
            "none", "input", "method", "runtime", "artifact", "review", "unsupported", "unknown"
        ] = "none"
        actions: list[AgentAction] = ["complete"]
    elif review.verdict == "passed-with-warnings":
        status = "needs-review"
        failure_category = "none"
        actions = ["complete", "request-clarification"]
    elif review.verdict == "revision-required":
        status = "blocked"
        failure_category = "review"
        actions = ["revise-analysis-spec", "request-clarification", "stop"]
    elif review.verdict == "blocked":
        status = "blocked"
        failure_category = "review"
        actions = ["request-clarification", "stop"]
    else:
        status = "failed"
        failure_category = "review"
        actions = ["retry-step", "stop"]
    return _observation(
        context,
        status=status,
        facts=facts,
        warnings=warnings,
        artifact_ids=artifact_ids,
        failure_category=failure_category,
        recommended_actions=actions,
        review_id=review_id,
    )


__all__ = (
    "ObservationContext",
    "VerifiedFailureSummary",
    "build_analysis_result_observation",
    "build_correlation_preflight_observation",
    "build_discovery_observation",
    "build_failure_observation",
    "build_reviewer_observation",
    "build_two_group_preflight_observation",
)
