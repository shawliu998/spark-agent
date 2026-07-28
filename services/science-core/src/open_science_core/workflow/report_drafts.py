from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Literal

from markdown_it import MarkdownIt
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    ReportDraftMutationRecord,
    ReportDraftRecord,
    ReviewRecord,
    WorkflowRecord,
    utc_now,
)
from .report_draft_schemas import (
    CreateReportDraftIn,
    ExportReportDraftIn,
    ReportDraftExportOut,
    ReviewReportDraftIn,
    SaveReportDraftIn,
)
from .schemas import DeterministicReviewResult, WorkflowResultOut
from .service import WorkflowConflict, content_sha256, workflow_snapshot
from .state import WorkflowFailure

_CITATION_TOKEN = re.compile(r"\[@evidence:([A-Za-z0-9_-]+):([0-9a-f]{64})\]")
_CITATION_PREFIX = "[@evidence:"
_REFERENCE_LINE = re.compile(
    r"^[ \t]*(?P<number>[1-9][0-9]{0,4})\.[^\n]*?"
    r"<!--[ \t]*(?P<token>\[@evidence:[A-Za-z0-9_-]+:[0-9a-f]{64}\])"
    r"[ \t]*-->[ \t]*$",
    re.MULTILINE,
)
_VISIBLE_CITATION_CONTENT = re.compile(
    r"[ \t]*[1-9][0-9]{0,4}"
    r"(?:[ \t]*,[ \t]*[1-9][0-9]{0,4})*[ \t]*"
)
_VISIBLE_CITATION_NUMBER = re.compile(r"[1-9][0-9]{0,4}")
_INTERNAL_EVIDENCE_MARKER = re.compile(r"\[evidence:[^\]\r\n]{1,256}\]")
_INLINE_BOUNDARY = "\0"
_MARKDOWN = MarkdownIt("commonmark")
# Preserve escaped punctuation as text_special tokens so it cannot become a citation.
_MARKDOWN.inline.ruler2.disable(["fragments_join"])


@dataclass(frozen=True, slots=True)
class ReportBase:
    workflow_sha256: str
    result_sha256: str
    evidence_sha256: str
    result: WorkflowResultOut


@dataclass(frozen=True, slots=True)
class CitationDocument:
    token_by_number: dict[int, str]
    referenced_numbers: frozenset[int]
    tokens: frozenset[str]


@dataclass(frozen=True, slots=True)
class MarkdownCitationInput:
    visible_blocks: tuple[str, ...]
    html_fragments: tuple[str, ...]
    inline_sources: tuple[str, ...]


def _raw_content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _payload_sha256(payload: object) -> str:
    if isinstance(payload, BaseModel):
        value = payload.model_dump(mode="json", by_alias=True, exclude_none=False)
    else:
        value = payload
    return content_sha256(value)


def _record_postcondition_sha256(record: ReportDraftRecord) -> str:
    return content_sha256(
        {
            "id": record.id,
            "projectId": record.project_id,
            "workflowId": record.workflow_id,
            "schemaVersion": record.schema_version,
            "revision": record.revision,
            "contentSha256": record.content_sha256,
            "baseWorkflowSha256": record.base_workflow_sha256,
            "baseResultSha256": record.base_result_sha256,
            "baseEvidenceSha256": record.base_evidence_sha256,
            "status": record.status,
        }
    )


def _replay_or_conflict(
    session: Session,
    record: ReportDraftRecord,
    *,
    operation: Literal["create", "save", "review"],
    idempotency_key: str,
    payload_sha256: str,
) -> bool:
    mutation = session.scalar(
        select(ReportDraftMutationRecord).where(
            ReportDraftMutationRecord.draft_id == record.id,
            ReportDraftMutationRecord.idempotency_key == idempotency_key,
        )
    )
    if mutation is None:
        return False
    if mutation.operation != operation or mutation.payload_sha256 != payload_sha256:
        raise WorkflowFailure(
            "report-draft-idempotency-conflict",
            "This report idempotency key was already used with a different request.",
        )
    if mutation.postcondition_sha256 != _record_postcondition_sha256(record):
        raise WorkflowFailure(
            "report-draft-idempotency-stale",
            "This report request already completed, but its original result is no longer current.",
        )
    return True


