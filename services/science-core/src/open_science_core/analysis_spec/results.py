from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, StringConstraints, field_validator, model_validator

from .schemas import (
    ColumnName,
    NonEmptyText,
    Sha256,
    SourceId,
    StrictAnalysisModel,
    canonical_model_sha256,
)

FiniteNumber: TypeAlias = Annotated[float, Field(strict=True, allow_inf_nan=False)]
NonNegativeCount: TypeAlias = Annotated[int, Field(strict=True, ge=0)]


class AnalysisSampleSummary(StrictAnalysisModel):
    total_rows: NonNegativeCount
    analyzed_rows: NonNegativeCount
    missing_rows: NonNegativeCount

    @model_validator(mode="after")
    def validate_counts(self) -> AnalysisSampleSummary:
        if self.analyzed_rows + self.missing_rows > self.total_rows:
            raise ValueError("analyzed and missing rows cannot exceed total rows")
        return self


class DescriptiveColumnResult(StrictAnalysisModel):
    column: ColumnName
    sample_size: NonNegativeCount
    missing_count: NonNegativeCount
    statistics: dict[str, int | float | str | None]

    @field_validator("statistics")
    @classmethod
    def validate_statistics(
        cls,
        value: dict[str, int | float | str | None],
    ) -> dict[str, int | float | str | None]:
        if not value or len(value) > 20:
            raise ValueError("descriptive statistics must contain 1 to 20 entries")
        if any(not key or len(key) > 100 for key in value):
            raise ValueError("descriptive statistic names must be bounded")
        return value


class DescriptiveAnalysisResult(StrictAnalysisModel):
    type: Literal["descriptive"]
    columns: Annotated[list[DescriptiveColumnResult], Field(min_length=1, max_length=100)]

    @field_validator("columns")
    @classmethod
    def require_unique_columns(
        cls,
        value: list[DescriptiveColumnResult],
    ) -> list[DescriptiveColumnResult]:
        columns = [item.column for item in value]
        if len(columns) != len(set(columns)):
            raise ValueError("descriptive result columns must be unique")
        return value


class TwoGroupComparisonResult(StrictAnalysisModel):
    type: Literal["two-group-comparison"]
    group_column: ColumnName
    outcome_column: ColumnName
    groups: tuple[NonEmptyText, NonEmptyText]
    sample_sizes: dict[str, NonNegativeCount]
    missing_counts: dict[str, NonNegativeCount]
    descriptive_statistics: dict[str, dict[str, int | float | None]]
    test_statistic: FiniteNumber
    p_value: Annotated[float, Field(strict=True, ge=0.0, le=1.0)]
    effect_size_name: Literal["hedges-g", "rank-biserial"]
    effect_size: FiniteNumber
    confidence_interval: tuple[FiniteNumber, FiniteNumber]

    @model_validator(mode="after")
    def validate_group_result(self) -> TwoGroupComparisonResult:
        if self.group_column == self.outcome_column:
            raise ValueError("result group and outcome columns must differ")
        if self.groups[0] == self.groups[1]:
            raise ValueError("result groups must be distinct")
        expected_groups = set(self.groups)
        if set(self.sample_sizes) != expected_groups:
            raise ValueError("sample sizes must cover the exact result groups")
        if set(self.missing_counts) != expected_groups:
            raise ValueError("missing counts must cover the exact result groups")
        if set(self.descriptive_statistics) != expected_groups:
            raise ValueError("descriptive statistics must cover the exact result groups")
        if self.confidence_interval[0] > self.confidence_interval[1]:
            raise ValueError("confidence interval lower bound cannot exceed upper bound")
        return self


