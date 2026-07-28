from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from typing import Annotated, Any, Literal, cast

from pydantic import Field, StringConstraints

from .results import (
    CorrelationAnalysisResult,
    DescriptiveAnalysisResult,
    StructuredAnalysisResult,
    TwoGroupComparisonResult,
    structured_analysis_result_sha256,
)
from .schemas import (
    AnalysisSpec,
    ColumnName,
    NonEmptyText,
    Sha256,
    StrictAnalysisModel,
    analysis_spec_sha256,
)

ReviewerVerdict = Literal[
    "passed",
    "passed-with-warnings",
    "revision-required",
    "blocked",
    "failed",
]
ReviewStatus = Literal["passed", "warning", "failed"]
ReviewCategory = Literal["identity", "method", "results", "language"]

_MAX_JSON_BYTES = 2 * 1024 * 1024
_MAX_SUMMARY_BYTES = 2 * 1024 * 1024
_FLOAT_ABS_TOLERANCE = 1e-12
_FLOAT_REL_TOLERANCE = 1e-12


class AnalysisReviewIdentity(StrictAnalysisModel):
    dataset_content_hash: Sha256
    dataset_profile_sha256: Sha256
    analysis_spec_sha256: Sha256
    compiler_version: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    ]
    code_sha256: Sha256
    approval_hash: Sha256
    runtime_policy_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    ]


class FigureLineage(StrictAnalysisModel):
    artifact_id: NonEmptyText
    analysis_spec_sha256: Sha256
    code_sha256: Sha256
    columns: Annotated[list[ColumnName], Field(min_length=1, max_length=100)]


class AnalysisReviewCheck(StrictAnalysisModel):
    code: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=100,
            pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        ),
    ]
    category: ReviewCategory
    status: ReviewStatus
    message: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000),
    ]
    artifact_id: NonEmptyText | None = None


class AnalysisSpecReview(StrictAnalysisModel):
    schema_version: Literal["1"]
    verdict: ReviewerVerdict
    checks: Annotated[list[AnalysisReviewCheck], Field(min_length=1, max_length=100)]
    required_revisions: Annotated[list[NonEmptyText], Field(max_length=100)]
    conclusion: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=8_000),
    ]


def review_analysis_spec_outputs(
    *,
    analysis_spec_json: bytes,
    results_json: bytes,
    summary_csv: bytes,
    executed_notebook_json: bytes,
    approved_code: str,
    approved_identity: AnalysisReviewIdentity,
    observed_identity: AnalysisReviewIdentity,
    expected_result_sha256: Sha256,
    figure_lineage: FigureLineage | None,
) -> AnalysisSpecReview:
    """Review compiled analysis outputs without model inference or recomputation."""

    checks: list[AnalysisReviewCheck] = []
    required_revisions: list[str] = []
    spec = _parse_model(
        analysis_spec_json,
        AnalysisSpec,
        "analysis-spec-schema-valid",
        "AnalysisSpec is valid and canonical.",
        "AnalysisSpec is missing, oversized, or invalid.",
        checks,
    )
    result = _parse_model(
        results_json,
        StructuredAnalysisResult,
        "structured-result-schema-valid",
        "The structured result conforms to schema version 1.",
        "results.json is missing, oversized, or invalid.",
        checks,
    )
    if spec is None or result is None:
        return _terminal_review(
            checks,
            verdict="failed",
            revision="Regenerate the invalid structured analysis artifacts.",
            conclusion=(
                "No scientific conclusion is available because the structured analysis "
                "artifacts did not pass schema validation."
            ),
        )

    _review_identity(
        spec=spec,
        result=result,
        approved_code=approved_code,
        approved=approved_identity,
        observed=observed_identity,
        checks=checks,
    )
    actual_result_sha256 = structured_analysis_result_sha256(result)
    _check(
        checks,
        code="structured-result-hash-matches",
        category="identity",
        passed=actual_result_sha256 == expected_result_sha256,
        passed_message="results.json matches its immutable structured-result hash.",
        failed_message="results.json does not match its immutable structured-result hash.",
    )
    identity_failed = any(
        check.status == "failed" and check.category == "identity" for check in checks
    )
    if identity_failed:
        return _terminal_review(
            checks,
            verdict="blocked",
            revision="Restore the exact approved identity and immutable run artifacts.",
            conclusion=(
                "No scientific conclusion is available because the approved analysis "
                "identity or artifact lineage could not be verified."
            ),
        )

    _review_method(spec, result, checks)
    _review_results(spec, result, checks)
    _review_summary(summary_csv, result, checks)
    _review_figure(spec, approved_identity, figure_lineage, checks)
    _review_notebook(executed_notebook_json, approved_code, checks)
    _review_language_guards(spec, result, checks)

    failures = [check for check in checks if check.status == "failed"]
    warnings = [check for check in checks if check.status == "warning"]
    if failures:
        required_revisions.extend(_revision_for(check.code) for check in failures)
        verdict: ReviewerVerdict = "revision-required"
        conclusion = (
            "No scientific conclusion is published because deterministic review found "
            "inconsistent method or result artifacts."
        )
    else:
        verdict = "passed-with-warnings" if warnings else "passed"
        conclusion = _conclusion(spec, result)
    return AnalysisSpecReview(
        schema_version="1",
        verdict=verdict,
        checks=checks,
        required_revisions=list(dict.fromkeys(required_revisions)),
        conclusion=conclusion,
    )