def _record_mutation(
    session: Session,
    record: ReportDraftRecord,
    *,
    operation: Literal["create", "save", "review"],
    idempotency_key: str,
    payload_sha256: str,
) -> None:
    session.add(
        ReportDraftMutationRecord(
            id=str(uuid.uuid4()),
            draft_id=record.id,
            idempotency_key=idempotency_key,
            operation=operation,
            payload_sha256=payload_sha256,
            postcondition_sha256=_record_postcondition_sha256(record),
        )
    )
    session.flush()


def replay_report_draft_mutation(
    session: Session,
    record: ReportDraftRecord,
    *,
    operation: Literal["create", "save", "review"],
    payload: object,
    idempotency_key: str,
) -> ReportDraftRecord:
    if not _replay_or_conflict(
        session,
        record,
        operation=operation,
        idempotency_key=idempotency_key,
        payload_sha256=_payload_sha256(payload),
    ):
        raise WorkflowFailure(
            "report-draft-idempotency-conflict",
            "The concurrent report mutation did not record this idempotency request.",
        )
    return record


def _citation_tokens(result: WorkflowResultOut) -> set[str]:
    return {
        f"[@evidence:{evidence.evidence_id}:{evidence.quote_hash}]"
        for claim in result.claims
        for evidence in claim.evidence
    }


def _mask_escaped_open_brackets(content: str) -> str:
    masked = list(content)
    for index, character in enumerate(masked):
        if character != "[":
            continue
        slash_count = 0
        cursor = index - 1
        while cursor >= 0 and masked[cursor] == "\\":
            slash_count += 1
            cursor -= 1
        if slash_count % 2 == 1:
            masked[index] = _INLINE_BOUNDARY
    return "".join(masked)


def _markdown_citation_input(content: str) -> MarkdownCitationInput:
    lines = content.splitlines(keepends=True)
    visible_blocks: list[str] = []
    html_fragments: list[str] = []
    inline_sources: list[str] = []
    seen_source_maps: set[tuple[int, int]] = set()
    for token in _MARKDOWN.parse(_mask_escaped_open_brackets(content)):
        if token.type != "inline":
            continue
        visible: list[str] = []
        for child in token.children or ():
            if child.type == "text":
                visible.append(child.content)
            elif child.type in {"softbreak", "hardbreak"}:
                visible.append("\n")
            elif child.type == "html_inline":
                html_fragments.append(child.content)
                visible.append(_INLINE_BOUNDARY)
            else:
                visible.append(_INLINE_BOUNDARY)
        visible_blocks.append("".join(visible))
        if token.map is None:
            continue
        source_map = (token.map[0], token.map[1])
        if source_map in seen_source_maps:
            continue
        seen_source_maps.add(source_map)
        inline_sources.append("".join(lines[source_map[0] : source_map[1]]))
    return MarkdownCitationInput(
        visible_blocks=tuple(visible_blocks),
        html_fragments=tuple(html_fragments),
        inline_sources=tuple(inline_sources),
    )


def _visible_citation_numbers(visible_text: str) -> frozenset[int]:
    numbers: set[int] = set()
    cursor = 0
    while cursor < len(visible_text):
        if visible_text[cursor] != "[":
            cursor += 1
            continue
        bracket_end = visible_text.find("]", cursor + 1)
        if bracket_end < 0:
            line_end = visible_text.find("\n", cursor + 1)
            candidate_end = len(visible_text) if line_end < 0 else line_end
            if any(char.isdigit() for char in visible_text[cursor + 1 : candidate_end]):
                raise WorkflowFailure(
                    "report-draft-citation-invalid",
                    "Numeric Markdown brackets must use a citation such as [1] "
                    "or [1, 2].",
                )
            cursor += 1
            continue
        candidate = visible_text[cursor + 1 : bracket_end]
        if not any(char.isdigit() for char in candidate):
            cursor = bracket_end + 1
            continue
        if _VISIBLE_CITATION_CONTENT.fullmatch(candidate) is None:
            raise WorkflowFailure(
                "report-draft-citation-invalid",
                "Numeric Markdown brackets must use a citation such as [1] "
                "or [1, 2].",
            )
        numbers.update(
            int(match.group(0))
            for match in _VISIBLE_CITATION_NUMBER.finditer(candidate)
        )
        cursor = bracket_end + 1
    return frozenset(numbers)


