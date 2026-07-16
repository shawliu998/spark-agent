from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from ..schemas import to_camel

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]
ColumnName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000),
]
SourceId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=36),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

DescriptiveStatistic = Literal[
    "count",
    "missing",
    "mean",
    "std",
    "median",
    "min",
    "max",
    "q1",
    "q3",
    "iqr",
    "unique",
    "frequency",
]
ScientificClarificationType = Literal[
    "outcome-column",
    "group-column",
    "group-values",
    "x-column",
    "y-column",
    "analysis-objective",
    "method-confirmation",
    "independence-assumption",
    "missing-value-policy",
]


class StrictAnalysisModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


def canonical_model_json_bytes(value: StrictAnalysisModel) -> bytes:
    payload = value.model_dump(mode="json", by_alias=True)
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_model_sha256(value: StrictAnalysisModel) -> str:
    return hashlib.sha256(canonical_model_json_bytes(value)).hexdigest()


class DescriptiveOperation(StrictAnalysisModel):
    type: Literal["descriptive"]
    columns: Annotated[list[ColumnName], Field(min_length=1, max_length=100)]
    statistics: Annotated[
        list[DescriptiveStatistic],
        Field(min_length=1, max_length=12),
    ]
    plot: Literal["none", "histogram", "bar"]

    @field_validator("columns", "statistics")
    @classmethod
    def require_unique_items(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("descriptive operation items must be unique")
        return value


class TwoGroupComparisonOperation(StrictAnalysisModel):
    type: Literal["two-group-comparison"]
    outcome_column: ColumnName
    group_column: ColumnName
    groups: tuple[ColumnName, ColumnName]
    method: Literal["auto", "welch-t-test", "mann-whitney-u"]
    effect_size: Literal["hedges-g", "rank-biserial"]
    check_assumptions: StrictBool
    plot: Literal["boxplot", "violin", "none"]

    @model_validator(mode="after")
    def validate_columns_groups_and_method(self) -> TwoGroupComparisonOperation:
        if self.outcome_column == self.group_column:
            raise ValueError("outcome and group columns must be different")
        if self.groups[0] == self.groups[1]:
            raise ValueError("two-group comparison requires two distinct group values")
        if self.method == "welch-t-test" and self.effect_size != "hedges-g":
            raise ValueError("Welch t-test requires Hedges' g")
        if self.method == "mann-whitney-u" and self.effect_size != "rank-biserial":
            raise ValueError("Mann-Whitney U requires rank-biserial effect size")
        return self


class CorrelationOperation(StrictAnalysisModel):
    type: Literal["correlation"]
    x_column: ColumnName
    y_column: ColumnName
    method: Literal["auto", "pearson", "spearman"]
    confidence_interval: StrictBool
    plot: Literal["scatter", "none"]

    @model_validator(mode="after")
    def validate_distinct_columns(self) -> CorrelationOperation:
        if self.x_column == self.y_column:
            raise ValueError("correlation columns must be different")
        return self


AnalysisOperation: TypeAlias = Annotated[
    DescriptiveOperation | TwoGroupComparisonOperation | CorrelationOperation,
    Field(discriminator="type"),
]


class AnalysisSpec(StrictAnalysisModel):
    schema_version: Literal["1"]
    objective: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=8_000),
    ]
    dataset_source_id: SourceId
    dataset_content_hash: Sha256
    dataset_profile_hash: Sha256
    operation: AnalysisOperation
    missing_value_policy: Literal["drop-per-operation", "report-only"]
    confidence_level: Annotated[float, Field(strict=True, ge=0.80, le=0.99)]
    random_seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
    assumptions: Annotated[list[NonEmptyText], Field(max_length=100)]
    limitations: Annotated[list[NonEmptyText], Field(max_length=100)]

    @field_validator("assumptions", "limitations")
    @classmethod
    def require_unique_notes(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("analysis notes must be unique")
        return value


class ScientificClarificationOption(StrictAnalysisModel):
    value: NonEmptyText
    label: NonEmptyText
    description: NonEmptyText | None = None


class ScientificClarification(StrictAnalysisModel):
    type: ScientificClarificationType
    question: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000),
    ]
    options: Annotated[
        list[ScientificClarificationOption],
        Field(default_factory=list, max_length=100),
    ]

    @field_validator("options")
    @classmethod
    def require_unique_option_values(
        cls,
        value: list[ScientificClarificationOption],
    ) -> list[ScientificClarificationOption]:
        option_values = [option.value for option in value]
        if len(option_values) != len(set(option_values)):
            raise ValueError("scientific clarification option values must be unique")
        return value


class ClarificationProposal(StrictAnalysisModel):
    reason: NonEmptyText
    requests: Annotated[
        list[ScientificClarification],
        Field(min_length=1, max_length=20),
    ]

    @field_validator("requests")
    @classmethod
    def require_unique_request_types(
        cls,
        value: list[ScientificClarification],
    ) -> list[ScientificClarification]:
        request_types = [request.type for request in value]
        if len(request_types) != len(set(request_types)):
            raise ValueError("scientific clarification request types must be unique")
        return value


class UnsupportedAnalysis(StrictAnalysisModel):
    capability: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=100,
            pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        ),
    ]
    explanation: NonEmptyText
    supported_alternatives: Annotated[
        list[Literal["descriptive", "two-group-comparison", "correlation"]],
        Field(max_length=3),
    ]

    @field_validator("supported_alternatives")
    @classmethod
    def require_unique_alternatives(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("supported alternatives must be unique")
        return value


class CompiledAnalysis(StrictAnalysisModel):
    compiler_version: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=100,
            pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
        ),
    ]
    spec_sha256: Sha256
    code: Annotated[str, StringConstraints(min_length=1, max_length=200_000)]
    code_sha256: Sha256
    expected_outputs: Annotated[
        list[NonEmptyText],
        Field(min_length=1, max_length=20),
    ]
    runtime_policy_id: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=100,
            pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
        ),
    ]

    @field_validator("expected_outputs")
    @classmethod
    def require_unique_outputs(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("compiled analysis outputs must be unique")
        return value

    @model_validator(mode="after")
    def validate_code_hash(self) -> CompiledAnalysis:
        if hashlib.sha256(self.code.encode("utf-8")).hexdigest() != self.code_sha256:
            raise ValueError("code_sha256 must hash the exact UTF-8 code")
        return self


def analysis_spec_sha256(spec: AnalysisSpec) -> str:
    return canonical_model_sha256(spec)