def _parse_model[ModelT: AnalysisSpec | StructuredAnalysisResult](
    content: bytes,
    model_type: type[ModelT],
    code: str,
    passed_message: str,
    failed_message: str,
    checks: list[AnalysisReviewCheck],
) -> ModelT | None:
    try:
        if not content or len(content) > _MAX_JSON_BYTES:
            raise ValueError("invalid JSON artifact size")
        model = model_type.model_validate_json(content)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _check(
            checks,
            code=code,
            category="results",
            passed=False,
            passed_message=passed_message,
            failed_message=failed_message,
        )
        return None
    _check(
        checks,
        code=code,
        category="results",
        passed=True,
        passed_message=passed_message,
        failed_message=failed_message,
    )
    return model


def _review_identity(
    *,
    spec: AnalysisSpec,
    result: StructuredAnalysisResult,
    approved_code: str,
    approved: AnalysisReviewIdentity,
    observed: AnalysisReviewIdentity,
    checks: list[AnalysisReviewCheck],
) -> None:
    actual_spec_sha256 = analysis_spec_sha256(spec)
    comparisons = (
        (
            "dataset-hash-matches",
            spec.dataset_content_hash
            == result.dataset_content_hash
            == approved.dataset_content_hash
            == observed.dataset_content_hash,
            "Dataset content hash matches the approved Spec, result, and run.",
            "Dataset content hash differs across the approved Spec, result, or run.",
        ),
        (
            "profile-hash-matches",
            spec.dataset_profile_hash
            == result.dataset_profile_hash
            == approved.dataset_profile_sha256
            == observed.dataset_profile_sha256,
            "Dataset profile hash matches the approved Spec, result, and run.",
            "Dataset profile hash differs across the approved Spec, result, or run.",
        ),
        (
            "analysis-spec-hash-matches",
            actual_spec_sha256
            == approved.analysis_spec_sha256
            == observed.analysis_spec_sha256,
            "AnalysisSpec hash matches the exact approved and executed Spec.",
            "AnalysisSpec hash differs from the approved or executed identity.",
        ),
        (
            "compiler-version-matches",
            approved.compiler_version == observed.compiler_version,
            "Compiler version matches the approved execution identity.",
            "Compiler version differs from the approved execution identity.",
        ),
        (
            "code-hash-matches",
            hashlib.sha256(approved_code.encode("utf-8")).hexdigest()
            == approved.code_sha256
            == observed.code_sha256,
            "Code hash matches the exact approved and executed code.",
            "Code hash differs from the approved or executed code.",
        ),
        (
            "approval-hash-matches",
            approved.approval_hash == observed.approval_hash,
            "Approval hash matches the immutable execution approval.",
            "Approval hash differs from the immutable execution approval.",
        ),
        (
            "runtime-policy-matches",
            approved.runtime_policy_id == observed.runtime_policy_id,
            "Runtime policy matches the approved execution policy.",
            "Runtime policy differs from the approved execution policy.",
        ),
    )
    for code, passed, passed_message, failed_message in comparisons:
        _check(
            checks,
            code=code,
            category="identity",
            passed=passed,
            passed_message=passed_message,
            failed_message=failed_message,
        )