def _validate_citations(
    content: str,
    allowed_tokens: set[str],
) -> CitationDocument:
    markdown = _markdown_citation_input(content)
    token_occurrences: list[str] = []
    for fragment in (*markdown.visible_blocks, *markdown.html_fragments):
        matches = list(_CITATION_TOKEN.finditer(fragment))
        if _CITATION_PREFIX in _CITATION_TOKEN.sub("", fragment):
            raise WorkflowFailure(
                "report-draft-citation-invalid",
                "The report contains an invalid evidence citation token.",
            )
        token_occurrences.extend(match.group(0) for match in matches)
    reference_entries = [
        (int(match.group("number")), match.group("token"))
        for source in markdown.inline_sources
        for match in _REFERENCE_LINE.finditer(source)
    ]
    if not token_occurrences or not reference_entries:
        raise WorkflowFailure(
            "report-draft-citation-required",
            "The report must retain at least one numbered evidence reference.",
        )
    reference_tokens = [token for _, token in reference_entries]
    if sorted(token_occurrences) != sorted(reference_tokens):
        raise WorkflowFailure(
            "report-draft-citation-invalid",
            "Evidence tokens are allowed only in their numbered reference definitions.",
        )
    token_by_number: dict[int, str] = {}
    for number, token in reference_entries:
        if number in token_by_number or token in token_by_number.values():
            raise WorkflowFailure(
                "report-draft-citation-invalid",
                "Each reference number and evidence token must have one unique binding.",
            )
        token_by_number[number] = token
    tokens = frozenset(token_occurrences)
    if len(tokens) != len(token_occurrences) or tokens != frozenset(
        token_by_number.values()
    ):
        raise WorkflowFailure(
            "report-draft-citation-invalid",
            "Each evidence token must appear in exactly one numbered reference.",
        )
    if not tokens.issubset(allowed_tokens):
        raise WorkflowFailure(
            "report-draft-citation-invalid",
            "The report contains an evidence citation outside its frozen result.",
        )
    referenced_numbers = frozenset(
        number
        for visible_block in markdown.visible_blocks
        for number in _visible_citation_numbers(visible_block)
    )
    if not referenced_numbers or not referenced_numbers.issubset(token_by_number):
        raise WorkflowFailure(
            "report-draft-citation-invalid",
            "Every visible citation index must resolve to a numbered evidence reference.",
        )
    if referenced_numbers != frozenset(token_by_number):
        raise WorkflowFailure(
            "report-draft-citation-invalid",
            "Every numbered evidence reference must be cited visibly in the report.",
        )
    return CitationDocument(
        token_by_number=token_by_number,
        referenced_numbers=referenced_numbers,
        tokens=tokens,
    )


def _rebase_citations(
    content: str,
    old_result: WorkflowResultOut,
    current_result: WorkflowResultOut,
    payload: ReviewReportDraftIn,
) -> str:
    old_allowed_tokens = _citation_tokens(old_result)
    document = _validate_citations(content, old_allowed_tokens)
    current_allowed_tokens = _citation_tokens(current_result)
    expected_previous_tokens = set(document.tokens).difference(current_allowed_tokens)
    replacements: dict[str, str] = {}
    current_targets: set[str] = set()
    for rebase in payload.citation_rebases:
        previous_token = (
            f"[@evidence:{rebase.previous_evidence_id}:{rebase.previous_quote_hash}]"
        )
        current_token = (
            f"[@evidence:{rebase.current_evidence_id}:{rebase.current_quote_hash}]"
        )
        if (
            previous_token in replacements
            or current_token in current_targets
            or previous_token not in old_allowed_tokens
            or previous_token not in document.tokens
            or current_token not in current_allowed_tokens
        ):
            raise WorkflowFailure(
                "report-draft-rebase-invalid",
                "Citation rebase entries must uniquely map cited old evidence to current "
                "authoritative evidence.",
            )
        replacements[previous_token] = current_token
        current_targets.add(current_token)
    if set(replacements) != expected_previous_tokens:
        raise WorkflowFailure(
            "report-draft-rebase-required",
            "Provide one explicit citation rebase for every cited evidence token that "
            "changed in the current reviewed result.",
        )
    rebased = _CITATION_TOKEN.sub(
        lambda match: replacements.get(match.group(0), match.group(0)),
        content,
    )
    _validate_citations(rebased, current_allowed_tokens)
    return rebased


