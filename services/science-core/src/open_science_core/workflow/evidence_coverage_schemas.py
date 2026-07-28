"""Public, read-only evidence-coverage projection for frozen literature workflows."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..schemas import ApiModel


class EvidenceCoverageSourceBreadthOut(ApiModel):
    frozen_source_count: int = Field(ge=0)
    sources_with_covered_evidence_count: int = Field(ge=0)
    sources_without_covered_evidence_count: int = Field(ge=0)
    verified_referenced_span_count: int = Field(ge=0)


class EvidenceCoverageFacetOut(ApiModel):
    column_id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=120)
    state: Literal["complete", "partial", "unverified", "missing"]
    source_count: int = Field(ge=0)
    covered_source_count: int = Field(ge=0)
    awaiting_confirmation_source_count: int = Field(ge=0)
    unverified_source_count: int = Field(ge=0)
    missing_source_count: int = Field(ge=0)


class EvidenceCoverageClaimCoverageOut(ApiModel):
    state: Literal["not-generated", "not-verified", "verified-frozen"]
    total_claim_count: int = Field(ge=0)
    evidence_linked_claim_count: int = Field(
        ge=0,
        description=(
            "Claims linked to at least one verified relationship in the current "
            "verified-frozen result; this is a structural count, not a scientific score."
        ),
    )
    supported_claim_count: int = Field(
        ge=0,
        description=(
            "Compatibility count derived only from the deterministic support status "
            "of a verified-frozen result; new clients should use evidenceLinkedClaimCount."
        ),
    )
    unresolved_question_count: int = Field(ge=0)


class EvidenceCoverageOut(ApiModel):
    schema_version: Literal["1"] = "1"
    workflow_id: str = Field(min_length=1, max_length=36)
    project_id: str = Field(min_length=1, max_length=36)
    plan_id: str | None = Field(default=None, min_length=1, max_length=36)
    plan_version: int | None = Field(default=None, ge=1)
    plan_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    state: Literal["not-ready", "available", "reviewed"]
    source_set_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_breadth: EvidenceCoverageSourceBreadthOut
    facets: list[EvidenceCoverageFacetOut]
    claim_coverage: EvidenceCoverageClaimCoverageOut
    contradiction_assessment: Literal["not-assessed"] = "not-assessed"