def _review_method(
    spec: AnalysisSpec,
    result: StructuredAnalysisResult,
    checks: list[AnalysisReviewCheck],
) -> None:
    operation = spec.operation
    operation_result = result.result
    _check(
        checks,
        code="goal-operation-executed",
        category="method",
        passed=result.operation_type == operation.type == operation_result.type,
        passed_message="The operation requested by the approved Goal and Spec was executed.",
        failed_message="The executed operation differs from the approved AnalysisSpec.",
    )
    requested_method = (
        "descriptive" if operation.type == "descriptive" else operation.method
    )
    _check(
        checks,
        code="method-recorded",
        category="method",
        passed=result.requested_method == requested_method,
        passed_message="Requested and resolved methods are explicitly recorded.",
        failed_message="The recorded requested method differs from the approved AnalysisSpec.",
    )
    columns_match = False
    groups_match = True
    if operation.type == "descriptive" and isinstance(
        operation_result, DescriptiveAnalysisResult
    ):
        columns_match = [item.column for item in operation_result.columns] == operation.columns
    elif operation.type == "two-group-comparison" and isinstance(
        operation_result, TwoGroupComparisonResult
    ):
        columns_match = (
            operation_result.outcome_column == operation.outcome_column
            and operation_result.group_column == operation.group_column
        )
        groups_match = operation_result.groups == operation.groups
    elif operation.type == "correlation" and isinstance(
        operation_result, CorrelationAnalysisResult
    ):
        columns_match = (
            operation_result.x_column == operation.x_column
            and operation_result.y_column == operation.y_column
        )
    _check(
        checks,
        code="analysis-columns-match",
        category="method",
        passed=columns_match,
        passed_message="The result uses exactly the columns approved in the AnalysisSpec.",
        failed_message="The result columns differ from the approved AnalysisSpec.",
    )
    if operation.type == "two-group-comparison":
        _check(
            checks,
            code="analysis-groups-match",
            category="method",
            passed=groups_match,
            passed_message="The result uses exactly the two approved groups.",
            failed_message="The result groups differ from the approved AnalysisSpec.",
        )
    _check(
        checks,
        code="missing-value-policy-recorded",
        category="method",
        passed=(
            spec.missing_value_policy in {"drop-per-operation", "report-only"}
            and result.sample_summary.missing_rows >= 0
        ),
        passed_message="The approved missing-value policy and missing-row count are recorded.",
        failed_message="The missing-value policy or missing-row count is unavailable.",
    )


