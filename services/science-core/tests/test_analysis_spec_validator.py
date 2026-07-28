from __future__ import annotations

from dataclasses import replace
from typing import Callable

import pytest

from open_science_core.analysis_spec.schemas import (
    AnalysisSpec,
    CorrelationOperation,
    DescriptiveOperation,
    TwoGroupComparisonOperation,
)
from open_science_core.analysis_spec.validator import (
    AnalysisSpecValidationError,
    AnalysisValidationContext,
    ExactCorrelationPreflight,
    ExactTwoGroupPreflight,
    ValidatedAnalysisSpec,
    validate_analysis_spec,
)
from open_science_core.dataset_inspector import dataset_profile_sha256
from open_science_core.workflow.schemas import (
    DatasetColumnProfile,
    DatasetInspectionWarning,
    DatasetProfile,
    DatasetSamplingRecord,
)

SOURCE_ID = "dataset-1"
CONTENT_HASH = "a" * 64


def _column(
    index: int,
    name: str,
    inferred_type: str,
    *,
    missing: int = 0,
    unique: int = 20,
    values: list[str] | None = None,
    minimum: float = 0.0,
    maximum: float = 100.0,
) -> DatasetColumnProfile:
    return DatasetColumnProfile.model_validate(
        {
            "index": index,
            "name": name,
            "inferredType": inferred_type,
            "missingCount": missing,
            "uniqueCount": unique,
            "numericRange": (
                {"minimum": minimum, "maximum": maximum}
                if inferred_type in {"integer", "number"}
                else None
            ),
            "lowCardinality": (
                {"values": values, "truncated": False} if values is not None else None
            ),
            "potentialDate": False,
            "potentialId": False,
            "mixedType": False,
        }
    )


def _profile(
    columns: list[DatasetColumnProfile] | None = None,
    *,
    rows: int = 40,
    rows_profiled: int | None = None,
    warnings: list[DatasetInspectionWarning] | None = None,
) -> DatasetProfile:
    selected = columns or [
        _column(0, "group", "categorical", unique=2, values=["treatment", "control"]),
        _column(1, "score", "number", unique=36),
        _column(2, "sleep_hours", "number", unique=30),
        _column(3, "cognitive_score", "number", unique=32),
    ]
    profiled = rows if rows_profiled is None else rows_profiled
    return DatasetProfile(
        schema_version="1",
        dataset_source_id=SOURCE_ID,
        filename="study.csv",
        content_hash=CONTENT_HASH,
        file_size_bytes=1_000,
        encoding="utf-8",
        delimiter=",",
        row_count=rows,
        column_count=len(selected),
        columns=selected,
        sampling=DatasetSamplingRecord(
            method="head-and-reservoir-v1",
            rows_read=rows,
            rows_profiled=profiled,
            max_sample_rows=500,
            seed=1,
        ),
        warnings=warnings or [],
    )


def _context(
    profile: DatasetProfile | None = None,
    *,
    two_group_preflight: ExactTwoGroupPreflight | None = None,
    correlation_preflight: ExactCorrelationPreflight | None = None,
) -> AnalysisValidationContext:
    selected = profile or _profile()
    return AnalysisValidationContext(
        project_id="project-1",
        source_project_id="project-1",
        source_kind="dataset",
        source_status="ready",
        source_id=SOURCE_ID,
        source_content_hash=CONTENT_HASH,
        profile=selected,
        profile_sha256=dataset_profile_sha256(selected),
        two_group_preflight=two_group_preflight,
        correlation_preflight=correlation_preflight,
    )


def _spec(
    operation: object,
    profile: DatasetProfile | None = None,
    *,
    missing_value_policy: str = "drop-per-operation",
) -> AnalysisSpec:
    context = _context(profile)
    return AnalysisSpec.model_validate(
        {
            "schemaVersion": "1",
            "objective": "Test the stated scientific question.",
            "datasetSourceId": SOURCE_ID,
            "datasetContentHash": CONTENT_HASH,
            "datasetProfileHash": context.profile_sha256,
            "operation": operation,
            "missingValuePolicy": missing_value_policy,
            "confidenceLevel": 0.95,
            "randomSeed": 7,
            "assumptions": [],
            "limitations": ["The dataset profile uses bounded sampling."],
        }
    )