class CorrelationAnalysisResult(StrictAnalysisModel):
    type: Literal["correlation"]
    x_column: ColumnName
    y_column: ColumnName
    sample_size: NonNegativeCount
    missing_pairs: NonNegativeCount
    correlation: Annotated[float, Field(strict=True, ge=-1.0, le=1.0)]
    p_value: Annotated[float, Field(strict=True, ge=0.0, le=1.0)]
    confidence_interval: tuple[
        Annotated[float, Field(strict=True, ge=-1.0, le=1.0)],
        Annotated[float, Field(strict=True, ge=-1.0, le=1.0)],
    ] | None

    @model_validator(mode="after")
    def validate_correlation_result(self) -> CorrelationAnalysisResult:
        if self.x_column == self.y_column:
            raise ValueError("correlation result columns must differ")
        if (
            self.confidence_interval is not None
            and self.confidence_interval[0] > self.confidence_interval[1]
        ):
            raise ValueError("confidence interval lower bound cannot exceed upper bound")
        return self


OperationResult: TypeAlias = Annotated[
    DescriptiveAnalysisResult | TwoGroupComparisonResult | CorrelationAnalysisResult,
    Field(discriminator="type"),
]
RequestedMethod = Literal[
    "descriptive",
    "auto",
    "welch-t-test",
    "mann-whitney-u",
    "pearson",
    "spearman",
]
ResolvedMethod = Literal[
    "descriptive",
    "welch-t-test",
    "mann-whitney-u",
    "pearson",
    "spearman",
]


class StructuredAnalysisResult(StrictAnalysisModel):
    schema_version: Literal["1"]
    objective: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=8_000),
    ]
    operation_type: Literal["descriptive", "two-group-comparison", "correlation"]
    dataset_source_id: SourceId
    dataset_content_hash: Sha256
    dataset_profile_hash: Sha256
    requested_method: RequestedMethod
    resolved_method: ResolvedMethod
    method_selection_reason: NonEmptyText
    sample_summary: AnalysisSampleSummary
    result: OperationResult
    warnings: Annotated[list[NonEmptyText], Field(max_length=100)]
    limitations: Annotated[list[NonEmptyText], Field(max_length=100)]

    @field_validator("warnings", "limitations")
    @classmethod
    def require_unique_notes(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("structured result notes must be unique")
        return value

    @model_validator(mode="after")
    def validate_method_and_result(self) -> StructuredAnalysisResult:
        if self.operation_type != self.result.type:
            raise ValueError("operation_type must match the structured result type")
        requested = self.requested_method
        resolved = self.resolved_method
        if self.result.type == "descriptive":
            if requested != "descriptive" or resolved != "descriptive":
                raise ValueError("descriptive results require descriptive methods")
            if any(
                item.sample_size + item.missing_count
                != self.sample_summary.total_rows
                for item in self.result.columns
            ):
                raise ValueError(
                    "descriptive column counts must equal the total row count"
                )
        elif self.result.type == "two-group-comparison":
            allowed = {"welch-t-test", "mann-whitney-u"}
            if resolved not in allowed or requested not in {"auto", *allowed}:
                raise ValueError("two-group results require a supported comparison method")
            if requested != "auto" and requested != resolved:
                raise ValueError("explicit comparison method must match the resolved method")
            expected_effect = (
                "hedges-g" if resolved == "welch-t-test" else "rank-biserial"
            )
            if self.result.effect_size_name != expected_effect:
                raise ValueError("resolved comparison method and effect size are incompatible")
            if sum(self.result.sample_sizes.values()) != self.sample_summary.analyzed_rows:
                raise ValueError(
                    "two-group sample sizes must equal the analyzed row count"
                )
            if sum(self.result.missing_counts.values()) > self.sample_summary.missing_rows:
                raise ValueError(
                    "two-group missing counts cannot exceed the missing row count"
                )
        else:
            allowed = {"pearson", "spearman"}
            if resolved not in allowed or requested not in {"auto", *allowed}:
                raise ValueError("correlation results require a supported correlation method")
            if requested != "auto" and requested != resolved:
                raise ValueError("explicit correlation method must match the resolved method")
            if (
                self.result.sample_size != self.sample_summary.analyzed_rows
                or self.result.missing_pairs != self.sample_summary.missing_rows
                or self.result.sample_size + self.result.missing_pairs
                != self.sample_summary.total_rows
            ):
                raise ValueError(
                    "correlation pair counts must exactly match the sample summary"
                )
        return self


def structured_analysis_result_sha256(result: StructuredAnalysisResult) -> str:
    return canonical_model_sha256(result)