def _review_results(
    spec: AnalysisSpec,
    result: StructuredAnalysisResult,
    checks: list[AnalysisReviewCheck],
) -> None:
    _check(
        checks,
        code="sample-size-present",
        category="results",
        passed=result.sample_summary.analyzed_rows > 0,
        passed_message="The analyzed sample size is present and positive.",
        failed_message="The analyzed sample size is absent or zero.",
    )
    _check(
        checks,
        code="missing-count-present",
        category="results",
        passed=result.sample_summary.missing_rows >= 0,
        passed_message="The missing-row count is explicitly reported.",
        failed_message="The missing-row count is unavailable.",
    )
    operation = spec.operation
    operation_result = result.result
    if isinstance(operation_result, DescriptiveAnalysisResult):
        _check(
            checks,
            code="sample-counts-consistent",
            category="results",
            passed=all(
                item.sample_size + item.missing_count
                == result.sample_summary.total_rows
                for item in operation_result.columns
            ),
            passed_message="Column sample and missing counts equal the dataset row count.",
            failed_message="Column sample or missing counts contradict the dataset row count.",
        )
    elif isinstance(operation_result, TwoGroupComparisonResult):
        _check(
            checks,
            code="sample-counts-consistent",
            category="results",
            passed=(
                sum(operation_result.sample_sizes.values())
                == result.sample_summary.analyzed_rows
                and sum(operation_result.missing_counts.values())
                <= result.sample_summary.missing_rows
            ),
            passed_message="Group sample and missing counts agree with the result envelope.",
            failed_message="Group sample or missing counts contradict the result envelope.",
        )
        _check_probability(operation_result.p_value, checks)
        _check(
            checks,
            code="effect-size-present",
            category="results",
            passed=math.isfinite(operation_result.effect_size),
            passed_message="The method-compatible effect size is present.",
            failed_message="The required effect size is missing or non-finite.",
        )
        _check(
            checks,
            code="confidence-interval-present",
            category="results",
            passed=len(operation_result.confidence_interval) == 2,
            passed_message="The required effect-size confidence interval is present.",
            failed_message="The required effect-size confidence interval is missing.",
        )
    else:
        _check(
            checks,
            code="sample-counts-consistent",
            category="results",
            passed=(
                operation_result.sample_size
                == result.sample_summary.analyzed_rows
                and operation_result.missing_pairs
                == result.sample_summary.missing_rows
                and operation_result.sample_size + operation_result.missing_pairs
                == result.sample_summary.total_rows
            ),
            passed_message="Correlation pair counts agree with the result envelope.",
            failed_message="Correlation pair counts contradict the result envelope.",
        )
        _check_probability(operation_result.p_value, checks)
        requires_interval = operation.type == "correlation" and operation.confidence_interval
        _check(
            checks,
            code="confidence-interval-present",
            category="results",
            passed=not requires_interval or operation_result.confidence_interval is not None,
            passed_message="The requested correlation confidence interval is present.",
            failed_message="The requested correlation confidence interval is missing.",
        )


def _check_probability(value: float, checks: list[AnalysisReviewCheck]) -> None:
    _check(
        checks,
        code="p-value-valid",
        category="results",
        passed=math.isfinite(value) and 0.0 <= value <= 1.0,
        passed_message="The p-value is finite and within [0, 1].",
        failed_message="The p-value is missing, non-finite, or outside [0, 1].",
    )


def _review_summary(
    content: bytes,
    result: StructuredAnalysisResult,
    checks: list[AnalysisReviewCheck],
) -> None:
    try:
        if not content or len(content) > _MAX_SUMMARY_BYTES:
            raise ValueError("invalid summary size")
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("invalid summary header")
        actual = [dict(row) for row in reader]
        expected = _expected_summary_rows(result)
        if isinstance(result.result, DescriptiveAnalysisResult):
            actual.sort(key=lambda row: (row.get("column", ""), row.get("statistic", "")))
            expected.sort(
                key=lambda row: (str(row.get("column", "")), str(row.get("statistic", "")))
            )
        if len(actual) != len(expected) or any(
            set(row) != set(expected_row)
            or any(
                not _csv_value_matches(row[key], expected_row[key])
                for key in expected_row
            )
            for row, expected_row in zip(actual, expected, strict=True)
        ):
            raise ValueError("summary contents differ")
    except (UnicodeDecodeError, csv.Error, ValueError):
        _check(
            checks,
            code="summary-matches-results",
            category="results",
            passed=False,
            passed_message="summary.csv is consistent with results.json.",
            failed_message="summary.csv is missing, malformed, or inconsistent with results.json.",
        )
        return
    _check(
        checks,
        code="summary-matches-results",
        category="results",
        passed=True,
        passed_message="summary.csv is consistent with results.json.",
        failed_message="summary.csv is missing, malformed, or inconsistent with results.json.",
    )


