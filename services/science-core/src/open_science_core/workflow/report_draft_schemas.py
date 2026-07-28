from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..schemas import to_camel

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class _ReportDraftModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        from_attributes=True,
        populate_by_name=True,
        strict=True,
    )


class ReportDraftOut(_ReportDraftModel):
    id: str
    project_id: str
    workflow_id: str
    schema_version: Literal["1"]
    revision: Annotated[int, Field(strict=True, ge=1)]
    content_markdown: str
    content_sha256: Sha256
    base_workflow_sha256: Sha256
    base_result_sha256: Sha256
    base_evidence_sha256: Sha256
    status: Literal["draft", "needs-review", "reviewed"]
    created_at: datetime
    updated_at: datetime


class CreateReportDraftIn(_ReportDraftModel):
    schema_version: Literal["1"]


class SaveReportDraftIn(_ReportDraftModel):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_content_sha256: Sha256
    content_markdown: Annotated[str, Field(min_length=1, max_length=500_000)]


class ReportCitationRebaseIn(_ReportDraftModel):
    previous_evidence_id: Annotated[str, Field(min_length=1, max_length=36)]
    previous_quote_hash: Sha256
    current_evidence_id: Annotated[str, Field(min_length=1, max_length=36)]
    current_quote_hash: Sha256


class ReviewReportDraftIn(_ReportDraftModel):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_content_sha256: Sha256
    citation_rebases: Annotated[
        list[ReportCitationRebaseIn],
        Field(default_factory=list, max_length=500),
    ]


class ExportReportDraftIn(_ReportDraftModel):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_content_sha256: Sha256


class ReportDraftExportOut(_ReportDraftModel):
    draft_id: str
    project_id: str
    workflow_id: str
    revision: Annotated[int, Field(strict=True, ge=1)]
    content_markdown: str
    content_sha256: Sha256
    base_workflow_sha256: Sha256
    base_result_sha256: Sha256
    base_evidence_sha256: Sha256


__all__ = (
    "CreateReportDraftIn",
    "ExportReportDraftIn",
    "ReportCitationRebaseIn",
    "ReportDraftExportOut",
    "ReportDraftOut",
    "ReviewReportDraftIn",
    "SaveReportDraftIn",
)
