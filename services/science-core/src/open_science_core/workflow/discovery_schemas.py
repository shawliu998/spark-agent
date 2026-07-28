from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

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

RemoteDiscoveryProvider = Literal["arxiv", "crossref", "openalex", "pubmed"]
DiscoveryProvider = RemoteDiscoveryProvider
DiscoveryCandidateProvider = Literal[
    "arxiv", "crossref", "openalex", "pubmed", "csl-json-file"
]
DiscoverySort = Literal["relevance", "newest"]
DiscoveryPolicyStopReason = Literal[
    "discovery-candidate-target-reached",
    "discovery-no-novelty-limit",
    "discovery-attempt-budget-reached",
]

QueryId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=36,
        pattern=r"^query-[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
]
QueryText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=500),
]
QuestionText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=3, max_length=2_000),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_PROVIDER_ORDER: tuple[DiscoveryProvider, ...] = (
    "arxiv",
    "crossref",
    "openalex",
    "pubmed",
)


class StrictDiscoveryModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


def discovery_sha256(value: StrictDiscoveryModel) -> str:
    canonical = json.dumps(
        value.model_dump(mode="json", by_alias=True),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class DiscoveryQuery(StrictDiscoveryModel):
    id: QueryId
    query: QueryText
    providers: Annotated[list[DiscoveryProvider], Field(min_length=1, max_length=4)]
    year_from: Annotated[int | None, Field(strict=True, ge=1800, le=2100)] = None
    year_to: Annotated[int | None, Field(strict=True, ge=1800, le=2100)] = None
    sort: DiscoverySort = "relevance"
    max_results_per_provider: Annotated[int, Field(strict=True, ge=1, le=50)] = 20

    @property
    def derived_maximum_results(self) -> int:
        """Return the exact approved upper bound for this multi-provider query."""

        return self.max_results_per_provider * len(self.providers)

    @field_validator("providers")
    @classmethod
    def require_canonical_providers(cls, value: list[DiscoveryProvider]) -> list[DiscoveryProvider]:
        if len(value) != len(set(value)):
            raise ValueError("discovery providers must be unique")
        expected = sorted(value, key=_PROVIDER_ORDER.index)
        if value != expected:
            raise ValueError("discovery providers must use canonical order")
        return value

    @model_validator(mode="after")
    def validate_year_range(self) -> DiscoveryQuery:
        if (
            self.year_from is not None
            and self.year_to is not None
            and self.year_from > self.year_to
        ):
            raise ValueError("yearFrom must not exceed yearTo")
        return self


class DiscoveryStopPolicy(StrictDiscoveryModel):
    min_unique_candidates: Annotated[int, Field(strict=True, ge=1, le=200)] = 20
    max_attempts: Annotated[int, Field(strict=True, ge=1, le=8)] = 4
    max_consecutive_no_novelty: Annotated[int, Field(strict=True, ge=1, le=3)] = 2


class DiscoverySpec(StrictDiscoveryModel):
    schema_version: Literal["1"]
    question: QuestionText
    queries: Annotated[list[DiscoveryQuery], Field(min_length=1, max_length=8)]
    stop_policy: DiscoveryStopPolicy
    download_open_access_pdfs: StrictBool = False
    max_pdf_downloads: Annotated[int, Field(strict=True, ge=0, le=20)] = 0

    @field_validator("queries")
    @classmethod
    def require_unique_queries(cls, value: list[DiscoveryQuery]) -> list[DiscoveryQuery]:
        ids = [item.id for item in value]
        texts = [item.query.casefold() for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("discovery query IDs must be unique")
        if len(texts) != len(set(texts)):
            raise ValueError("discovery query texts must be unique")
        return value

    @model_validator(mode="after")
    def validate_budgets(self) -> DiscoverySpec:
        approved_operations = sum(len(query.providers) for query in self.queries)
        if self.stop_policy.max_attempts > approved_operations:
            raise ValueError("maxAttempts cannot exceed approved query-provider operations")
        if self.download_open_access_pdfs != (self.max_pdf_downloads > 0):
            raise ValueError("PDF download permission and maxPdfDownloads must be enabled together")
        return self


class DiscoveryCandidate(StrictDiscoveryModel):
    provider: DiscoveryCandidateProvider
    provider_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ]
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000)]
    authors: Annotated[list[str], Field(max_length=200)] = Field(default_factory=list)
    abstract: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=20_000)] = (
        None
    )
    publication_date: Annotated[
        str | None, StringConstraints(strip_whitespace=True, max_length=32)
    ] = None
    doi: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=255)] = None
    arxiv_id: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=100)] = None
    pmid: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=50)] = None
    landing_url: Annotated[
        str | None, StringConstraints(strip_whitespace=True, max_length=2_000)
    ] = None
    open_access_pdf_url: Annotated[
        str | None, StringConstraints(strip_whitespace=True, max_length=2_000)
    ] = None

    @field_validator("authors")
    @classmethod
    def normalize_authors(cls, value: list[str]) -> list[str]:
        normalized = [author.strip() for author in value]
        if any(not author or len(author) > 300 for author in normalized):
            raise ValueError("candidate authors must be non-empty and at most 300 characters")
        if len(normalized) != len(set(normalized)):
            raise ValueError("candidate authors must be unique")
        return normalized

    @model_validator(mode="after")
    def require_scholarly_identity(self) -> DiscoveryCandidate:
        if not any((self.doi, self.arxiv_id, self.pmid, self.provider_id)):
            raise ValueError("candidate must have a provider or scholarly identifier")
        return self


