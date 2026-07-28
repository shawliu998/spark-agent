from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from .config import normalize_model_identifier


def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        from_attributes=True,
        populate_by_name=True,
    )


class ModelGatewayDestinationOut(ApiModel):
    provider: Literal["openai-compatible"]
    endpoint_host: str
    endpoint_identity: str
    model: str


class HealthOut(ApiModel):
    status: str
    version: str
    database: str
    paper_qa: str
    model_gateway: str
    model_destination: ModelGatewayDestinationOut | None
    runtime: str


class ProjectCreate(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        from_attributes=True,
        populate_by_name=True,
        extra="forbid",
    )

    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    research_domain: str | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(not character.isprintable() for character in normalized):
            raise ValueError("title must be printable and not blank")
        return normalized


class ProjectRename(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        from_attributes=True,
        populate_by_name=True,
        extra="forbid",
    )

    title: str = Field(min_length=1, max_length=300)
    expected_row_version: StrictInt = Field(ge=1)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(not character.isprintable() for character in normalized):
            raise ValueError("title must be printable and not blank")
        return normalized


class ProjectStateMutation(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        from_attributes=True,
        populate_by_name=True,
        extra="forbid",
    )

    expected_row_version: StrictInt = Field(ge=1)


class ProjectOut(ApiModel):
    id: str
    title: str
    description: str
    project_path: str
    research_domain: str | None
    execution_mode: str
    row_version: int
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DiscoverySourceLineageOut(ApiModel):
    schema_version: Literal["1"]
    workflow_id: str
    candidate_id: str
    candidate_sha256: str
    occurrence_invocation_id: str
    query_id: str
    provider: Literal["arxiv", "crossref", "openalex", "pubmed", "csl-json-file"]
    raw_item_sha256: str
    source_content_hash: str


class SourceOut(ApiModel):
    id: str
    project_id: str
    title: str
    source_kind: str
    authors: list[str]
    doi: str | None
    arxiv_id: str | None
    local_path: str
    publication_date: str | None
    ingestion_status: str
    content_hash: str
    page_count: int | None
    page_manifest_hash: str | None = None
    discovery_lineage: DiscoverySourceLineageOut | None = None
    created_at: datetime