def _change_content_hash(context: AnalysisValidationContext) -> AnalysisValidationContext:
    return replace(context, source_content_hash="b" * 64)


def _change_profile_hash(context: AnalysisValidationContext) -> AnalysisValidationContext:
    return replace(context, profile_sha256="b" * 64)


def _change_project(context: AnalysisValidationContext) -> AnalysisValidationContext:
    return replace(context, source_project_id="other")


def test_validates_descriptive_and_numeric_plot() -> None:
    spec = _spec(
        DescriptiveOperation(
            type="descriptive",
            columns=["score"],
            statistics=["count", "missing", "mean", "std"],
            plot="histogram",
        )
    )

    validated = validate_analysis_spec(spec, _context())

    assert validated.resolved_method == "descriptive"
    assert validated.referenced_columns == ("score",)


@pytest.mark.parametrize(
    ("method", "effect_size", "expected"),
    [
        ("welch-t-test", "hedges-g", "welch-t-test"),
        ("mann-whitney-u", "rank-biserial", "mann-whitney-u"),
    ],
)
def test_validates_explicit_two_group_methods(
    method: str,
    effect_size: str,
    expected: str,
) -> None:
    spec = _spec(
        TwoGroupComparisonOperation.model_validate(
            {
                "type": "two-group-comparison",
                "outcomeColumn": "score",
                "groupColumn": "group",
                "groups": ("treatment", "control"),
                "method": method,
                "effectSize": effect_size,
                "checkAssumptions": True,
                "plot": "boxplot",
            }
        )
    )

    assert validate_analysis_spec(spec, _context()).resolved_method == expected


@pytest.mark.parametrize(("method", "expected"), [("pearson", "pearson"), ("spearman", "spearman")])
def test_validates_explicit_correlations(method: str, expected: str) -> None:
    spec = _spec(
        CorrelationOperation.model_validate(
            {
                "type": "correlation",
                "xColumn": "sleep_hours",
                "yColumn": "cognitive_score",
                "method": method,
                "confidenceInterval": True,
                "plot": "scatter",
            }
        )
    )

    validated = validate_analysis_spec(spec, _context())
    assert validated.resolved_method == expected
    assert "causation" in validated.method_selection_reason


@pytest.mark.parametrize(
    ("mutate_context", "expected_code"),
    [
        (_change_content_hash, "dataset-content-changed"),
        (_change_profile_hash, "dataset-profile-context-invalid"),
        (_change_project, "dataset-project-mismatch"),
    ],
)
def test_rejects_identity_changes(
    mutate_context: Callable[[AnalysisValidationContext], AnalysisValidationContext],
    expected_code: str,
) -> None:
    spec = _spec(
        DescriptiveOperation(
            type="descriptive",
            columns=["score"],
            statistics=["count"],
            plot="none",
        )
    )
    context = mutate_context(_context())

    with pytest.raises(AnalysisSpecValidationError) as caught:
        validate_analysis_spec(spec, context)

    assert caught.value.code == expected_code


def test_rejects_non_numeric_outcome() -> None:
    spec = _spec(
        TwoGroupComparisonOperation(
            type="two-group-comparison",
            outcome_column="group",
            group_column="score",
            groups=("treatment", "control"),
            method="welch-t-test",
            effect_size="hedges-g",
            check_assumptions=True,
            plot="boxplot",
        )
    )

    with pytest.raises(AnalysisSpecValidationError) as caught:
        validate_analysis_spec(spec, _context())

    assert caught.value.code == "outcome-column-not-numeric"


