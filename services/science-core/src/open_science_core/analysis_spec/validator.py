from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from ..dataset_inspector import dataset_profile_sha256
from ..workflow.schemas import DatasetColumnProfile, DatasetProfile
from .schemas import (
    AnalysisSpec,
    CorrelationOperation,
    DescriptiveOperation,
    TwoGroupComparisonOperation,
)

NUMERIC_TYPES = frozenset({"integer", "number"})
NUMERIC_STATISTICS = frozenset(
    {"mean", "std", "median", "min", "max", "q1", "q3", "iqr"}
)
MIN_GROUP_SAMPLE_SIZE = 3
MIN_PAIRED_SAMPLE_SIZE = 3
MAX_DESCRIPTIVE_PLOT_COLUMNS = 10
ALLOWED_DATASET_DELIMITERS = frozenset({",", ";", "\t", "|"})
ALLOWED_DATASET_ENCODINGS = frozenset({"utf-8", "utf-8-sig", "cp1252", "latin-1"})

ValidationFailureKind = Literal[
    "clarification-required",
    "unsupported",
    "blocked",
]
ResolvedMethod = Literal[
    "descriptive",
    "welch-t-test",
    "mann-whitney-u",
    "pearson",
    "spearman",
]


class AnalysisSpecValidationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        kind: ValidationFailureKind,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.kind = kind


@dataclass(frozen=True, slots=True)
class ExactTwoGroupPreflight:
    outcome_column: str
    group_column: str
    valid_counts: Mapping[str, int]
    non_constant_groups: Mapping[str, bool]

    def __post_init__(self) -> None:
        if not self.outcome_column or not self.group_column:
            raise ValueError("two-group preflight columns must be non-empty")
        if self.outcome_column == self.group_column:
            raise ValueError("two-group preflight columns must differ")
        counts = dict(self.valid_counts)
        if any(
            not group
            or isinstance(count, bool)
            or count < 0
            for group, count in counts.items()
        ):
            raise ValueError("two-group preflight counts must be non-negative integers")
        object.__setattr__(self, "valid_counts", MappingProxyType(counts))
        non_constant = dict(self.non_constant_groups)
        if set(non_constant) != set(counts):
            raise ValueError(
                "two-group preflight variance flags must cover the exact groups"
            )
        object.__setattr__(
            self,
            "non_constant_groups",
            MappingProxyType(non_constant),
        )