def discovery_candidate_sha256(candidate: DiscoveryCandidate) -> Sha256:
    return discovery_sha256(candidate)


DISCOVERY_TERMINAL_RESULT_SCHEMA_VERSION = "discovery-terminal-result-v1"


class DiscoveryTerminalOccurrenceRef(StrictDiscoveryModel):
    rank: Annotated[int, Field(strict=True, ge=1)]
    candidate_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=36)
    ]
    candidate_sha256: Sha256
    raw_item_sha256: Sha256


class DiscoveryTerminalResultProjection(StrictDiscoveryModel):
    """Canonical durable identity for one successful Discovery result."""

    schema_version: Literal["discovery-terminal-result-v1"]
    invocation_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=36)
    ]
    job_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=36)]
    operation_key: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ]
    attempt: Annotated[int, Field(strict=True, ge=1)]
    output_sha256: Sha256
    returned_count: Annotated[int, Field(strict=True, ge=0)]
    novel_candidate_count: Annotated[int, Field(strict=True, ge=0)]
    duplicate_count: Annotated[int, Field(strict=True, ge=0)]
    occurrence_count: Annotated[int, Field(strict=True, ge=0)]
    occurrences: list[DiscoveryTerminalOccurrenceRef]
    candidate_set_sha256: Sha256

    @model_validator(mode="after")
    def validate_terminal_result(self) -> DiscoveryTerminalResultProjection:
        if self.returned_count != (self.novel_candidate_count + self.duplicate_count):
            raise ValueError("returnedCount must equal novelCandidateCount + duplicateCount")
        if self.occurrence_count != len(self.occurrences):
            raise ValueError("occurrenceCount must match the complete occurrence projection")
        if self.occurrence_count > self.returned_count:
            raise ValueError("occurrenceCount cannot exceed returnedCount")
        if self.occurrence_count < self.novel_candidate_count:
            raise ValueError("occurrenceCount cannot be lower than novelCandidateCount")
        identities = [(item.rank, item.candidate_id) for item in self.occurrences]
        if identities != sorted(identities):
            raise ValueError("terminal occurrences must use canonical order")
        if len({item.rank for item in self.occurrences}) != len(self.occurrences):
            raise ValueError("terminal occurrence ranks must be unique")
        if len({item.candidate_id for item in self.occurrences}) != len(self.occurrences):
            raise ValueError("terminal occurrence candidates must be unique")
        return self


DISCOVERY_PLAN_APPROVAL_SCHEMA_VERSION = "discovery-plan-approval-v1"
DISCOVERY_PLAN_APPROVAL_REASON = (
    "Approve this exact public paper-search scope. Only the listed query metadata "
    "may be disclosed; returned metadata remains untrusted and no PDF is downloaded."
)


