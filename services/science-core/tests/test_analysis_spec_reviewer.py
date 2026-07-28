from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from open_science_core.analysis_spec.results import StructuredAnalysisResult
from open_science_core.analysis_spec.reviewer import (
    AnalysisReviewIdentity,
    FigureLineage,
    review_analysis_spec_outputs,
)
from open_science_core.analysis_spec.schemas import (
    AnalysisSpec,
    analysis_spec_sha256,
    canonical_model_json_bytes,
)

DATASET_HASH = "a" * 64
PROFILE_HASH = "b" * 64
APPROVAL_HASH = "c" * 64
APPROVED_CODE = "print('approved')"
CODE_HASH = hashlib.sha256(APPROVED_CODE.encode("utf-8")).hexdigest()


def _correlation_spec(*, confidence_interval: bool = True) -> AnalysisSpec:
    return AnalysisSpec.model_validate(
        {
            "schemaVersion": "1",
            "objective": "Assess the association between x and y.",
            "datasetSourceId": "dataset-1",
            "datasetContentHash": DATASET_HASH,
            "datasetProfileHash": PROFILE_HASH,
            "operation": {
                "type": "correlation",
                "xColumn": "x",
                "yColumn": "y",
                "method": "pearson",
                "confidenceInterval": confidence_interval,
                "plot": "scatter",
            },
            "missingValuePolicy": "drop-per-operation",
            "confidenceLevel": 0.95,
            "randomSeed": 42,
            "assumptions": ["Rows are independent."],
            "limitations": [],
        }
    )


def _correlation_result(
    *,
    confidence_interval: tuple[float, float] | None = (0.05, 0.65),
    p_value: float = 0.03,
    x_column: str = "x",
) -> StructuredAnalysisResult:
    return StructuredAnalysisResult.model_validate(
        {
            "schemaVersion": "1",
            "objective": "Assess the association between x and y.",
            "operationType": "correlation",
            "datasetSourceId": "dataset-1",
            "datasetContentHash": DATASET_HASH,
            "datasetProfileHash": PROFILE_HASH,
            "requestedMethod": "pearson",
            "resolvedMethod": "pearson",
            "methodSelectionReason": "Pearson was explicitly requested.",
            "sampleSummary": {
                "totalRows": 12,
                "analyzedRows": 10,
                "missingRows": 2,
            },
            "result": {
                "type": "correlation",
                "xColumn": x_column,
                "yColumn": "y",
                "sampleSize": 10,
                "missingPairs": 2,
                "correlation": 0.4,
                "pValue": p_value,
                "confidenceInterval": confidence_interval,
            },
            "warnings": ["Correlation does not establish causation."],
            "limitations": [],
        }
    )


def _two_group_spec() -> AnalysisSpec:
    return AnalysisSpec.model_validate(
        {
            "schemaVersion": "1",
            "objective": "Compare treatment and control scores.",
            "datasetSourceId": "dataset-1",
            "datasetContentHash": DATASET_HASH,
            "datasetProfileHash": PROFILE_HASH,
            "operation": {
                "type": "two-group-comparison",
                "outcomeColumn": "score",
                "groupColumn": "group",
                "groups": ("treatment", "control"),
                "method": "welch-t-test",
                "effectSize": "hedges-g",
                "checkAssumptions": True,
                "plot": "boxplot",
            },
            "missingValuePolicy": "drop-per-operation",
            "confidenceLevel": 0.95,
            "randomSeed": 42,
            "assumptions": ["Rows are independent."],
            "limitations": [],
        }
    )


def _two_group_result_payload() -> dict[str, Any]:
    return {
        "schemaVersion": "1",
        "objective": "Compare treatment and control scores.",
        "operationType": "two-group-comparison",
        "datasetSourceId": "dataset-1",
        "datasetContentHash": DATASET_HASH,
        "datasetProfileHash": PROFILE_HASH,
        "requestedMethod": "welch-t-test",
        "resolvedMethod": "welch-t-test",
        "methodSelectionReason": "Welch was explicitly requested.",
        "sampleSummary": {"totalRows": 12, "analyzedRows": 10, "missingRows": 2},
        "result": {
            "type": "two-group-comparison",
            "groupColumn": "group",
            "outcomeColumn": "score",
            "groups": ("treatment", "control"),
            "sampleSizes": {"treatment": 5, "control": 5},
            "missingCounts": {"treatment": 1, "control": 1},
            "descriptiveStatistics": {
                "treatment": {"mean": 5.0, "std": 1.0, "median": 5.0},
                "control": {"mean": 4.0, "std": 1.0, "median": 4.0},
            },
            "testStatistic": 1.5,
            "pValue": 0.17,
            "effectSizeName": "hedges-g",
            "effectSize": 0.8,
            "confidenceInterval": (-0.3, 1.9),
        },
        "warnings": [],
        "limitations": [],
    }


