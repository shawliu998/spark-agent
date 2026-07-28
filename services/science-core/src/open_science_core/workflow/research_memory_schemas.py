from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..schemas import to_camel

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MemoryType = Literal[
    "user-decision",
    "assumption",
    "open-question",
    "failure-lesson",
    "operational-fact",
]
MemoryStatus = Literal[
    "candidate",
    "committed",
    "rejected",
    "superseded",
    "invalidated",
]
MemoryAction = Literal["accept", "reject", "invalidate"]
MemoryReferenceType = Literal[
    "step-observation",
    "user-response",
    "source",
    "evidence",
    "artifact",
]
MemoryContextReasonCode = Literal[
    "selected-in-latest-snapshot",
    "eligible-for-future-snapshot",
    "bounded-context-excluded",
    "candidate-excluded",
    "rejected-excluded",
    "superseded-excluded",
    "invalidated-excluded",
    "source-missing",
    "source-not-ready",
    "source-stale",
    "evidence-missing",
    "evidence-invalid",
]


class _MemoryModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        from_attributes=True,
        populate_by_name=True,
        strict=True,
    )


class ResearchMemoryReferenceOut(_MemoryModel):
    id: str
    sha256: Sha256
    type: MemoryReferenceType


class ResearchMemoryOut(_MemoryModel):
    id: str
    project_id: str
    scope_workflow_id: str | None
    subject_key: str
    revision: int
    previous_id: str | None
    schema_version: Literal["1"]
    type: MemoryType
    content_json: dict[str, Any]
    source_refs: list[ResearchMemoryReferenceOut]
    artifact_refs: list[ResearchMemoryReferenceOut]
    invalidation_rule: str | None
    status: MemoryStatus
    created_by: str
    memory_sha256: Sha256
    created_at: datetime
    updated_at: datetime


class MemoryCandidateResolveIn(_MemoryModel):
    decision: Literal["accept", "reject"]
    expected_content_hash: Sha256
    expected_status: Literal["candidate"]
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_subject_head_id: str
    expected_subject_head_revision: Annotated[int, Field(strict=True, ge=1)]


class MemoryInvalidateIn(_MemoryModel):
    expected_content_hash: Sha256
    expected_status: Literal["committed"]
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_subject_head_id: str
    expected_subject_head_revision: Annotated[int, Field(strict=True, ge=1)]


class CreateEvidenceMemoryCandidateIn(_MemoryModel):
    evidence_id: str
    expected_source_content_hash: Sha256
    expected_quote_hash: Sha256


class VerifiedEpisodeOut(_MemoryModel):
    episode_id: str
    episode_sha256: Sha256
    action: Literal["remembered-evidence-action-v1"]
    schema_version: Literal["1"]


class CreateEvidenceMemoryCandidateOut(_MemoryModel):
    outcome: Literal[
        "candidate-created",
        "candidate-reopened",
        "already-remembered",
    ]
    memory: ResearchMemoryOut
    verified_episode: VerifiedEpisodeOut


class ResearchMemoryContextOut(_MemoryModel):
    state: Literal["selected", "eligible", "excluded"]
    reason_code: MemoryContextReasonCode
    snapshot_id: str | None
    snapshot_sha256: Sha256 | None

    @model_validator(mode="after")
    def validate_state_reason(self) -> ResearchMemoryContextOut:
        if self.state == "selected" and self.reason_code != (
            "selected-in-latest-snapshot"
        ):
            raise ValueError("selected context requires the selected reason")
        if self.state == "selected" and self.snapshot_id is None:
            raise ValueError("selected context requires a snapshot identity")
        if self.state == "eligible" and self.reason_code != (
            "eligible-for-future-snapshot"
        ):
            raise ValueError("eligible context requires the future-snapshot reason")
        if self.state == "excluded" and self.reason_code in {
            "selected-in-latest-snapshot",
            "eligible-for-future-snapshot",
        }:
            raise ValueError("excluded context requires an exclusion reason")
        if (self.snapshot_id is None) != (self.snapshot_sha256 is None):
            raise ValueError("context snapshot identity must be complete")
        return self


class ResearchMemoryWorkspaceItemOut(ResearchMemoryOut):
    subject_head_id: str
    subject_head_revision: Annotated[int, Field(strict=True, ge=1)]
    available_actions: list[MemoryAction]
    context: ResearchMemoryContextOut

    @model_validator(mode="after")
    def validate_actions(self) -> ResearchMemoryWorkspaceItemOut:
        expected: dict[MemoryStatus, list[MemoryAction]] = {
            "candidate": ["accept", "reject"],
            "committed": ["invalidate"],
            "rejected": [],
            "superseded": [],
            "invalidated": [],
        }
        if self.available_actions != expected[self.status]:
            raise ValueError("availableActions do not match the memory status")
        return self


class ResearchMemoryWorkspaceCounts(_MemoryModel):
    candidate: Annotated[int, Field(strict=True, ge=0)]
    committed: Annotated[int, Field(strict=True, ge=0)]
    rejected: Annotated[int, Field(strict=True, ge=0)]
    superseded: Annotated[int, Field(strict=True, ge=0)]
    invalidated: Annotated[int, Field(strict=True, ge=0)]


class ResearchMemoryWorkspaceOut(_MemoryModel):
    schema_version: Literal["1"]
    project_id: str
    workflow_id: str
    latest_context_snapshot_id: str | None
    latest_context_snapshot_sha256: Sha256 | None
    counts: ResearchMemoryWorkspaceCounts
    items: list[ResearchMemoryWorkspaceItemOut]
    workspace_sha256: Sha256


__all__ = (
    "CreateEvidenceMemoryCandidateIn",
    "CreateEvidenceMemoryCandidateOut",
    "MemoryCandidateResolveIn",
    "MemoryInvalidateIn",
    "ResearchMemoryContextOut",
    "ResearchMemoryOut",
    "ResearchMemoryReferenceOut",
    "ResearchMemoryWorkspaceCounts",
    "ResearchMemoryWorkspaceItemOut",
    "ResearchMemoryWorkspaceOut",
    "VerifiedEpisodeOut",
)