def test_rejects_one_group_and_too_few_pairs() -> None:
    one_group = _profile(
        [
            _column(0, "group", "categorical", unique=1, values=["treatment"]),
            _column(1, "score", "number", unique=10),
        ]
    )
    group_spec = _spec(
        TwoGroupComparisonOperation(
            type="two-group-comparison",
            outcome_column="score",
            group_column="group",
            groups=("treatment", "control"),
            method="welch-t-test",
            effect_size="hedges-g",
            check_assumptions=True,
            plot="none",
        ),
        one_group,
    )
    with pytest.raises(AnalysisSpecValidationError) as group_error:
        validate_analysis_spec(group_spec, _context(one_group))
    assert group_error.value.code == "group-column-has-one-value"

    too_small = _profile(
        [
            _column(0, "x", "number", missing=1, unique=3),
            _column(1, "y", "number", missing=1, unique=3),
        ],
        rows=4,
    )
    correlation = _spec(
        CorrelationOperation(
            type="correlation",
            x_column="x",
            y_column="y",
            method="pearson",
            confidence_interval=True,
            plot="none",
        ),
        too_small,
    )
    with pytest.raises(AnalysisSpecValidationError) as pair_error:
        validate_analysis_spec(
            correlation,
            _context(
                too_small,
                correlation_preflight=ExactCorrelationPreflight(
                    x_column="x",
                    y_column="y",
                    valid_pair_count=2,
                ),
            ),
        )
    assert pair_error.value.code == "correlation-sample-too-small"


def test_rejects_duplicate_profile_headers_as_ambiguous() -> None:
    profile = _profile(
        [
            _column(0, "score", "number", unique=20),
            _column(1, "score", "number", unique=20),
        ]
    )
    spec = _spec(
        DescriptiveOperation(
            type="descriptive",
            columns=["score"],
            statistics=["mean"],
            plot="none",
        ),
        profile,
    )

    with pytest.raises(AnalysisSpecValidationError) as caught:
        validate_analysis_spec(spec, _context(profile))

    assert caught.value.code == "dataset-column-names-ambiguous"


def test_recomputes_profile_hash_and_rejects_inconsistent_context() -> None:
    profile = _profile()
    spec = _spec(
        DescriptiveOperation(
            type="descriptive",
            columns=["score"],
            statistics=["count"],
            plot="none",
        ),
        profile,
    )
    changed_profile = profile.model_copy(update={"filename": "changed.csv"})
    stale_context = replace(_context(profile), profile=changed_profile)

    with pytest.raises(AnalysisSpecValidationError) as caught:
        validate_analysis_spec(spec, stale_context)

    assert caught.value.code == "dataset-profile-context-invalid"


def test_validated_spec_enforces_operation_method_effect_and_dataset_format() -> None:
    profile = _profile()
    spec = _spec(
        TwoGroupComparisonOperation(
            type="two-group-comparison",
            outcome_column="score",
            group_column="group",
            groups=("treatment", "control"),
            method="auto",
            effect_size="hedges-g",
            check_assumptions=True,
            plot="none",
        ),
        profile,
    )

    with pytest.raises(ValueError, match="effect size"):
        ValidatedAnalysisSpec(
            spec=spec,
            resolved_method="mann-whitney-u",
            method_selection_reason="The bounded rule selected Mann-Whitney U.",
            referenced_columns=("score", "group"),
            dataset_delimiter=",",
            dataset_encoding="utf-8",
        )
    with pytest.raises(ValueError, match="delimiter"):
        ValidatedAnalysisSpec(
            spec=spec,
            resolved_method="welch-t-test",
            method_selection_reason="The bounded rule selected Welch's t-test.",
            referenced_columns=("score", "group"),
            dataset_delimiter="^",
            dataset_encoding="utf-8",
        )


def test_auto_normalizes_effect_size_after_method_resolution() -> None:
    profile = _profile(
        [
            _column(0, "group", "categorical", unique=2, values=["treatment", "control"]),
            _column(1, "score", "number", unique=5),
        ]
    )
    spec = _spec(
        TwoGroupComparisonOperation(
            type="two-group-comparison",
            outcome_column="score",
            group_column="group",
            groups=("treatment", "control"),
            method="auto",
            effect_size="hedges-g",
            check_assumptions=True,
            plot="none",
        ),
        profile,
    )

    validated = validate_analysis_spec(spec, _context(profile))

    assert validated.resolved_method == "mann-whitney-u"
    assert isinstance(validated.spec.operation, TwoGroupComparisonOperation)
    assert validated.spec.operation.method == "auto"
    assert validated.spec.operation.effect_size == "rank-biserial"
    assert validated.dataset_delimiter == ","
    assert validated.dataset_encoding == "utf-8"