def _identity(spec: AnalysisSpec, **updates: str) -> AnalysisReviewIdentity:
    values = {
        "datasetContentHash": DATASET_HASH,
        "datasetProfileSha256": PROFILE_HASH,
        "analysisSpecSha256": analysis_spec_sha256(spec),
        "compilerVersion": "analysis-spec-compiler-v1",
        "codeSha256": CODE_HASH,
        "approvalHash": APPROVAL_HASH,
        "runtimePolicyId": "dataset-analysis-spec-v1",
        **updates,
    }
    return AnalysisReviewIdentity.model_validate(values)


def _notebook(code: str = APPROVED_CODE) -> bytes:
    return json.dumps(
        {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [
                {"cell_type": "markdown", "metadata": {}, "source": ["# Analysis"]},
                {
                    "cell_type": "code",
                    "metadata": {"tags": ["analysis"]},
                    "source": [code],
                    "outputs": [],
                    "execution_count": 1,
                },
            ],
        }
    ).encode()


def _correlation_summary(*, correlation: float = 0.4, p_value: float = 0.03) -> bytes:
    return (
        "xColumn,yColumn,sampleSize,missingPairs,method,correlation,pValue,"
        "confidenceIntervalLower,confidenceIntervalUpper\n"
        f"x,y,10,2,pearson,{correlation},{p_value},0.05,0.65\n"
    ).encode()


def _review(
    *,
    spec: AnalysisSpec | None = None,
    result: StructuredAnalysisResult | None = None,
    results_json: bytes | None = None,
    summary: bytes | None = None,
    notebook: bytes | None = None,
    observed_identity: AnalysisReviewIdentity | None = None,
    figure: FigureLineage | None | object = ...,  # sentinel preserves a valid figure
):
    spec = spec or _correlation_spec()
    result = result or _correlation_result()
    identity = _identity(spec)
    if figure is ...:
        figure = FigureLineage(
            artifact_id="figure-1",
            analysis_spec_sha256=identity.analysis_spec_sha256,
            code_sha256=CODE_HASH,
            columns=["x", "y"],
        )
    assert figure is None or isinstance(figure, FigureLineage)
    return review_analysis_spec_outputs(
        analysis_spec_json=canonical_model_json_bytes(spec),
        results_json=results_json or canonical_model_json_bytes(result),
        summary_csv=summary or _correlation_summary(),
        executed_notebook_json=notebook or _notebook(),
        approved_code=APPROVED_CODE,
        approved_identity=identity,
        observed_identity=observed_identity or identity,
        expected_result_sha256=hashlib.sha256(canonical_model_json_bytes(result)).hexdigest(),
        figure_lineage=figure,
    )


def test_reviewer_passes_valid_outputs_with_explicit_cautious_language() -> None:
    review = _review()

    assert review.verdict == "passed-with-warnings"
    assert not review.required_revisions
    assert "used 10 of 12" in review.conclusion
    assert "reported 2" in review.conclusion
    assert "does not establish causation" in review.conclusion
    assert "practical importance" in review.conclusion
    assert {check.code for check in review.checks} >= {
        "dataset-hash-matches",
        "profile-hash-matches",
        "analysis-spec-hash-matches",
        "compiler-version-matches",
        "code-hash-matches",
        "approval-hash-matches",
        "runtime-policy-matches",
        "summary-matches-results",
        "figure-lineage-matches",
        "notebook-code-matches",
    }


def test_identity_mismatch_blocks_publication() -> None:
    spec = _correlation_spec()
    review = _review(
        spec=spec,
        observed_identity=_identity(spec, approvalHash="d" * 64),
    )

    assert review.verdict == "blocked"
    assert "No scientific conclusion" in review.conclusion
    assert next(
        check for check in review.checks if check.code == "approval-hash-matches"
    ).status == "failed"