class DiscoveryRunCreateIn(StrictDiscoveryModel):
    goal: QuestionText
    discovery_spec: DiscoverySpec

    @model_validator(mode="after")
    def validate_initial_public_scope(self) -> DiscoveryRunCreateIn:
        if self.goal != self.discovery_spec.question:
            raise ValueError("goal must exactly match discoverySpec.question")
        for query in self.discovery_spec.queries:
            if query.providers == ["crossref"]:
                continue
            if (
                query.providers in (["openalex"], ["crossref", "openalex"])
                and query.sort == "relevance"
                and query.year_from is None
                and query.year_to is None
            ):
                continue
            raise ValueError(
                "the public discovery slice supports Crossref, OpenAlex, or their "
                "canonical combined relevance search without year filters"
            )
        if (
            self.discovery_spec.download_open_access_pdfs
            or self.discovery_spec.max_pdf_downloads != 0
        ):
            raise ValueError("the first public discovery slice prohibits PDF downloads")
        return self


DiscoveryInvocationStatus = Literal[
    "not-started",
    "prepared",
    "pending",
    "succeeded",
    "failed",
    "outcome-unknown",
    "cancelled",
]
DiscoveryRetryClassification = Literal[
    "safe-to-retry",
    "never-retry",
    "manual-review",
]


class DiscoveryOperationProgressOut(StrictDiscoveryModel):
    operation_key: str
    query_id: QueryId
    provider: DiscoveryProvider
    status: DiscoveryInvocationStatus
    attempt: int | None = Field(default=None, ge=1)
    invocation_id: str | None = Field(default=None, min_length=1, max_length=36)
    returned_count: int = Field(ge=0)
    novel_candidate_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    candidate_set_sha256: Sha256 | None
    error_code: str | None = Field(default=None, min_length=1, max_length=100)
    retry_classification: DiscoveryRetryClassification | None
    created_at: datetime | None
    finished_at: datetime | None


class DiscoverySummaryOut(StrictDiscoveryModel):
    total_operations: int = Field(ge=1, le=32)
    not_started_operations: int = Field(ge=0, le=32)
    in_progress_operations: int = Field(ge=0, le=32)
    succeeded_operations: int = Field(ge=0, le=32)
    failed_operations: int = Field(ge=0, le=32)
    outcome_unknown_operations: int = Field(ge=0, le=32)
    cancelled_operations: int = Field(ge=0, le=32)
    returned_count: int = Field(ge=0)
    novel_candidate_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    unique_candidate_count: int = Field(ge=0)
    occurrence_count: int = Field(ge=0)


class DiscoveryCandidateOccurrenceOut(StrictDiscoveryModel):
    invocation_id: str = Field(min_length=1, max_length=36)
    query_id: QueryId
    provider: DiscoveryCandidateProvider
    attempt: int = Field(ge=1)
    rank: int = Field(ge=1)
    raw_item_sha256: Sha256


class DiscoveryCandidateOut(StrictDiscoveryModel):
    id: str = Field(min_length=1, max_length=36)
    provider: DiscoveryCandidateProvider
    provider_id: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=1_000)
    authors: list[str] = Field(max_length=200)
    abstract: str | None = Field(default=None, max_length=20_000)
    publication_date: str | None = Field(default=None, max_length=32)
    doi: str | None = Field(default=None, max_length=255)
    arxiv_id: str | None = Field(default=None, max_length=100)
    pmid: str | None = Field(default=None, max_length=50)
    candidate_sha256: Sha256
    trust_classification: Literal["untrusted-metadata"]
    full_text_verification: Literal["not-verified"]
    import_availability: Literal["manual-pdf-required"]
    landing_page_availability: Literal["reported", "not-reported"]
    open_access_pdf_availability: Literal["reported", "not-reported"]
    attachment_status: Literal["manual-pdf-required", "verified-local-source"]
    attached_source_id: str | None = Field(default=None, max_length=36)
    occurrences: list[DiscoveryCandidateOccurrenceOut] = Field(
        min_length=1,
        max_length=32,
    )


class DiscoveryCandidatePageOut(StrictDiscoveryModel):
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    has_more: bool
    items: list[DiscoveryCandidateOut] = Field(max_length=100)