@pytest.mark.parametrize(
    ("columns", "operation", "expected_code"),
    [
        (
            [
                _column(0, "group", "categorical", unique=2, values=["a", "b"]),
                _column(1, "score", "number", missing=40, unique=0),
            ],
            TwoGroupComparisonOperation(
                type="two-group-comparison",
                outcome_column="score",
                group_column="group",
                groups=("a", "b"),
                method="welch-t-test",
                effect_size="hedges-g",
                check_assumptions=True,
                plot="none",
            ),
            "outcome-column-all-missing",
        ),
        (
            [
                _column(0, "x", "number", unique=1, minimum=4.0, maximum=4.0),
                _column(1, "y", "number", unique=20),
            ],
            CorrelationOperation(
                type="correlation",
                x_column="x",
                y_column="y",
                method="pearson",
                confidence_interval=True,
                plot="none",
            ),
            "x-column-constant",
        ),
    ],
)
def test_rejects_proven_degenerate_inferential_columns(
    columns: list[DatasetColumnProfile],
    operation: object,
    expected_code: str,
) -> None:
    profile = _profile(columns)
    spec = _spec(operation, profile)

    with pytest.raises(AnalysisSpecValidationError) as caught:
        validate_analysis_spec(spec, _context(profile))

    assert caught.value.code == expected_code


def test_rejects_standard_deviation_with_one_non_missing_value() -> None:
    profile = _profile(
        [_column(0, "score", "number", missing=39, unique=1)],
    )
    spec = _spec(
        DescriptiveOperation(
            type="descriptive",
            columns=["score"],
            statistics=["count", "std"],
            plot="none",
        ),
        profile,
    )

    with pytest.raises(AnalysisSpecValidationError) as caught:
        validate_analysis_spec(spec, _context(profile))

    assert caught.value.code == "descriptive-sample-too-small-for-standard-deviation"


def test_report_only_rejects_observed_missing_values_for_inference() -> None:
    profile = _profile(
        [
            _column(0, "group", "categorical", unique=2, values=["a", "b"]),
            _column(1, "score", "number", missing=1, unique=20),
        ]
    )
    spec = _spec(
        TwoGroupComparisonOperation(
            type="two-group-comparison",
            outcome_column="score",
            group_column="group",
            groups=("a", "b"),
            method="welch-t-test",
            effect_size="hedges-g",
            check_assumptions=True,
            plot="none",
        ),
        profile,
        missing_value_policy="report-only",
    )

    with pytest.raises(AnalysisSpecValidationError) as caught:
        validate_analysis_spec(spec, _context(profile))

    assert caught.value.code == "report-only-inferential-missing-values"
    assert caught.value.kind == "clarification-required"


def test_two_group_exact_preflight_is_strict_and_absence_adds_runtime_guard() -> None:
    profile = _profile()
    spec = _spec(
        TwoGroupComparisonOperation(
            type="two-group-comparison",
            outcome_column="score",
            group_column="group",
            groups=("treatment", "control"),
            method="welch-t-test",
            effect_size="hedges-g",
            check_assumptions=True,
            plot="none",
        ),
        profile,
    )

    without_exact = validate_analysis_spec(spec, _context(profile))
    assert "minimum-three-valid-observations-per-group" in without_exact.runtime_guards

    with pytest.raises(AnalysisSpecValidationError) as caught:
        validate_analysis_spec(
            spec,
            _context(
                profile,
                two_group_preflight=ExactTwoGroupPreflight(
                    outcome_column="score",
                    group_column="group",
                    valid_counts={"treatment": 2, "control": 20},
                    non_constant_groups={"treatment": True, "control": True},
                ),
            ),
        )
    assert caught.value.code == "two-group-sample-too-small"

    with_exact = validate_analysis_spec(
        spec,
        _context(
            profile,
            two_group_preflight=ExactTwoGroupPreflight(
                outcome_column="score",
                group_column="group",
                valid_counts={"treatment": 20, "control": 20},
                non_constant_groups={"treatment": True, "control": True},
            ),
        ),
    )
    assert "minimum-three-valid-observations-per-group" not in with_exact.runtime_guards

    with pytest.raises(AnalysisSpecValidationError) as zero_variance:
        validate_analysis_spec(
            spec,
            _context(
                profile,
                two_group_preflight=ExactTwoGroupPreflight(
                    outcome_column="score",
                    group_column="group",
                    valid_counts={"treatment": 20, "control": 20},
                    non_constant_groups={"treatment": False, "control": True},
                ),
            ),
        )
    assert zero_variance.value.code == "welch-group-variance-zero"