def _expected_summary_rows(
    result: StructuredAnalysisResult,
) -> list[dict[str, int | float | str | None]]:
    operation_result = result.result
    if isinstance(operation_result, DescriptiveAnalysisResult):
        return [
            {
                "column": _safe_csv_value(column.column),
                "statistic": statistic,
                "value": value,
            }
            for column in operation_result.columns
            for statistic, value in column.statistics.items()
        ]
    if isinstance(operation_result, TwoGroupComparisonResult):
        return [
            {
                "group": _safe_csv_value(group),
                "sampleSize": operation_result.sample_sizes[group],
                "missingCount": operation_result.missing_counts[group],
                **operation_result.descriptive_statistics[group],
            }
            for group in operation_result.groups
        ]
    assert isinstance(operation_result, CorrelationAnalysisResult)
    interval = operation_result.confidence_interval
    return [
        {
            "xColumn": _safe_csv_value(operation_result.x_column),
            "yColumn": _safe_csv_value(operation_result.y_column),
            "sampleSize": operation_result.sample_size,
            "missingPairs": operation_result.missing_pairs,
            "method": result.resolved_method,
            "correlation": operation_result.correlation,
            "pValue": operation_result.p_value,
            "confidenceIntervalLower": None if interval is None else interval[0],
            "confidenceIntervalUpper": None if interval is None else interval[1],
        }
    ]


def _csv_value_matches(actual: str, expected: int | float | str | None) -> bool:
    if expected is None:
        return actual == ""
    if isinstance(expected, bool):
        return actual == str(expected)
    if isinstance(expected, int):
        try:
            value = float(actual)
        except ValueError:
            return False
        return math.isfinite(value) and value.is_integer() and value == expected
    if isinstance(expected, float):
        try:
            value = float(actual)
        except ValueError:
            return False
        return math.isfinite(value) and math.isclose(
            value,
            expected,
            rel_tol=_FLOAT_REL_TOLERANCE,
            abs_tol=_FLOAT_ABS_TOLERANCE,
        )
    return actual == expected


def _safe_csv_value(value: str) -> str:
    cleaned = "".join(character if character.isprintable() else " " for character in value)
    if cleaned.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + cleaned
    return cleaned


def _review_figure(
    spec: AnalysisSpec,
    identity: AnalysisReviewIdentity,
    lineage: FigureLineage | None,
    checks: list[AnalysisReviewCheck],
) -> None:
    operation = spec.operation
    expects_figure = operation.plot != "none"
    if not expects_figure:
        _check(
            checks,
            code="figure-lineage-matches",
            category="results",
            passed=lineage is None,
            passed_message="No figure was requested or emitted.",
            failed_message="An undeclared figure was emitted for a no-plot AnalysisSpec.",
        )
        return
    if operation.type == "descriptive":
        expected_columns = operation.columns
    elif operation.type == "two-group-comparison":
        expected_columns = [operation.group_column, operation.outcome_column]
    else:
        expected_columns = [operation.x_column, operation.y_column]
    passed = (
        lineage is not None
        and lineage.analysis_spec_sha256 == identity.analysis_spec_sha256
        and lineage.code_sha256 == identity.code_sha256
        and lineage.columns == expected_columns
    )
    _check(
        checks,
        code="figure-lineage-matches",
        category="results",
        passed=passed,
        passed_message="The figure is bound to the approved Spec, code, and columns.",
        failed_message="The requested figure is missing or has inconsistent lineage.",
        artifact_id=None if lineage is None else lineage.artifact_id,
    )


