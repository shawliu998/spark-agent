from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from ...analysis_spec.schemas import AnalysisSpec, ScientificClarification
from ...schemas import to_camel

AgentAction = Literal[
    "continue",
    "request-clarification",
    "revise-analysis-spec",
    "retry-step",
    "complete",
    "stop",
]
ObservationType = Literal[
    "pre-plan",
    "step-output",
    "analysis-execution",
    "review",
]
ObservationStatus = Literal["succeeded", "failed", "blocked", "needs-review"]
FailureCategory = Literal[
    "none",
    "input",
    "method",
    "runtime",
    "artifact",
    "review",
    "unsupported",
    "unknown",
]
SourceType = Literal[
    "dataset-profile",
    "analysis-spec",
    "preflight",
    "run",
    "structured-result",
    "artifact",
    "review",
    "workflow",
]
AnswerType = Literal[
    "single-choice",
    "multi-choice",
    "column-selection",
    "method-confirmation",
    "assumption-confirmation",
    "boolean",
    "text",
]

Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=36),
]
StepKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][a-z0-9-]*$",
    ),
]
Code = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
]
NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
JsonScalar: TypeAlias = str | int | float | bool
JsonValue: TypeAlias = JsonScalar | list[Any] | dict[str, Any]


class StrictAgentLoopModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


def _canonical_sha256(value: StrictAgentLoopModel) -> str:
    canonical = json.dumps(
        value.model_dump(mode="json", by_alias=True),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class ObservationFact(StrictAgentLoopModel):
    code: Code
    statement: NonEmptyText
    value: JsonValue | None = None
    source_type: SourceType
    source_id: Identifier


class ObservationWarning(StrictAgentLoopModel):
    code: Code
    message: NonEmptyText
    severity: Literal["info", "warning", "error"]
    source_id: Identifier | None = None


class UnresolvedQuestion(StrictAgentLoopModel):
    code: Code
    question: NonEmptyText
    answer_type: AnswerType


class StepObservation(StrictAgentLoopModel):
    schema_version: Literal["1"]
    workflow_id: Identifier
    plan_id: Identifier | None = None
    task_id: Identifier | None = None
    source_job_id: Identifier
    run_id: Identifier | None = None
    review_id: Identifier | None = None
    observation_type: ObservationType
    step_key: StepKey
    attempt: Annotated[int, Field(strict=True, ge=1)]
    status: ObservationStatus
    facts: Annotated[list[ObservationFact], Field(min_length=1, max_length=500)]
    warnings: Annotated[list[ObservationWarning], Field(max_length=200)] = Field(
        default_factory=list[ObservationWarning]
    )
    unresolved_questions: Annotated[
        list[UnresolvedQuestion], Field(max_length=100)
    ] = Field(default_factory=list[UnresolvedQuestion])
    artifact_ids: Annotated[list[Identifier], Field(max_length=500)] = Field(
        default_factory=list[Identifier]
    )
    failure_category: FailureCategory
    recommended_actions: Annotated[list[AgentAction], Field(min_length=1, max_length=6)]

    @field_validator("facts", "warnings", "unresolved_questions")
    @classmethod
    def require_unique_codes(cls, value: list[Any]) -> list[Any]:
        codes = [item.code for item in value]
        if len(codes) != len(set(codes)):
            raise ValueError("observation item codes must be unique within each section")
        return value

    @field_validator("artifact_ids", "recommended_actions")
    @classmethod
    def require_unique_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("observation list values must be unique")
        return value

    @model_validator(mode="after")
    def validate_scope_and_failure(self) -> StepObservation:
        if self.task_id is None and self.observation_type not in {"pre-plan", "review"}:
            raise ValueError("only pre-plan and review observations may omit task_id")
        if self.plan_id is None and self.observation_type != "pre-plan":
            raise ValueError("only pre-plan observations may omit plan_id")
        successful = self.status in {"succeeded", "needs-review"}
        if successful != (self.failure_category == "none"):
            raise ValueError("observation status and failure_category are inconsistent")
        return self


class AnalysisSpecDiff(StrictAgentLoopModel):
    changed_fields: Annotated[list[NonEmptyText], Field(min_length=1, max_length=100)]
    previous_values: dict[str, Any]
    proposed_values: dict[str, Any]
    reason: NonEmptyText

    @model_validator(mode="after")
    def validate_changed_fields(self) -> AnalysisSpecDiff:
        if len(self.changed_fields) != len(set(self.changed_fields)):
            raise ValueError("analysis spec diff fields must be unique")
        expected = set(self.changed_fields)
        if set(self.previous_values) != expected or set(self.proposed_values) != expected:
            raise ValueError("analysis spec diff values must exactly match changed_fields")
        if all(
            self.previous_values[field] == self.proposed_values[field]
            for field in self.changed_fields
        ):
            raise ValueError("analysis spec diff must contain at least one changed value")
        return self


class AgentDecision(StrictAgentLoopModel):
    schema_version: Literal["1"]
    action: AgentAction
    reason_code: Code
    reason: NonEmptyText
    target_step_key: StepKey | None = None
    clarification_requests: Annotated[
        list[ScientificClarification], Field(max_length=20)
    ] = Field(default_factory=list[ScientificClarification])
    proposed_analysis_spec: AnalysisSpec | None = None
    analysis_spec_diff: AnalysisSpecDiff | None = None
    requires_user_confirmation: StrictBool

    @model_validator(mode="after")
    def validate_action_shape(self) -> AgentDecision:
        if self.action in {"continue", "retry-step"}:
            if self.target_step_key is None:
                raise ValueError(f"{self.action} requires target_step_key")
            if self.clarification_requests or self.proposed_analysis_spec is not None:
                raise ValueError(f"{self.action} cannot request clarification or revise a spec")
            if self.analysis_spec_diff is not None or self.requires_user_confirmation:
                raise ValueError(f"{self.action} must be an automatic bounded action")
        elif self.action == "request-clarification":
            if not self.clarification_requests:
                raise ValueError("request-clarification requires at least one request")
            if self.target_step_key is not None or self.proposed_analysis_spec is not None:
                raise ValueError("request-clarification cannot target or revise a step")
            if self.analysis_spec_diff is not None or self.requires_user_confirmation:
                raise ValueError(
                    "request-clarification must automatically create its interaction"
                )
        elif self.action == "revise-analysis-spec":
            if self.proposed_analysis_spec is None or self.analysis_spec_diff is None:
                raise ValueError("revise-analysis-spec requires a proposed spec and diff")
            if self.target_step_key is not None or self.clarification_requests:
                raise ValueError("revise-analysis-spec cannot target a step or ask questions")
            if not self.requires_user_confirmation:
                raise ValueError("revise-analysis-spec requires user confirmation")
        else:
            if (
                self.target_step_key is not None
                or self.clarification_requests
                or self.proposed_analysis_spec is not None
                or self.analysis_spec_diff is not None
                or self.requires_user_confirmation
            ):
                raise ValueError(f"{self.action} cannot include pending work")
        return self


def step_observation_sha256(value: StepObservation) -> str:
    return _canonical_sha256(value)


def agent_decision_sha256(value: AgentDecision) -> str:
    return _canonical_sha256(value)