def _evidence_snapshot(result: WorkflowResultOut) -> list[dict[str, object]]:
    return [
        {
            "claimId": claim.id,
            "claimStatement": claim.statement,
            "evidence": evidence.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=False,
            ),
        }
        for claim in result.claims
        for evidence in claim.evidence
    ]


def _authoritative_base(session: Session, workflow: WorkflowRecord) -> ReportBase:
    if workflow.workflow_type != "literature-synthesis":
        raise WorkflowFailure(
            "report-draft-workflow-type-invalid",
            "Report drafts are available only for literature workflows.",
        )
    if workflow.status != "completed":
        raise WorkflowFailure(
            "report-draft-workflow-incomplete",
            "Complete and review the literature workflow before creating a report draft.",
        )
    try:
        snapshot = workflow_snapshot(session, workflow)
    except WorkflowConflict as error:
        raise WorkflowFailure(
            "report-draft-base-stale",
            "The reviewed workflow result or its evidence no longer matches the saved source state.",
        ) from error
    result = snapshot.result
    review = snapshot.latest_review
    if (
        result is None
        or result.integrity_status != "verified-frozen-v2"
        or review is None
        or review.verdict != "passed"
        or not isinstance(review.result, DeterministicReviewResult)
        or review.result.result_snapshot_sha256 is None
    ):
        raise WorkflowFailure(
            "report-draft-base-unverified",
            "A verified frozen workflow result is required before creating or rebasing a report.",
        )
    result_sha256 = content_sha256(
        result.model_dump(mode="json", by_alias=True, exclude_none=False)
    )
    if result_sha256 != review.result.result_snapshot_sha256:
        raise WorkflowFailure(
            "report-draft-base-stale",
            "The reviewed workflow result no longer matches its frozen snapshot.",
        )
    workflow_sha256 = content_sha256(
        {
            "workflow": snapshot.workflow.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=False,
            ),
            "reviewId": review.id,
            "reviewInputSha256": review.input_sha256,
            "resultSha256": result_sha256,
        }
    )
    return ReportBase(
        workflow_sha256=workflow_sha256,
        result_sha256=result_sha256,
        evidence_sha256=content_sha256(_evidence_snapshot(result)),
        result=result,
    )


def _frozen_result_for_draft(
    session: Session,
    record: ReportDraftRecord,
) -> WorkflowResultOut:
    reviews = session.scalars(
        select(ReviewRecord)
        .where(ReviewRecord.workflow_id == record.workflow_id)
        .order_by(ReviewRecord.created_at.desc(), ReviewRecord.id.desc())
    )
    for review in reviews:
        try:
            parsed = DeterministicReviewResult.model_validate(review.result_json)
        except ValidationError:
            continue
        result = parsed.result_snapshot
        if (
            result is not None
            and parsed.result_snapshot_sha256 == record.base_result_sha256
            and content_sha256(
                result.model_dump(mode="json", by_alias=True, exclude_none=False)
            )
            == record.base_result_sha256
        ):
            return result
    raise WorkflowFailure(
        "report-draft-base-missing",
        "The frozen result used by this report draft is no longer available.",
    )


def _initial_markdown(result: WorkflowResultOut) -> str:
    summary = _INTERNAL_EVIDENCE_MARKER.sub("", result.summary).strip()
    lines = ["# Research synthesis", "", summary, "", "## Findings", ""]
    citation_indexes: dict[str, int] = {}
    next_index = 1
    for claim in result.claims:
        indexes: list[int] = []
        for evidence in claim.evidence:
            token = f"[@evidence:{evidence.evidence_id}:{evidence.quote_hash}]"
            if token not in citation_indexes:
                citation_indexes[token] = next_index
                next_index += 1
            indexes.append(citation_indexes[token])
        markers = " ".join(f"[{index}]" for index in indexes)
        lines.append(f"- {claim.statement.strip()} {markers}".rstrip())
    if result.unresolved_questions:
        lines.extend(["", "## Unresolved questions", ""])
        lines.extend(f"- {item.strip()}" for item in result.unresolved_questions)
    lines.extend(["", "## References", ""])
    seen: set[str] = set()
    for claim in result.claims:
        for evidence in claim.evidence:
            token = f"[@evidence:{evidence.evidence_id}:{evidence.quote_hash}]"
            if token in seen:
                continue
            seen.add(token)
            page = evidence.page_label or str(evidence.page_index + 1)
            title = evidence.source_title or evidence.source_id
            lines.append(
                f"{citation_indexes[token]}. {title}, page {page} "
                f"<!-- {token} -->"
            )
    return "\n".join(lines).strip() + "\n"