def _review_notebook(
    content: bytes,
    approved_code: str,
    checks: list[AnalysisReviewCheck],
) -> None:
    artifact_id = None
    try:
        if not content or len(content) > _MAX_JSON_BYTES:
            raise ValueError("invalid notebook size")
        raw: object = json.loads(content)
        if not isinstance(raw, dict):
            raise ValueError("notebook must be an object")
        cells = cast(dict[str, Any], raw).get("cells")
        if not isinstance(cells, list):
            raise ValueError("notebook cells are invalid")
        analysis_sources: list[str] = []
        for cell_value in cast(list[object], cells):
            if not isinstance(cell_value, dict):
                raise ValueError("notebook cell is invalid")
            cell = cast(dict[str, Any], cell_value)
            metadata = cell.get("metadata")
            if not isinstance(metadata, dict):
                raise ValueError("notebook metadata is invalid")
            if cell.get("cell_type") != "code" or cast(dict[str, Any], metadata).get(
                "tags"
            ) != ["analysis"]:
                continue
            source = cell.get("source")
            if isinstance(source, str):
                analysis_sources.append(source)
            elif isinstance(source, list) and all(
                isinstance(part, str) for part in cast(list[object], source)
            ):
                analysis_sources.append("".join(cast(list[str], source)))
            else:
                raise ValueError("analysis cell source is invalid")
        if analysis_sources != [approved_code]:
            raise ValueError("analysis cell differs")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _check(
            checks,
            code="notebook-code-matches",
            category="results",
            passed=False,
            passed_message="The executed notebook contains the exact approved analysis code.",
            failed_message="The executed notebook does not contain the exact approved analysis code.",
            artifact_id=artifact_id,
        )
        return
    _check(
        checks,
        code="notebook-code-matches",
        category="results",
        passed=True,
        passed_message="The executed notebook contains the exact approved analysis code.",
        failed_message="The executed notebook does not contain the exact approved analysis code.",
        artifact_id=artifact_id,
    )


def _review_language_guards(
    spec: AnalysisSpec,
    result: StructuredAnalysisResult,
    checks: list[AnalysisReviewCheck],
) -> None:
    cautions: list[str] = []
    if result.operation_type == "correlation":
        cautions.append("Correlation will be described as association, not causation.")
    operation_result = result.result
    if isinstance(operation_result, (TwoGroupComparisonResult, CorrelationAnalysisResult)):
        if operation_result.p_value >= 1.0 - spec.confidence_level:
            cautions.append(
                "A non-significant result will be described as insufficient evidence, not no effect."
            )
        else:
            cautions.append(
                "Statistical significance will not be presented as practical importance."
            )
    _check(
        checks,
        code="language-guard-applied",
        category="language",
        passed=True,
        passed_message=(
            "Deterministic conclusion language reports sample size and missingness. "
            + (" ".join(cautions) if cautions else "No unexecuted method is claimed.")
        ),
        failed_message="Deterministic conclusion language guard could not be applied.",
    )
    for warning in [*result.warnings, *result.limitations]:
        checks.append(
            AnalysisReviewCheck(
                code="reported-analysis-limitation",
                category="language",
                status="warning",
                message=warning,
                artifact_id=None,
            )
        )


