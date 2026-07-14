from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        from_attributes=True,
        populate_by_name=True,
    )


class HealthOut(ApiModel):
    status: str
    version: str
    database: str
    paper_qa: str
    model_gateway: str
    runtime: str


class ProjectCreate(ApiModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    research_domain: str | None = None


class ProjectOut(ApiModel):
    id: str
    title: str
    description: str
    project_path: str
    research_domain: str | None
    execution_mode: str
    created_at: datetime
    updated_at: datetime


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
    created_at: datetime


class QuestionIn(ApiModel):
    question: str = Field(min_length=2, max_length=8_000)
    model: str | None = None
    remote_data_approved: bool = False


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
    coordinate_space: str
    quote_hash: str
    extraction_method: str
    confidence: float
    verified: bool


class ClaimOut(ApiModel):
    id: str
    statement: str
    claim_type: str
    confidence: float
    review_status: str
    evidence: list[EvidenceOut]


class AnswerOut(ApiModel):
    id: str
    project_id: str
    question: str
    answer: str
    claims: list[ClaimOut]
    unresolved_questions: list[str]
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