@pytest.mark.parametrize("missing_field", ["pValue", "effectSize"])
def test_missing_required_numeric_result_fails_schema(missing_field: str) -> None:
    spec = _two_group_spec()
    payload = _two_group_result_payload()
    payload["result"].pop(missing_field)
    valid_result = StructuredAnalysisResult.model_validate(_two_group_result_payload())

    review = _review(
        spec=spec,
        result=valid_result,
        results_json=json.dumps(payload).encode(),
        summary=(
            b"group,sampleSize,missingCount,mean,std,median\n"
            b"treatment,5,1,5.0,1.0,5.0\ncontrol,5,1,4.0,1.0,4.0\n"
        ),
        figure=FigureLineage(
            artifact_id="figure-1",
            analysis_spec_sha256=analysis_spec_sha256(spec),
            code_sha256=CODE_HASH,
            columns=["group", "score"],
        ),
    )

    assert review.verdict == "failed"
    assert review.checks[-1].code == "structured-result-schema-valid"
    assert review.checks[-1].status == "failed"


def test_requested_confidence_interval_is_required() -> None:
    result = _correlation_result(confidence_interval=None)
    review = _review(result=result)

    assert review.verdict == "revision-required"
    assert next(
        check for check in review.checks if check.code == "confidence-interval-present"
    ).status == "failed"


def test_wrong_columns_require_revision() -> None:
    result = _correlation_result(x_column="other")
    review = _review(result=result)

    assert review.verdict == "revision-required"
    assert next(
        check for check in review.checks if check.code == "analysis-columns-match"
    ).status == "failed"


def test_tampered_results_hash_blocks_publication() -> None:
    result = _correlation_result(p_value=0.04)
    review = _review(result=result, summary=_correlation_summary(p_value=0.04))

    # _review binds the hash to the same result, so change only the JSON after binding.
    valid = _correlation_result()
    spec = _correlation_spec()
    identity = _identity(spec)
    review = review_analysis_spec_outputs(
        analysis_spec_json=canonical_model_json_bytes(spec),
        results_json=canonical_model_json_bytes(result),
        summary_csv=_correlation_summary(p_value=0.04),
        executed_notebook_json=_notebook(),
        approved_code=APPROVED_CODE,
        approved_identity=identity,
        observed_identity=identity,
        expected_result_sha256=hashlib.sha256(canonical_model_json_bytes(valid)).hexdigest(),
        figure_lineage=FigureLineage(
            artifact_id="figure-1",
            analysis_spec_sha256=identity.analysis_spec_sha256,
            code_sha256=CODE_HASH,
            columns=["x", "y"],
        ),
    )

    assert review.verdict == "blocked"
    assert next(
        check for check in review.checks if check.code == "structured-result-hash-matches"
    ).status == "failed"


@pytest.mark.parametrize(
    ("summary", "notebook", "figure"),
    [
        (_correlation_summary(correlation=0.9), _notebook(), ...),
        (_correlation_summary(), _notebook("print('tampered')"), ...),
        (
            _correlation_summary(),
            _notebook(),
            FigureLineage(
                artifact_id="figure-1",
                analysis_spec_sha256=analysis_spec_sha256(_correlation_spec()),
                code_sha256=CODE_HASH,
                columns=["x", "other"],
            ),
        ),
    ],
)
def test_inconsistent_derived_artifacts_require_revision(
    summary: bytes,
    notebook: bytes,
    figure: FigureLineage | object,
) -> None:
    review = _review(summary=summary, notebook=notebook, figure=figure)

    assert review.verdict == "revision-required"


def test_non_significant_conclusion_does_not_claim_no_association() -> None:
    result = _correlation_result(p_value=0.4)
    review = _review(result=result, summary=_correlation_summary(p_value=0.4))

    assert review.verdict == "passed-with-warnings"
    assert "not proof of no association" in review.conclusion
    assert "no association exists" not in review.conclusion