def test_correlation_profile_bound_does_not_claim_exact_pairs() -> None:
    profile = _profile(
        [
            _column(0, "x", "number", missing=1, unique=3),
            _column(1, "y", "number", missing=1, unique=3),
        ],
        rows=4,
    )
    spec = _spec(
        CorrelationOperation(
            type="correlation",
            x_column="x",
            y_column="y",
            method="pearson",
            confidence_interval=True,
            plot="none",
        ),
        profile,
    )

    validated = validate_analysis_spec(spec, _context(profile))

    assert validated.runtime_guards == ("minimum-three-complete-pairs",)


def test_sample_limited_profile_does_not_false_block_group_counts() -> None:
    profile = _profile(
        [
            _column(0, "group", "categorical", unique=2, values=["a", "b"]),
            _column(1, "score", "number", unique=4),
        ],
        rows=100,
        rows_profiled=4,
    )
    spec = _spec(
        TwoGroupComparisonOperation(
            type="two-group-comparison",
            outcome_column="score",
            group_column="group",
            groups=("a", "b"),
            method="welch-t-test",
            effect_size="hedges-g",
            check_assumptions=True,
            plot="none",
        ),
        profile,
    )

    validated = validate_analysis_spec(spec, _context(profile))

    assert "minimum-three-valid-observations-per-group" in validated.runtime_guards
    assert "non-degenerate-outcome" in validated.runtime_guards


def test_rejects_large_descriptive_plot_and_lossy_group_values() -> None:
    plot_profile = _profile(
        [_column(index, f"value_{index}", "number") for index in range(11)]
    )
    plot_spec = _spec(
        DescriptiveOperation(
            type="descriptive",
            columns=[f"value_{index}" for index in range(11)],
            statistics=["count"],
            plot="histogram",
        ),
        plot_profile,
    )
    with pytest.raises(AnalysisSpecValidationError) as plot_error:
        validate_analysis_spec(plot_spec, _context(plot_profile))
    assert plot_error.value.code == "descriptive-plot-column-limit-exceeded"

    lossy_warning = DatasetInspectionWarning(
        code="other",
        message=(
            "Control characters, formula-like prefixes, or overlong display values "
            "were escaped only in the bounded profile; the source file was unchanged."
        ),
        column_name=None,
    )
    group_profile = _profile(warnings=[lossy_warning])
    group_spec = _spec(
        TwoGroupComparisonOperation(
            type="two-group-comparison",
            outcome_column="score",
            group_column="group",
            groups=("treatment", "control"),
            method="welch-t-test",
            effect_size="hedges-g",
            check_assumptions=True,
            plot="none",
        ),
        group_profile,
    )
    with pytest.raises(AnalysisSpecValidationError) as group_error:
        validate_analysis_spec(group_spec, _context(group_profile))
    assert group_error.value.code == "group-values-not-executable-from-profile"


@pytest.mark.parametrize(
    ("delimiter", "encoding"),
    [("^", "utf-8"), (",", "utf-16")],
)
def test_rejects_csv_format_outside_runtime_allowlist(
    delimiter: str,
    encoding: str,
) -> None:
    profile = _profile().model_copy(
        update={"delimiter": delimiter, "encoding": encoding}
    )
    spec = _spec(
        DescriptiveOperation(
            type="descriptive",
            columns=["score"],
            statistics=["count"],
            plot="none",
        ),
        profile,
    )

    with pytest.raises(AnalysisSpecValidationError) as caught:
        validate_analysis_spec(spec, _context(profile))

    assert caught.value.code == "dataset-profile-csv-format-invalid"