@dataclass(frozen=True, slots=True)
class ExactCorrelationPreflight:
    x_column: str
    y_column: str
    valid_pair_count: int

    def __post_init__(self) -> None:
        if not self.x_column or not self.y_column:
            raise ValueError("correlation preflight columns must be non-empty")
        if self.x_column == self.y_column:
            raise ValueError("correlation preflight columns must differ")
        if (
            isinstance(self.valid_pair_count, bool)
            or self.valid_pair_count < 0
        ):
            raise ValueError("valid pair count must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class AnalysisValidationContext:
    project_id: str
    source_project_id: str
    source_kind: str
    source_status: str
    source_id: str
    source_content_hash: str
    profile: DatasetProfile
    profile_sha256: str
    two_group_preflight: ExactTwoGroupPreflight | None = None
    correlation_preflight: ExactCorrelationPreflight | None = None


@dataclass(frozen=True, slots=True)
class ValidatedAnalysisSpec:
    spec: AnalysisSpec
    resolved_method: ResolvedMethod
    method_selection_reason: str
    referenced_columns: tuple[str, ...]
    dataset_delimiter: str
    dataset_encoding: str
    runtime_guards: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.method_selection_reason.strip():
            raise ValueError("method selection reason must be non-empty")
        if self.dataset_delimiter not in ALLOWED_DATASET_DELIMITERS:
            raise ValueError("dataset delimiter is outside the runtime allowlist")
        if self.dataset_encoding not in ALLOWED_DATASET_ENCODINGS:
            raise ValueError("dataset encoding is outside the runtime allowlist")
        if len(self.runtime_guards) != len(set(self.runtime_guards)):
            raise ValueError("runtime guards must be unique")

        operation = self.spec.operation
        if isinstance(operation, DescriptiveOperation):
            expected_columns = tuple(operation.columns)
            if self.resolved_method != "descriptive":
                raise ValueError("descriptive specs require the descriptive resolved method")
        elif isinstance(operation, TwoGroupComparisonOperation):
            expected_columns = (operation.outcome_column, operation.group_column)
            if self.resolved_method not in {"welch-t-test", "mann-whitney-u"}:
                raise ValueError("two-group specs require a comparison resolved method")
            if operation.method != "auto" and operation.method != self.resolved_method:
                raise ValueError("explicit comparison method must match the resolved method")
            expected_effect = (
                "hedges-g" if self.resolved_method == "welch-t-test" else "rank-biserial"
            )
            if operation.effect_size != expected_effect:
                raise ValueError("comparison effect size must match the resolved method")
        else:
            expected_columns = (operation.x_column, operation.y_column)
            if self.resolved_method not in {"pearson", "spearman"}:
                raise ValueError("correlation specs require a correlation resolved method")
            if operation.method != "auto" and operation.method != self.resolved_method:
                raise ValueError("explicit correlation method must match the resolved method")
        if self.referenced_columns != expected_columns:
            raise ValueError("referenced columns must exactly match the analysis operation")


def validate_analysis_spec(
    spec: AnalysisSpec,
    context: AnalysisValidationContext,
) -> ValidatedAnalysisSpec:
    """Validate a parsed AnalysisSpec against immutable source and profile facts."""

    _validate_identity(spec, context)
    columns = _unambiguous_columns(context.profile)
    operation = spec.operation
    if isinstance(operation, DescriptiveOperation):
        return _validate_descriptive(spec, operation, columns, context.profile)
    if isinstance(operation, TwoGroupComparisonOperation):
        return _validate_two_group(
            spec,
            operation,
            columns,
            context.profile,
            context.two_group_preflight,
        )
    return _validate_correlation(
        spec,
        operation,
        columns,
        context.profile,
        context.correlation_preflight,
    )


def _validate_identity(spec: AnalysisSpec, context: AnalysisValidationContext) -> None:
    if context.source_project_id != context.project_id:
        raise AnalysisSpecValidationError(
            "dataset-project-mismatch",
            "The dataset does not belong to the current project.",
            kind="blocked",
        )
    if context.source_kind != "dataset" or context.source_status != "ready":
        raise AnalysisSpecValidationError(
            "dataset-not-ready",
            "Analysis requires a ready dataset source.",
            kind="blocked",
        )
    if spec.dataset_source_id != context.source_id:
        raise AnalysisSpecValidationError(
            "dataset-source-mismatch",
            "The analysis specification names a different dataset source.",
            kind="blocked",
        )
    if (
        spec.dataset_content_hash != context.source_content_hash
        or context.profile.content_hash != context.source_content_hash
    ):
        raise AnalysisSpecValidationError(
            "dataset-content-changed",
            "The dataset content hash changed after method selection.",
            kind="blocked",
        )
    computed_profile_sha256 = dataset_profile_sha256(context.profile)
    if context.profile_sha256 != computed_profile_sha256:
        raise AnalysisSpecValidationError(
            "dataset-profile-context-invalid",
            "The supplied dataset profile hash does not hash the supplied profile.",
            kind="blocked",
        )
    if spec.dataset_profile_hash != computed_profile_sha256:
        raise AnalysisSpecValidationError(
            "dataset-profile-changed",
            "The dataset profile changed after method selection.",
            kind="blocked",
        )
    if context.profile.dataset_source_id != context.source_id:
        raise AnalysisSpecValidationError(
            "dataset-profile-source-mismatch",
            "The dataset profile is bound to a different source.",
            kind="blocked",
        )
    if (
        context.profile.delimiter not in ALLOWED_DATASET_DELIMITERS
        or context.profile.encoding not in ALLOWED_DATASET_ENCODINGS
    ):
        raise AnalysisSpecValidationError(
            "dataset-profile-csv-format-invalid",
            "The dataset CSV format is outside the bounded runtime allowlist.",
            kind="blocked",
        )


def _unambiguous_columns(profile: DatasetProfile) -> dict[str, DatasetColumnProfile]:
    names = [column.name for column in profile.columns]
    duplicates = {name for name, count in Counter(names).items() if count > 1}
    if duplicates:
        joined = ", ".join(sorted(duplicates))
        raise AnalysisSpecValidationError(
            "dataset-column-names-ambiguous",
            f"Duplicate dataset column names cannot be referenced safely: {joined}.",
            kind="blocked",
        )
    return {column.name: column for column in profile.columns}


def _require_column(
    columns: dict[str, DatasetColumnProfile],
    name: str,
) -> DatasetColumnProfile:
    column = columns.get(name)
    if column is None:
        raise AnalysisSpecValidationError(
            "analysis-column-missing",
            f"The analysis column {name!r} is not present in the dataset profile.",
            kind="clarification-required",
        )
    return column


def _require_numeric(column: DatasetColumnProfile, *, role: str) -> None:
    if column.inferred_type not in NUMERIC_TYPES or column.mixed_type:
        raise AnalysisSpecValidationError(
            f"{role}-column-not-numeric",
            f"The {role.replace('-', ' ')} column must be numeric.",
            kind="clarification-required",
        )


def _profile_is_complete(profile: DatasetProfile) -> bool:
    return profile.sampling.rows_profiled == profile.row_count


def _profiled_non_missing_count(
    column: DatasetColumnProfile,
    profile: DatasetProfile,
) -> int:
    return profile.sampling.rows_profiled - column.missing_count


def _require_profiled_numeric_sample(
    column: DatasetColumnProfile,
    profile: DatasetProfile,
    *,
    require_two: bool,
    role: str,
) -> None:
    if not _profile_is_complete(profile):
        return
    non_missing = _profiled_non_missing_count(column, profile)
    if non_missing == 0:
        raise AnalysisSpecValidationError(
            f"{role}-column-all-missing",
            f"The {role.replace('-', ' ')} column has no non-missing numeric values.",
            kind="blocked",
        )
    if require_two and non_missing < 2:
        raise AnalysisSpecValidationError(
            f"{role}-sample-too-small-for-standard-deviation",
            "A sample standard deviation requires at least two non-missing values.",
            kind="blocked",
        )


def _require_non_degenerate_inferential_column(
    column: DatasetColumnProfile,
    profile: DatasetProfile,
    *,
    role: str,
) -> None:
    if not _profile_is_complete(profile):
        return
    non_missing = _profiled_non_missing_count(column, profile)
    if non_missing == 0:
        raise AnalysisSpecValidationError(
            f"{role}-column-all-missing",
            f"The {role.replace('-', ' ')} column has no non-missing values.",
            kind="blocked",
        )
    if column.unique_count < 2 or (
        column.numeric_range is not None
        and column.numeric_range.minimum == column.numeric_range.maximum
    ):
        raise AnalysisSpecValidationError(
            f"{role}-column-constant",
            f"The {role.replace('-', ' ')} column is constant.",
            kind="blocked",
        )


def _has_lossy_profile_values(profile: DatasetProfile) -> bool:
    return any(
        warning.code == "other"
        and (
            "escaped only in the bounded profile" in warning.message
            or "full value was not retained in the profile" in warning.message
        )
        for warning in profile.warnings
    )


def _reject_report_only_missing_values(
    spec: AnalysisSpec,
    columns: tuple[DatasetColumnProfile, ...],
) -> None:
    if spec.missing_value_policy == "report-only" and any(
        column.missing_count > 0 for column in columns
    ):
        raise AnalysisSpecValidationError(
            "report-only-inferential-missing-values",
            (
                "Inferential analysis contains missing values but the selected policy "
                "does not authorize dropping them."
            ),
            kind="clarification-required",
        )


def _validate_descriptive(
    spec: AnalysisSpec,
    operation: DescriptiveOperation,
    columns: dict[str, DatasetColumnProfile],
    profile: DatasetProfile,
) -> ValidatedAnalysisSpec:
    selected = [_require_column(columns, name) for name in operation.columns]
    runtime_guards: list[str] = []
    if operation.plot != "none" and len(operation.columns) > MAX_DESCRIPTIVE_PLOT_COLUMNS:
        raise AnalysisSpecValidationError(
            "descriptive-plot-column-limit-exceeded",
            f"Descriptive plots support at most {MAX_DESCRIPTIVE_PLOT_COLUMNS} columns.",
            kind="clarification-required",
        )
    if NUMERIC_STATISTICS.intersection(operation.statistics):
        for column in selected:
            _require_numeric(column, role="descriptive")
            _require_profiled_numeric_sample(
                column,
                profile,
                require_two="std" in operation.statistics,
                role="descriptive",
            )
        if not _profile_is_complete(profile):
            runtime_guards.append("descriptive-numeric-sample-validity")
    if "frequency" in operation.statistics and any(
        column.inferred_type in NUMERIC_TYPES
        and (column.low_cardinality is None or column.unique_count > 20)
        for column in selected
    ):
        raise AnalysisSpecValidationError(
            "frequency-column-not-categorical",
            "Frequency tables require categorical or low-cardinality columns.",
            kind="clarification-required",
        )
    if operation.plot == "histogram":
        for column in selected:
            _require_numeric(column, role="histogram")
    if operation.plot == "bar" and any(
        column.inferred_type in NUMERIC_TYPES
        and (column.low_cardinality is None or column.unique_count > 20)
        for column in selected
    ):
        raise AnalysisSpecValidationError(
            "bar-column-not-categorical",
            "Bar plots require categorical or low-cardinality columns.",
            kind="clarification-required",
        )
    return ValidatedAnalysisSpec(
        spec=spec,
        resolved_method="descriptive",
        method_selection_reason="The goal requests supported descriptive summaries.",
        referenced_columns=tuple(operation.columns),
        dataset_delimiter=profile.delimiter,
        dataset_encoding=profile.encoding,
        runtime_guards=tuple(runtime_guards),
    )


def _validate_two_group(
    spec: AnalysisSpec,
    operation: TwoGroupComparisonOperation,
    columns: dict[str, DatasetColumnProfile],
    profile: DatasetProfile,
    preflight: ExactTwoGroupPreflight | None,
) -> ValidatedAnalysisSpec:
    outcome = _require_column(columns, operation.outcome_column)
    group = _require_column(columns, operation.group_column)
    _require_numeric(outcome, role="outcome")
    _require_non_degenerate_inferential_column(
        outcome,
        profile,
        role="outcome",
    )
    _reject_report_only_missing_values(spec, (outcome, group))
    if group.inferred_type in NUMERIC_TYPES and (
        group.low_cardinality is None or group.unique_count > 20
    ):
        raise AnalysisSpecValidationError(
            "group-column-not-categorical",
            "The group column must be categorical or low-cardinality.",
            kind="clarification-required",
        )
    if group.unique_count < 2:
        raise AnalysisSpecValidationError(
            "group-column-has-one-value",
            "The group column does not contain two observed values.",
            kind="blocked",
        )
    if _has_lossy_profile_values(profile):
        raise AnalysisSpecValidationError(
            "group-values-not-executable-from-profile",
            (
                "The bounded profile contains escaped or truncated display values, so "
                "group values cannot be bound to source values safely."
            ),
            kind="blocked",
        )
    known_values: set[str] = (
        set(group.low_cardinality.values) if group.low_cardinality else set()
    )
    if not set(operation.groups).issubset(known_values):
        raise AnalysisSpecValidationError(
            "group-values-not-observed",
            "Both requested group values must be present in the profile summary.",
            kind="clarification-required",
        )
    runtime_guards: list[str] = []
    if preflight is not None:
        if (
            preflight.outcome_column != operation.outcome_column
            or preflight.group_column != operation.group_column
            or not set(operation.groups).issubset(preflight.valid_counts)
        ):
            raise AnalysisSpecValidationError(
                "two-group-preflight-mismatch",
                "Exact two-group preflight evidence does not match the analysis operation.",
                kind="blocked",
            )
        if any(
            preflight.valid_counts[group_name] < MIN_GROUP_SAMPLE_SIZE
            for group_name in operation.groups
        ):
            raise AnalysisSpecValidationError(
                "two-group-sample-too-small",
                "Each selected group requires at least three valid observations.",
                kind="blocked",
            )
    else:
        if (
            _profile_is_complete(profile)
            and _profiled_non_missing_count(outcome, profile)
            < MIN_GROUP_SAMPLE_SIZE * 2
        ):
            raise AnalysisSpecValidationError(
                "two-group-sample-too-small",
                "The complete profile proves that both groups cannot have three valid observations.",
                kind="blocked",
            )
        runtime_guards.append("minimum-three-valid-observations-per-group")
    if spec.missing_value_policy == "report-only" and not _profile_is_complete(profile):
        runtime_guards.append("report-only-reject-missing-values")
    if not _profile_is_complete(profile):
        runtime_guards.append("non-degenerate-outcome")
    if operation.method == "auto":
        low_cardinality_numeric = outcome.unique_count <= 10
        if low_cardinality_numeric:
            resolved: ResolvedMethod = "mann-whitney-u"
            reason = (
                "Deterministic auto-selection chose Mann-Whitney U because the profiled "
                "outcome is low-cardinality; this rule is not a complete distributional "
                "diagnostic."
            )
        else:
            resolved = "welch-t-test"
            reason = (
                "Deterministic auto-selection chose Welch's t-test because the profiled "
                "outcome is numeric without the limited warning signals used by v1; this "
                "rule is not a complete normality or outlier diagnostic."
            )
    else:
        resolved = operation.method
        reason = f"The user-confirmed method is {operation.method}."
    if (
        resolved == "welch-t-test"
        and preflight is not None
        and any(
            not preflight.non_constant_groups[group_name]
            for group_name in operation.groups
        )
    ):
        raise AnalysisSpecValidationError(
            "welch-group-variance-zero",
            "Welch's t-test and Hedges' g require non-zero variance in both groups.",
            kind="blocked",
        )
    expected_effect = "hedges-g" if resolved == "welch-t-test" else "rank-biserial"
    if operation.method != "auto" and operation.effect_size != expected_effect:
        raise AnalysisSpecValidationError(
            "comparison-effect-size-mismatch",
            f"The resolved method {resolved} requires {expected_effect}.",
            kind="clarification-required",
        )
    normalized_spec = spec
    if operation.method == "auto" and operation.effect_size != expected_effect:
        normalized_spec = spec.model_copy(
            update={
                "operation": operation.model_copy(
                    update={"effect_size": expected_effect}
                )
            }
        )
    return ValidatedAnalysisSpec(
        spec=normalized_spec,
        resolved_method=resolved,
        method_selection_reason=reason,
        referenced_columns=(operation.outcome_column, operation.group_column),
        dataset_delimiter=profile.delimiter,
        dataset_encoding=profile.encoding,
        runtime_guards=tuple(runtime_guards),
    )


def _validate_correlation(
    spec: AnalysisSpec,
    operation: CorrelationOperation,
    columns: dict[str, DatasetColumnProfile],
    profile: DatasetProfile,
    preflight: ExactCorrelationPreflight | None,
) -> ValidatedAnalysisSpec:
    x_column = _require_column(columns, operation.x_column)
    y_column = _require_column(columns, operation.y_column)
    _require_numeric(x_column, role="x")
    _require_numeric(y_column, role="y")
    _require_non_degenerate_inferential_column(x_column, profile, role="x")
    _require_non_degenerate_inferential_column(y_column, profile, role="y")
    _reject_report_only_missing_values(spec, (x_column, y_column))
    runtime_guards: list[str] = []
    if preflight is not None:
        if (
            preflight.x_column != operation.x_column
            or preflight.y_column != operation.y_column
        ):
            raise AnalysisSpecValidationError(
                "correlation-preflight-mismatch",
                "Exact correlation preflight evidence does not match the analysis operation.",
                kind="blocked",
            )
        if preflight.valid_pair_count < MIN_PAIRED_SAMPLE_SIZE:
            raise AnalysisSpecValidationError(
                "correlation-sample-too-small",
                "Correlation requires at least three exact complete pairs.",
                kind="blocked",
            )
    else:
        valid_pair_upper_bound = min(
            _profiled_non_missing_count(x_column, profile),
            _profiled_non_missing_count(y_column, profile),
        )
        if _profile_is_complete(profile) and valid_pair_upper_bound < MIN_PAIRED_SAMPLE_SIZE:
            raise AnalysisSpecValidationError(
                "correlation-sample-too-small",
                "The complete profile proves that three complete pairs are impossible.",
                kind="blocked",
            )
        runtime_guards.append("minimum-three-complete-pairs")
    if spec.missing_value_policy == "report-only" and not _profile_is_complete(profile):
        runtime_guards.append("report-only-reject-missing-values")
    if not _profile_is_complete(profile):
        runtime_guards.append("non-degenerate-correlation-columns")
    if operation.method == "auto":
        if x_column.unique_count <= 10 or y_column.unique_count <= 10:
            resolved: ResolvedMethod = "spearman"
            reason = (
                "Deterministic auto-selection chose Spearman correlation because at least "
                "one profiled variable is low-cardinality; correlation does not establish "
                "causation."
            )
        else:
            resolved = "pearson"
            reason = (
                "Deterministic auto-selection chose Pearson correlation because both "
                "profiled variables are numeric with more than ten distinct values; this "
                "rule is not a complete linearity or outlier diagnostic, and correlation "
                "does not establish causation."
            )
    else:
        resolved = operation.method
        reason = (
            f"The user-confirmed method is {operation.method}; correlation does not "
            "establish causation."
        )
    return ValidatedAnalysisSpec(
        spec=spec,
        resolved_method=resolved,
        method_selection_reason=reason,
        referenced_columns=(operation.x_column, operation.y_column),
        dataset_delimiter=profile.delimiter,
        dataset_encoding=profile.encoding,
        runtime_guards=tuple(runtime_guards),
    )
