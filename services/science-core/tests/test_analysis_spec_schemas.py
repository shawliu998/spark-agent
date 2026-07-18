from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

import pytest
from pydantic import ValidationError

from open_science_core.analysis_spec import (
    AnalysisSampleSummary,
    AnalysisSpec,
    ClarificationProposal,
    CompiledAnalysis,
    CorrelationAnalysisResult,
    CorrelationOperation,
    DescriptiveAnalysisResult,
    DescriptiveColumnResult,
    DescriptiveOperation,
    ScientificClarification,
    ScientificClarificationOption,
    StructuredAnalysisResult,
    TwoGroupComparisonOperation,
    TwoGroupComparisonResult,
    UnsupportedAnalysis,
    analysis_spec_sha256,
    canonical_model_json_bytes,
    structured_analysis_result_sha256,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def analysis_spec(operation: object, **overrides: object) -> AnalysisSpec:
    payload: dict[str, object] = {
        "schema_version": "1",
        "objective": "Answer the bounded analysis objective.",
        "dataset_source_id": "dataset-1",
        "dataset_content_hash": HASH_A,
        "dataset_profile_hash": HASH_B,
        "operation": operation,
        "missing_value_policy": "drop-per-operation",
        "confidence_level": 0.95,
        "random_seed": 17,
        "assumptions": ["Rows are independent observations."],
        "limitations": ["The analysis is observational."],
    }
    payload.update(overrides)
    return AnalysisSpec.model_validate(payload)


def descriptive_operation(**overrides: object) -> DescriptiveOperation:
    payload: dict[str, object] = {
        "type": "descriptive",
        "columns": ["score", "group"],
        "statistics": ["count", "missing", "mean", "frequency"],
        "plot": "none",
    }
    payload.update(overrides)
    return DescriptiveOperation.model_validate(payload)


def two_group_operation(**overrides: object) -> TwoGroupComparisonOperation:
    payload: dict[str, object] = {
        "type": "two-group-comparison",
        "outcome_column": "score",
        "group_column": "group",
        "groups": ("treatment", "control"),
        "method": "welch-t-test",
        "effect_size": "hedges-g",
        "check_assumptions": True,
        "plot": "boxplot",
    }
    payload.update(overrides)
    return TwoGroupComparisonOperation.model_validate(payload)


def correlation_operation(**overrides: object) -> CorrelationOperation:
    payload: dict[str, object] = {
        "type": "correlation",
        "x_column": "sleep_hours",
        "y_column": "cognitive_score",
        "method": "pearson",
        "confidence_interval": True,
        "plot": "scatter",
    }
    payload.update(overrides)
    return CorrelationOperation.model_validate(payload)


def sample_summary() -> AnalysisSampleSummary:
    return AnalysisSampleSummary(total_rows=90, analyzed_rows=86, missing_rows=4)


def structured_result(
    result: object,
    *,
    operation_type: str,
    requested_method: str,
    resolved_method: str,
) -> StructuredAnalysisResult:
    return StructuredAnalysisResult.model_validate(
        {
            "schema_version": "1",
            "objective": "Answer the bounded analysis objective.",
            "operation_type": operation_type,
            "dataset_source_id": "dataset-1",
            "dataset_content_hash": HASH_A,
            "dataset_profile_hash": HASH_B,
            "requested_method": requested_method,
            "resolved_method": resolved_method,
            "method_selection_reason": "The deterministic rule selected this method.",
            "sample_summary": sample_summary(),
            "result": result,
            "warnings": [],
            "limitations": ["Association does not establish causation."],
        }
    )


def test_valid_descriptive_spec_is_strict_frozen_and_canonical() -> None:
    spec = analysis_spec(descriptive_operation())

    assert spec.operation.type == "descriptive"
    payload = json.loads(canonical_model_json_bytes(spec))
    assert payload["schemaVersion"] == "1"
    assert payload["datasetSourceId"] == "dataset-1"
    assert payload["missingValuePolicy"] == "drop-per-operation"
    assert analysis_spec_sha256(spec) == hashlib.sha256(
        canonical_model_json_bytes(spec)
    ).hexdigest()
    with pytest.raises(ValidationError):
        spec.random_seed = 18  # type: ignore[misc]


@pytest.mark.parametrize(
    ("method", "effect_size"),
    [
        ("welch-t-test", "hedges-g"),
        ("mann-whitney-u", "rank-biserial"),
        ("auto", "hedges-g"),
    ],
)
def test_valid_two_group_specs(method: str, effect_size: str) -> None:
    spec = analysis_spec(
        two_group_operation(method=method, effect_size=effect_size)
    )

    assert spec.operation.type == "two-group-comparison"
    assert spec.operation.method == method


@pytest.mark.parametrize("method", ["pearson", "spearman", "auto"])
def test_valid_correlation_specs(method: str) -> None:
    spec = analysis_spec(correlation_operation(method=method))

    assert spec.operation.type == "correlation"
    assert spec.operation.method == method


@pytest.mark.parametrize(
    ("factory", "overrides"),
    [
        (descriptive_operation, {"columns": []}),
        (descriptive_operation, {"columns": ["score", "score"]}),
        (descriptive_operation, {"statistics": ["mean", "mean"]}),
        (two_group_operation, {"groups": ("control", "control")}),
        (two_group_operation, {"outcome_column": "group"}),
        (
            two_group_operation,
            {"method": "welch-t-test", "effect_size": "rank-biserial"},
        ),
        (
            two_group_operation,
            {"method": "mann-whitney-u", "effect_size": "hedges-g"},
        ),
        (correlation_operation, {"y_column": "sleep_hours"}),
    ],
)
def test_operation_schema_rejects_ambiguous_or_incompatible_values(
    factory: Any,
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        factory(**overrides)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence_level", 0.79),
        ("confidence_level", 1.0),
        ("confidence_level", 95),
        ("random_seed", -1),
        ("random_seed", 2**32),
        ("random_seed", True),
    ],
)
def test_analysis_spec_rejects_invalid_confidence_and_seed(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        analysis_spec(descriptive_operation(), **{field: value})


def test_analysis_spec_rejects_extra_fields_at_every_level() -> None:
    with pytest.raises(ValidationError):
        analysis_spec(descriptive_operation(), invented=True)

    with pytest.raises(ValidationError):
        descriptive_operation(shell="echo unsafe")


def test_analysis_spec_rejects_unsupported_operation() -> None:
    with pytest.raises(ValidationError):
        analysis_spec(
            {
                "type": "regression",
                "outcomeColumn": "score",
                "predictors": ["age"],
            }
        )


def test_scientific_clarification_and_unsupported_contracts_are_bounded() -> None:
    clarification = ClarificationProposal(
        reason="The outcome column is ambiguous.",
        requests=[
            ScientificClarification(
                type="outcome-column",
                question="Which outcome should be compared?",
                options=[
                    ScientificClarificationOption(value="score", label="Score"),
                    ScientificClarificationOption(value="accuracy", label="Accuracy"),
                ],
            )
        ],
    )
    unsupported = UnsupportedAnalysis(
        capability="structural-equation-modeling",
        explanation="The current version does not support SEM.",
        supported_alternatives=["descriptive", "correlation"],
    )

    assert clarification.requests[0].type == "outcome-column"
    assert unsupported.capability == "structural-equation-modeling"
    with pytest.raises(ValidationError):
        UnsupportedAnalysis(
            capability="SEM",
            explanation="Unsupported.",
            supported_alternatives=["correlation", "correlation"],
        )


def test_compiled_analysis_binds_the_exact_code_hash() -> None:
    code = "print('controlled')\n"
    compiled = CompiledAnalysis(
        compiler_version="analysis-compiler-v1",
        spec_sha256=HASH_A,
        code=code,
        code_sha256=hashlib.sha256(code.encode()).hexdigest(),
        expected_outputs=["analysis-spec.json", "results.json"],
        runtime_policy_id="dataset-analysis-compiled-v1",
    )

    assert compiled.code == code
    with pytest.raises(ValidationError):
        compiled.model_copy(update={"code_sha256": HASH_B}, deep=True).model_validate(
            compiled.model_copy(update={"code_sha256": HASH_B}).model_dump()
        )


def test_structured_descriptive_result_is_valid_and_hash_stable() -> None:
    result = structured_result(
        DescriptiveAnalysisResult(
            type="descriptive",
            columns=[
                DescriptiveColumnResult(
                    column="score",
                    sample_size=86,
                    missing_count=4,
                    statistics={"count": 86, "mean": 4.2, "missing": 4},
                )
            ],
        ),
        operation_type="descriptive",
        requested_method="descriptive",
        resolved_method="descriptive",
    )

    assert structured_analysis_result_sha256(result) == canonical_hash(result)


@pytest.mark.parametrize(
    ("requested_method", "resolved_method", "effect_size_name"),
    [
        ("auto", "welch-t-test", "hedges-g"),
        ("mann-whitney-u", "mann-whitney-u", "rank-biserial"),
    ],
)
def test_structured_two_group_results_are_method_compatible(
    requested_method: str,
    resolved_method: str,
    effect_size_name: Literal["hedges-g", "rank-biserial"],
) -> None:
    result = structured_result(
        TwoGroupComparisonResult(
            type="two-group-comparison",
            group_column="group",
            outcome_column="score",
            groups=("treatment", "control"),
            sample_sizes={"treatment": 42, "control": 44},
            missing_counts={"treatment": 2, "control": 2},
            descriptive_statistics={
                "treatment": {"mean": 7.1, "std": 1.2},
                "control": {"mean": 6.3, "std": 1.1},
            },
            test_statistic=2.41,
            p_value=0.018,
            effect_size_name=effect_size_name,
            effect_size=0.52,
            confidence_interval=(0.08, 0.96),
        ),
        operation_type="two-group-comparison",
        requested_method=requested_method,
        resolved_method=resolved_method,
    )

    assert result.result.type == "two-group-comparison"


@pytest.mark.parametrize(
    ("requested_method", "resolved_method"),
    [("auto", "pearson"), ("spearman", "spearman")],
)
def test_structured_correlation_results_are_method_compatible(
    requested_method: str,
    resolved_method: str,
) -> None:
    result = structured_result(
        CorrelationAnalysisResult(
            type="correlation",
            x_column="sleep_hours",
            y_column="cognitive_score",
            sample_size=86,
            missing_pairs=4,
            correlation=0.43,
            p_value=0.00004,
            confidence_interval=(0.24, 0.58),
        ),
        operation_type="correlation",
        requested_method=requested_method,
        resolved_method=resolved_method,
    )

    assert result.result.type == "correlation"


def test_structured_result_rejects_method_result_mismatch_and_invalid_numbers() -> None:
    correlation = CorrelationAnalysisResult(
        type="correlation",
        x_column="x",
        y_column="y",
        sample_size=10,
        missing_pairs=0,
        correlation=0.2,
        p_value=0.5,
        confidence_interval=None,
    )
    with pytest.raises(ValidationError):
        structured_result(
            correlation,
            operation_type="two-group-comparison",
            requested_method="auto",
            resolved_method="welch-t-test",
        )
    with pytest.raises(ValidationError):
        CorrelationAnalysisResult(
            type="correlation",
            x_column="x",
            y_column="y",
            sample_size=10,
            missing_pairs=0,
            correlation=1.1,
            p_value=0.5,
            confidence_interval=None,
        )


def test_structured_result_rejects_contradictory_sample_counts() -> None:
    result = structured_result(
        CorrelationAnalysisResult(
            type="correlation",
            x_column="x",
            y_column="y",
            sample_size=86,
            missing_pairs=4,
            correlation=0.2,
            p_value=0.5,
            confidence_interval=None,
        ),
        operation_type="correlation",
        requested_method="pearson",
        resolved_method="pearson",
    )
    payload = result.model_dump(mode="json", by_alias=True)
    payload["sampleSummary"] = {
        "totalRows": 100,
        "analyzedRows": 90,
        "missingRows": 10,
    }

    with pytest.raises(ValidationError, match="pair counts"):
        StructuredAnalysisResult.model_validate(payload)


def canonical_hash(value: StructuredAnalysisResult) -> str:
    return hashlib.sha256(canonical_model_json_bytes(value)).hexdigest()