class ScreeningDecisionUpsert(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    decision: Literal["include", "exclude"]
    reason: str | None = Field(default=None, max_length=2_000)
    criteria_version: str = Field(default="screening-v1", min_length=1, max_length=100)
    expected_version: StrictInt = Field(ge=0)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("criteria_version")
    @classmethod
    def validate_criteria_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(not character.isprintable() for character in normalized):
            raise ValueError("criteriaVersion must be printable and not blank")
        return normalized


class ScreeningDecisionOut(ApiModel):
    id: str
    project_id: str
    source_id: str
    decision: Literal["include", "exclude"]
    reason: str | None
    criteria_version: str
    row_version: int
    created_at: datetime
    updated_at: datetime


class CandidateTriageDecisionUpsert(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    decision: Literal["keep", "reject", "uncertain"]
    reason: str | None = Field(default=None, max_length=2_000)
    criteria_version: str = Field(
        default="candidate-triage-v1",
        min_length=1,
        max_length=100,
    )
    expected_version: StrictInt = Field(ge=0)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("criteria_version")
    @classmethod
    def validate_criteria_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(not character.isprintable() for character in normalized):
            raise ValueError("criteriaVersion must be printable and not blank")
        return normalized


class CandidateTriageDecisionOut(ApiModel):
    id: str
    project_id: str
    candidate_id: str
    decision: Literal["keep", "reject", "uncertain"]
    reason: str | None
    criteria_version: str
    evidence_status: Literal["not-evidence"] = "not-evidence"
    row_version: int
    created_at: datetime
    updated_at: datetime


class EvidenceDirectionJudgmentUpsert(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    direction: Literal["supporting", "mixed", "insufficient"]
    expected_version: StrictInt = Field(ge=0)


class EvidenceDirectionJudgmentOut(ApiModel):
    id: str
    project_id: str
    answer_id: str
    source_id: str
    direction: Literal["supporting", "mixed", "insufficient"]
    row_version: int
    created_at: datetime
    updated_at: datetime


class ExtractionColumnCreate(ApiModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    instructions: str | None = Field(default=None, max_length=2_000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ExtractionColumnOut(ApiModel):
    id: str
    project_id: str
    name: str
    instructions: str | None
    order_index: int
    row_version: int
    created_at: datetime
    updated_at: datetime


class ExtractionCellUpsert(ApiModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    value: str = Field(min_length=1, max_length=20_000)
    review_status: Literal["unreviewed", "confirmed"] = "unreviewed"
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    expected_version: StrictInt = Field(ge=0)

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("evidenceIds must not contain blank values")
        if len(set(value)) != len(value):
            raise ValueError("evidenceIds must be unique")
        return value


class ExtractionCellDelete(ApiModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    expected_version: StrictInt = Field(ge=1)


class ExtractionCellOut(ApiModel):
    id: str
    project_id: str
    source_id: str
    column_id: str
    value: str
    review_status: Literal["unreviewed", "confirmed"]
    evidence_ids: list[str]
    row_version: int
    created_at: datetime
    updated_at: datetime


class ExtractionMatrixOut(ApiModel):
    columns: list[ExtractionColumnOut]
    cells: list[ExtractionCellOut]


class QuestionIn(ApiModel):
    question: str = Field(min_length=2, max_length=8_000)
    model: str | None = None
    remote_data_approved: StrictBool = False

    @field_validator("model")
    @classmethod
    def validate_model_override(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            normalized = normalize_model_identifier(value)
        except ValueError as error:
            raise ValueError(str(error)) from None
        if normalized is None:
            raise ValueError("model override must not be blank")
        return normalized


class BoundingBoxOut(ApiModel):
    x0: float
    y0: float
    x1: float
    y1: float


class EvidenceOut(ApiModel):
    id: str
    source_id: str
    page_index: int
    page_label: str | None
    text: str
    bbox: BoundingBoxOut | None
    coordinate_space: Literal["normalized-rotated-top-left-v1"]
    quote_hash: str
    extraction_method: str
    confidence: float
    verified: bool


class ExactEvidenceSpanCreate(ApiModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    page_index: StrictInt = Field(ge=0)
    quote_text: str = Field(min_length=12, max_length=20_000)
    expected_source_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_page_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("quote_text")
    @classmethod
    def normalize_quote(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 12:
            raise ValueError("quoteText must contain at least 12 non-whitespace characters")
        return normalized


class ClaimOut(ApiModel):
    id: str
    statement: str
    claim_type: Literal["answer", "finding", "limitation", "contradiction"]
    confidence: float
    review_status: Literal["unreviewed", "verified", "rejected"]
    evidence: list[EvidenceOut]


class AnswerOut(ApiModel):
    id: str
    project_id: str
    question: str
    answer: str
    claims: list[ClaimOut]
    unresolved_questions: list[str]
    generator: str
    model: str | None
    prompt_version: str | None
    metadata: dict[str, Any]
    created_at: datetime


class AnalysisIntentCreate(ApiModel):
    model_config = ConfigDict(extra="forbid")

    dataset_source_id: str = Field(min_length=1, max_length=36)
    objective: str = Field(min_length=1, max_length=8_000)
    code: str = Field(min_length=1, max_length=100_000)

    @field_validator("objective", "code")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class AnalysisDecisionIn(ApiModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]


class AnalysisIntentOut(ApiModel):
    id: str
    task_id: str
    project_id: str
    dataset_source_id: str
    objective: str
    code: str
    payload_sha256: str
    risk_level: Literal["high"]
    affected_resources: list[str]
    status: str
    decision: str | None
    created_at: datetime
    updated_at: datetime


class AnalysisArtifactOut(ApiModel):
    id: str
    artifact_type: str
    path: str
    mime_type: str
    content_hash: str
    size_bytes: int
    created_at: datetime


class AnalysisRunOut(ApiModel):
    id: str
    intent_id: str
    task_id: str
    project_id: str
    dataset_source_id: str
    objective: str
    code: str
    payload_sha256: str
    status: str
    environment_hash: str | None
    input_artifacts: list[str]
    output_artifacts: list[str]
    stdout: str
    stderr: str
    log: str
    logs: str
    error: str | None
    artifacts: list[AnalysisArtifactOut]
    created_at: datetime
    finished_at: datetime | None