def create_report_draft(
    session: Session,
    workflow: WorkflowRecord,
    payload: CreateReportDraftIn,
    idempotency_key: str,
) -> ReportDraftRecord:
    payload_hash = _payload_sha256(payload)
    existing = session.scalar(
        select(ReportDraftRecord).where(ReportDraftRecord.workflow_id == workflow.id)
    )
    if existing is not None:
        if _replay_or_conflict(
            session,
            existing,
            operation="create",
            idempotency_key=idempotency_key,
            payload_sha256=payload_hash,
        ):
            return existing
        raise WorkflowFailure(
            "report-draft-already-exists",
            "A report draft already exists for this workflow.",
        )
    base = _authoritative_base(session, workflow)
    content = _initial_markdown(base.result)
    _validate_citations(content, _citation_tokens(base.result))
    record = ReportDraftRecord(
        id=str(uuid.uuid4()),
        project_id=workflow.project_id,
        workflow_id=workflow.id,
        schema_version="1",
        revision=1,
        content_markdown=content,
        content_sha256=_raw_content_sha256(content),
        base_workflow_sha256=base.workflow_sha256,
        base_result_sha256=base.result_sha256,
        base_evidence_sha256=base.evidence_sha256,
        status="draft",
        create_idempotency_key=idempotency_key,
        create_payload_sha256=payload_hash,
    )
    session.add(record)
    session.flush()
    _record_mutation(
        session,
        record,
        operation="create",
        idempotency_key=idempotency_key,
        payload_sha256=payload_hash,
    )
    return record


def refresh_report_draft_status(
    session: Session,
    workflow: WorkflowRecord,
    record: ReportDraftRecord,
) -> ReportDraftRecord:
    try:
        base = _authoritative_base(session, workflow)
    except WorkflowFailure:
        base = None
    stale = base is None or (
        record.base_workflow_sha256,
        record.base_result_sha256,
        record.base_evidence_sha256,
    ) != (
        base.workflow_sha256,
        base.result_sha256,
        base.evidence_sha256,
    )
    if stale:
        _mark_needs_review(session, record)
    return record


def _mark_needs_review(
    session: Session,
    record: ReportDraftRecord,
) -> None:
    if record.status != "needs-review":
        record.status = "needs-review"
        record.revision += 1
        record.updated_at = utc_now()
        session.flush()


def _assert_cas(
    record: ReportDraftRecord,
    *,
    expected_revision: int,
    expected_content_sha256: str,
) -> None:
    if (
        record.revision != expected_revision
        or record.content_sha256 != expected_content_sha256
    ):
        raise WorkflowFailure(
            "report-draft-conflict",
            "The report draft changed. Reload it before saving or reviewing.",
        )


def save_report_draft(
    session: Session,
    workflow: WorkflowRecord,
    record: ReportDraftRecord,
    payload: SaveReportDraftIn,
    idempotency_key: str,
) -> ReportDraftRecord:
    payload_hash = _payload_sha256(payload)
    if _replay_or_conflict(
        session,
        record,
        operation="save",
        idempotency_key=idempotency_key,
        payload_sha256=payload_hash,
    ):
        return record
    _assert_cas(
        record,
        expected_revision=payload.expected_revision,
        expected_content_sha256=payload.expected_content_sha256,
    )
    frozen_result = _frozen_result_for_draft(session, record)
    _validate_citations(payload.content_markdown, _citation_tokens(frozen_result))
    try:
        base = _authoritative_base(session, workflow)
    except WorkflowFailure:
        base = None
    stale = base is None or (
        record.base_workflow_sha256,
        record.base_result_sha256,
        record.base_evidence_sha256,
    ) != (
        base.workflow_sha256,
        base.result_sha256,
        base.evidence_sha256,
    )
    record.content_markdown = payload.content_markdown
    record.content_sha256 = _raw_content_sha256(payload.content_markdown)
    record.status = "needs-review" if stale else "draft"
    record.revision += 1
    record.updated_at = utc_now()
    session.flush()
    _record_mutation(
        session,
        record,
        operation="save",
        idempotency_key=idempotency_key,
        payload_sha256=payload_hash,
    )
    return record