def _conclusion(spec: AnalysisSpec, result: StructuredAnalysisResult) -> str:
    operation_result = result.result
    sample = result.sample_summary
    sample_text = (
        f"The analysis used {sample.analyzed_rows} of {sample.total_rows} row(s) and "
        f"reported {sample.missing_rows} row(s) with missing analysis values."
    )
    if isinstance(operation_result, DescriptiveAnalysisResult):
        column_samples = "; ".join(
            f"{item.column}: n={item.sample_size}, missing={item.missing_count}"
            for item in operation_result.columns
        )
        return (
            f"The dataset contained {sample.total_rows} row(s). The approved descriptive "
            f"analysis used column-specific available values ({column_samples}). "
            "These summaries do not establish group differences, associations, or causation. "
            + _limitations_text(result)
        ).strip()
    alpha = 1.0 - spec.confidence_level
    if isinstance(operation_result, TwoGroupComparisonResult):
        first, second = operation_result.groups
        interval = operation_result.confidence_interval
        group_sample_text = (
            f"{first}: n={operation_result.sample_sizes[first]}, missing="
            f"{operation_result.missing_counts[first]}; {second}: "
            f"n={operation_result.sample_sizes[second]}, missing="
            f"{operation_result.missing_counts[second]}."
        )
        evidence = (
            "The data provide statistical evidence of a group difference at the approved "
            f"alpha level of {alpha:.3g}. Statistical significance alone does not establish "
            "practical importance."
            if operation_result.p_value < alpha
            else "The data do not provide sufficient statistical evidence of a group "
            f"difference at the approved alpha level of {alpha:.3g}; this is not proof that "
            "the groups are identical."
        )
        return (
            f"{sample_text} {group_sample_text} The approved {result.resolved_method} "
            f"comparison of {first} and "
            f"{second} produced statistic={operation_result.test_statistic:.6g}, "
            f"p={operation_result.p_value:.6g}, and {operation_result.effect_size_name}="
            f"{operation_result.effect_size:.6g} with a {spec.confidence_level:.0%} confidence "
            f"interval [{interval[0]:.6g}, {interval[1]:.6g}]. {evidence} "
            + _limitations_text(result)
        ).strip()
    assert isinstance(operation_result, CorrelationAnalysisResult)
    interval_text = (
        ""
        if operation_result.confidence_interval is None
        else (
            f" with a {spec.confidence_level:.0%} confidence interval "
            f"[{operation_result.confidence_interval[0]:.6g}, "
            f"{operation_result.confidence_interval[1]:.6g}]"
        )
    )
    evidence = (
        "The association is statistically significant at the approved alpha level, but "
        "significance alone does not establish practical importance."
        if operation_result.p_value < alpha
        else "The data do not provide sufficient statistical evidence of a non-zero "
        "association at the approved alpha level; this is not proof of no association."
    )
    return (
        f"{sample_text} The approved {result.resolved_method} association between "
        f"{operation_result.x_column} and {operation_result.y_column} was "
        f"{operation_result.correlation:.6g} (p={operation_result.p_value:.6g})"
        f"{interval_text}. {evidence} Correlation does not establish causation. "
        + _limitations_text(result)
    ).strip()


def _limitations_text(result: StructuredAnalysisResult) -> str:
    notes = list(dict.fromkeys([*result.warnings, *result.limitations]))
    return "" if not notes else "Recorded limitations: " + " ".join(notes)


def _revision_for(code: str) -> str:
    return {
        "goal-operation-executed": "Execute the exact operation approved in the AnalysisSpec.",
        "method-recorded": "Record the approved requested method and the resolved method.",
        "analysis-columns-match": "Regenerate results using exactly the approved columns.",
        "analysis-groups-match": "Regenerate results using exactly the approved groups.",
        "missing-value-policy-recorded": "Record the approved missing-value policy and counts.",
        "sample-size-present": "Regenerate results with an explicit positive analyzed sample size.",
        "missing-count-present": "Regenerate results with explicit missing-value counts.",
        "p-value-valid": "Regenerate results with a valid p-value.",
        "effect-size-present": "Regenerate results with the required compatible effect size.",
        "confidence-interval-present": "Regenerate results with the requested confidence interval.",
        "summary-matches-results": "Regenerate summary.csv from the exact structured result.",
        "figure-lineage-matches": "Regenerate the figure from the approved Spec and columns.",
        "notebook-code-matches": "Execute a notebook containing the exact approved code.",
    }.get(code, "Regenerate the inconsistent analysis artifact.")


def _terminal_review(
    checks: list[AnalysisReviewCheck],
    *,
    verdict: Literal["failed", "blocked"],
    revision: str,
    conclusion: str,
) -> AnalysisSpecReview:
    return AnalysisSpecReview(
        schema_version="1",
        verdict=verdict,
        checks=checks,
        required_revisions=[revision],
        conclusion=conclusion,
    )


def _check(
    checks: list[AnalysisReviewCheck],
    *,
    code: str,
    category: ReviewCategory,
    passed: bool,
    passed_message: str,
    failed_message: str,
    artifact_id: str | None = None,
) -> None:
    checks.append(
        AnalysisReviewCheck(
            code=code,
            category=category,
            status="passed" if passed else "failed",
            message=passed_message if passed else failed_message,
            artifact_id=artifact_id,
        )
    )