class CslJsonImportOut(StrictDiscoveryModel):
    schema_version: Literal["1"]
    project_id: str = Field(min_length=1, max_length=36)
    workflow_id: str = Field(min_length=1, max_length=36)
    invocation_id: str = Field(min_length=1, max_length=36)
    file_sha256: Sha256
    parser_version: Literal["csl-json-file-v1"]
    imported_count: int = Field(ge=0, le=500)
    unchanged_count: int = Field(ge=0, le=500)
    candidate_ids: list[str] = Field(max_length=500)
    replayed: bool


class DiscoveryAgentSelectionOut(StrictDiscoveryModel):
    """Latest applied Agent choice within the approved Discovery plan."""

    decision_id: str = Field(min_length=1, max_length=36)
    selected_operation_key: str = Field(min_length=1, max_length=300)
    selected_step_key: str = Field(min_length=1, max_length=100)
    query_id: QueryId
    provider: DiscoveryProvider
    reason_code: Literal[
        "only-eligible-operation",
        "query-coverage-gap",
        "provider-coverage-gap",
        "lower-query-no-novelty",
        "higher-observed-novelty",
        "lower-duplicate-burden",
        "stable-tie-break",
    ]
    eligible_operation_count: int = Field(ge=1, le=32)
    query_attempt_count: int = Field(ge=0, le=8)
    provider_attempt_count: int = Field(ge=0, le=8)
    query_no_novelty_count: int = Field(ge=0, le=8)
    query_novel_candidate_count: int = Field(ge=0)
    query_duplicate_count: int = Field(ge=0)
    selection_snapshot_sha256: Sha256


class WorkflowDiscoverySnapshotOut(StrictDiscoveryModel):
    workflow_id: str = Field(min_length=1, max_length=36)
    project_id: str = Field(min_length=1, max_length=36)
    workflow_status: Literal[
        "waiting-plan-approval",
        "running",
        "blocked",
        "failed",
        "cancelled",
        "completed",
    ]
    stop_reason: DiscoveryPolicyStopReason | None = None
    discovery_spec_id: str = Field(min_length=1, max_length=36)
    discovery_spec_revision: int = Field(ge=1)
    discovery_spec_sha256: Sha256
    discovery_spec_status: Literal[
        "pending-approval",
        "approved",
        "rejected",
        "superseded",
    ]
    exact_scope: DiscoverySpec
    operations: list[DiscoveryOperationProgressOut] = Field(min_length=1, max_length=32)
    summary: DiscoverySummaryOut
    candidates: DiscoveryCandidatePageOut
    latest_agent_selection: DiscoveryAgentSelectionOut | None = None


def discovery_approval_resources(
    *,
    project_id: str,
    spec_id: str,
    revision: int,
    spec_sha256: str,
    spec: DiscoverySpec,
) -> list[str]:
    """Return exact, stable public-search consent resources for one specification."""

    resources = [
        f"project:{project_id}",
        (f"discovery-spec:{spec_id}:revision:{revision}:sha256:{spec_sha256}"),
        "disclosure:public-search:query-metadata-only",
        (
            "discovery-stop-policy:"
            + json.dumps(
                spec.stop_policy.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        ),
        (
            "discovery-download-policy:"
            + json.dumps(
                {
                    "downloadOpenAccessPdfs": spec.download_open_access_pdfs,
                    "maxPdfDownloads": spec.max_pdf_downloads,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        ),
    ]
    resources.extend(f"remote-provider:{provider}" for provider in _providers_in_scope(spec))
    resources.extend(
        "discovery-query:"
        + json.dumps(
            {
                "derivedMaximumResults": query.derived_maximum_results,
                "id": query.id,
                "maxResultsPerProvider": query.max_results_per_provider,
                "providers": query.providers,
                "query": query.query,
                "sort": query.sort,
                "yearFrom": query.year_from,
                "yearTo": query.year_to,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        for query in spec.queries
    )
    return resources


def _providers_in_scope(spec: DiscoverySpec) -> tuple[DiscoveryProvider, ...]:
    """Return approved providers once, in their stable global order."""

    selected = {provider for query in spec.queries for provider in query.providers}
    return tuple(provider for provider in _PROVIDER_ORDER if provider in selected)