def review_report_draft(
    session: Session,
    workflow: WorkflowRecord,
    record: ReportDraftRecord,
    payload: ReviewReportDraftIn,
    idempotency_key: str,
) -> ReportDraftRecord:
    payload_hash = _payload_sha256(payload)
    if _replay_or_conflict(
        session,
        record,
        operation="review",
        idempotency_key=idempotency_key,
        payload_sha256=payload_hash,
    ):
        return record
    _assert_cas(
        record,
        expected_revision=payload.expected_revision,
        expected_content_sha256=payload.expected_content_sha256,
    )
    frozen_result = _frozen_result_for_draft(session, record)
    _validate_citations(record.content_markdown, _citation_tokens(frozen_result))
    try:
        base = _authoritative_base(session, workflow)
    except WorkflowFailure as error:
        _mark_needs_review(session, record)
        raise WorkflowFailure(
            "report-draft-base-stale",
            "The report base changed. Review and rebase it before exporting.",
        ) from error
    base_changed = (
        record.base_workflow_sha256,
        record.base_result_sha256,
        record.base_evidence_sha256,
    ) != (
        base.workflow_sha256,
        base.result_sha256,
        base.evidence_sha256,
    )
    if base_changed:
        try:
            content = _rebase_citations(
                record.content_markdown,
                frozen_result,
                base.result,
                payload,
            )
        except WorkflowFailure as error:
            _mark_needs_review(session, record)
            if error.code == "report-draft-rebase-required":
                raise
            raise WorkflowFailure(
                "report-draft-rebase-invalid",
                error.user_message,
            ) from error
    else:
        if payload.citation_rebases:
            raise WorkflowFailure(
                "report-draft-rebase-invalid",
                "Citation rebases are accepted only when the authoritative report base changed.",
            )
        _validate_citations(record.content_markdown, _citation_tokens(base.result))
        content = record.content_markdown
    record.content_markdown = content
    record.content_sha256 = _raw_content_sha256(content)
    record.base_workflow_sha256 = base.workflow_sha256
    record.base_result_sha256 = base.result_sha256
    record.base_evidence_sha256 = base.evidence_sha256
    record.status = "reviewed"
    record.revision += 1
    record.updated_at = utc_now()
    session.flush()
    _record_mutation(
        session,
        record,
        operation="review",
        idempotency_key=idempotency_key,
        payload_sha256=payload_hash,
    )
    return record


def export_report_draft(
    session: Session,
    workflow: WorkflowRecord,
    record: ReportDraftRecord,
    payload: ExportReportDraftIn,
) -> ReportDraftExportOut:
    _assert_cas(
        record,
        expected_revision=payload.expected_revision,
        expected_content_sha256=payload.expected_content_sha256,
    )
    if record.status == "needs-review":
        raise WorkflowFailure(
            "report-draft-needs-review",
            "Review and rebase this report against the current evidence before exporting.",
        )
    try:
        base = _authoritative_base(session, workflow)
    except WorkflowFailure as error:
        record.status = "needs-review"
        record.revision += 1
        record.updated_at = utc_now()
        session.flush()
        raise WorkflowFailure(
            "report-draft-base-stale",
            "The report base changed. Review and rebase it before exporting.",
        ) from error
    if (
        record.base_workflow_sha256,
        record.base_result_sha256,
        record.base_evidence_sha256,
    ) != (
        base.workflow_sha256,
        base.result_sha256,
        base.evidence_sha256,
    ):
        if record.status != "needs-review":
            record.status = "needs-review"
            record.revision += 1
            record.updated_at = utc_now()
            session.flush()
        raise WorkflowFailure(
            "report-draft-base-stale",
            "The report base changed. Review and rebase it before exporting.",
        )
    _validate_citations(record.content_markdown, _citation_tokens(base.result))
    return ReportDraftExportOut(
        draft_id=record.id,
        project_id=record.project_id,
        workflow_id=record.workflow_id,
        revision=record.revision,
        content_markdown=record.content_markdown,
        content_sha256=record.content_sha256,
        base_workflow_sha256=record.base_workflow_sha256,
        base_result_sha256=record.base_result_sha256,
        base_evidence_sha256=record.base_evidence_sha256,
    )


__all__ = (
    "create_report_draft",
    "export_report_draft",
    "refresh_report_draft_status",
    "replay_report_draft_mutation",
    "review_report_draft",
    "save_report_draft",
)