def test_descriptive_summary_row_order_does_not_change_consistency() -> None:
    spec = AnalysisSpec.model_validate(
        {
            "schemaVersion": "1",
            "objective": "Summarize score.",
            "datasetSourceId": "dataset-1",
            "datasetContentHash": DATASET_HASH,
            "datasetProfileHash": PROFILE_HASH,
            "operation": {
                "type": "descriptive",
                "columns": ["score"],
                "statistics": ["mean", "count"],
                "plot": "none",
            },
            "missingValuePolicy": "drop-per-operation",
            "confidenceLevel": 0.95,
            "randomSeed": 42,
            "assumptions": [],
            "limitations": [],
        }
    )
    result = StructuredAnalysisResult.model_validate(
        {
            "schemaVersion": "1",
            "objective": "Summarize score.",
            "operationType": "descriptive",
            "datasetSourceId": "dataset-1",
            "datasetContentHash": DATASET_HASH,
            "datasetProfileHash": PROFILE_HASH,
            "requestedMethod": "descriptive",
            "resolvedMethod": "descriptive",
            "methodSelectionReason": "Descriptive statistics were requested.",
            "sampleSummary": {"totalRows": 3, "analyzedRows": 3, "missingRows": 0},
            "result": {
                "type": "descriptive",
                "columns": [
                    {
                        "column": "score",
                        "sampleSize": 3,
                        "missingCount": 0,
                        "statistics": {"mean": 2.0, "count": 3},
                    }
                ],
            },
            "warnings": [],
            "limitations": [],
        }
    )
    identity = _identity(spec)

    review = review_analysis_spec_outputs(
        analysis_spec_json=canonical_model_json_bytes(spec),
        results_json=canonical_model_json_bytes(result),
        summary_csv=(
            b"column,statistic,value\nscore,mean,2.0\nscore,count,3\n"
        ),
        executed_notebook_json=_notebook(),
        approved_code=APPROVED_CODE,
        approved_identity=identity,
        observed_identity=identity,
        expected_result_sha256=hashlib.sha256(canonical_model_json_bytes(result)).hexdigest(),
        figure_lineage=None,
    )

    assert review.verdict == "passed"
    assert "score: n=3, missing=0" in review.conclusion


def test_descriptive_summary_accepts_integral_csv_float_rendering() -> None:
    spec = AnalysisSpec.model_validate(
        {
            "schemaVersion": "1",
            "objective": "Summarize score.",
            "datasetSourceId": "dataset-1",
            "datasetContentHash": DATASET_HASH,
            "datasetProfileHash": PROFILE_HASH,
            "operation": {
                "type": "descriptive",
                "columns": ["score"],
                "statistics": ["count", "missing", "mean"],
                "plot": "none",
            },
            "missingValuePolicy": "drop-per-operation",
            "confidenceLevel": 0.95,
            "randomSeed": 42,
            "assumptions": [],
            "limitations": [],
        }
    )
    result = StructuredAnalysisResult.model_validate(
        {
            "schemaVersion": "1",
            "objective": "Summarize score.",
            "operationType": "descriptive",
            "datasetSourceId": "dataset-1",
            "datasetContentHash": DATASET_HASH,
            "datasetProfileHash": PROFILE_HASH,
            "requestedMethod": "descriptive",
            "resolvedMethod": "descriptive",
            "methodSelectionReason": "Descriptive statistics were requested.",
            "sampleSummary": {"totalRows": 3, "analyzedRows": 3, "missingRows": 0},
            "result": {
                "type": "descriptive",
                "columns": [
                    {
                        "column": "score",
                        "sampleSize": 3,
                        "missingCount": 0,
                        "statistics": {"count": 3, "missing": 0, "mean": 2.0},
                    }
                ],
            },
            "warnings": [],
            "limitations": [],
        }
    )
    identity = _identity(spec)

    review = review_analysis_spec_outputs(
        analysis_spec_json=canonical_model_json_bytes(spec),
        results_json=canonical_model_json_bytes(result),
        summary_csv=(
            b"column,statistic,value\nscore,count,3.0\nscore,missing,0.0\nscore,mean,2.0\n"
        ),
        executed_notebook_json=_notebook(),
        approved_code=APPROVED_CODE,
        approved_identity=identity,
        observed_identity=identity,
        expected_result_sha256=hashlib.sha256(canonical_model_json_bytes(result)).hexdigest(),
        figure_lineage=None,
    )

    assert review.verdict == "passed"
    assert next(
        check for check in review.checks if check.code == "summary-matches-results"
    ).status == "passed"
