"""Fail-closed coverage projection over one approved frozen literature plan."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Iterable, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    EvidenceSpanRecord,
    ExtractionCellEvidenceRecord,
    ExtractionCellRecord,
    ExtractionColumnRecord,
    PlanRecord,
    SourcePageRecord,
    TaskRecord,
    WorkflowRecord,
)
from ..pdf import PdfPage, locate_quote
from ._handlers.sources import (
    parse_source_descriptors,
    validated_source_descriptors_for_task,
)
from ._handlers.text import normalized_contains
from ._service.integrity import (
    WorkflowConflict,
    assert_approved_plan_for_workflow,
    assert_plan_approval_integrity,
    assert_task_matches_approved_plan,
    content_sha256,
)
from ._service.snapshots import assert_result_sources_current, workflow_snapshot
from .evidence_coverage_schemas import (
    EvidenceCoverageClaimCoverageOut,
    EvidenceCoverageFacetOut,
    EvidenceCoverageOut,
    EvidenceCoverageSourceBreadthOut,
)
from .schemas import FrozenSourceDescriptor
from .state import WorkflowFailure


def workflow_evidence_coverage(
    session: Session,
    workflow: WorkflowRecord,
) -> EvidenceCoverageOut:
    """Return no inferred score: only frozen, human-confirmed local evidence counts."""

    if workflow.workflow_type != "literature-synthesis":
        raise WorkflowConflict(
            "evidence-coverage-workflow-invalid",
            "Evidence coverage is available only for literature synthesis workflows.",
        )

    plan = session.scalar(
        select(PlanRecord)
        .where(PlanRecord.workflow_id == workflow.id, PlanRecord.status == "approved")
        .order_by(PlanRecord.version.desc())
    )
    if plan is None:
        return _not_ready(workflow)

    # An approved plan must itself still bind to its immutable approval before any
    # potentially useful-looking coverage is returned.
    assert_approved_plan_for_workflow(workflow, plan)
    assert_plan_approval_integrity(session, workflow, plan)
    inspect_task = session.scalar(
        select(TaskRecord).where(
            TaskRecord.workflow_id == workflow.id,
            TaskRecord.plan_id == plan.id,
            TaskRecord.step_key == "inspect-sources",
        )
    )
    if inspect_task is None or inspect_task.status != "completed":
        return _not_ready(workflow, plan)
    assert_task_matches_approved_plan(workflow, plan, inspect_task)
    descriptors = _frozen_descriptors(inspect_task)
    # Do not convert an old, incomplete inspect output into a partial score.
    if descriptors is None:
        return _not_ready(workflow, plan)
    try:
        descriptors = validated_source_descriptors_for_task(
            session,
            workflow,
            inspect_task,
            inspect_task=inspect_task,
        )
    except WorkflowFailure as error:
        raise WorkflowConflict(
            "evidence-coverage-frozen-source-invalid",
            "The completed source inspection no longer matches its approved frozen source set.",
        ) from error
    assert_result_sources_current(session, workflow, descriptors)

    source_ids = [descriptor.source_id for descriptor in descriptors]
    columns = list(
        session.scalars(
            select(ExtractionColumnRecord)
            .where(ExtractionColumnRecord.project_id == workflow.project_id)
            .order_by(ExtractionColumnRecord.order_index, ExtractionColumnRecord.id)
        )
    )
    cells = list(
        session.scalars(
            select(ExtractionCellRecord)
            .where(
                ExtractionCellRecord.project_id == workflow.project_id,
                ExtractionCellRecord.source_id.in_(source_ids),
            )
            .order_by(ExtractionCellRecord.source_id, ExtractionCellRecord.column_id)
        )
    )
    cells_by_pair = {(cell.source_id, cell.column_id): cell for cell in cells}
    confirmed_cell_ids = [
        cell.id for cell in cells if cell.review_status == "confirmed"
    ]
    links_by_cell = _evidence_links(
        session,
        workflow.project_id,
        source_ids,
        confirmed_cell_ids,
    )
    evidence_by_id = _verified_referenced_evidence_or_conflict(
        session,
        workflow,
        source_ids,
        links_by_cell.values(),
    )

    facets: list[EvidenceCoverageFacetOut] = []
    covered_any_source: set[str] = set()
    covered_evidence_ids: set[str] = set()
    for column in columns:
        covered = awaiting = unverified = missing = 0
        for source_id in source_ids:
            cell = cells_by_pair.get((source_id, column.id))
            if cell is None:
                missing += 1
                continue
            if cell.review_status != "confirmed":
                awaiting += 1
                continue
            evidence_ids = links_by_cell.get(cell.id, [])
            if not evidence_ids:
                missing += 1
                continue
            if all(evidence_id in evidence_by_id for evidence_id in evidence_ids):
                covered += 1
                covered_any_source.add(source_id)
                covered_evidence_ids.update(evidence_ids)
            else:
                unverified += 1
        state = _facet_state(
            total=len(source_ids), covered=covered, unverified=unverified
        )
        facets.append(
            EvidenceCoverageFacetOut(
                column_id=column.id,
                name=column.name,
                state=state,
                source_count=len(source_ids),
                covered_source_count=covered,
                awaiting_confirmation_source_count=awaiting,
                unverified_source_count=unverified,
                missing_source_count=missing,
            )
        )

    return EvidenceCoverageOut(
        workflow_id=workflow.id,
        project_id=workflow.project_id,
        plan_id=plan.id,
        plan_version=plan.version,
        plan_sha256=plan.spec_sha256,
        state="available",
        source_set_sha256=content_sha256(
            [item.model_dump(mode="json", by_alias=True) for item in descriptors]
        ),
        source_breadth=EvidenceCoverageSourceBreadthOut(
            frozen_source_count=len(source_ids),
            sources_with_covered_evidence_count=len(covered_any_source),
            sources_without_covered_evidence_count=len(source_ids) - len(covered_any_source),
            verified_referenced_span_count=len(covered_evidence_ids),
        ),
        facets=facets,
        claim_coverage=_claim_coverage(session, workflow),
    )


def _not_ready(
    workflow: WorkflowRecord,
    plan: PlanRecord | None = None,
) -> EvidenceCoverageOut:
    return EvidenceCoverageOut(
        workflow_id=workflow.id,
        project_id=workflow.project_id,
        plan_id=plan.id if plan is not None else None,
        plan_version=plan.version if plan is not None else None,
        plan_sha256=plan.spec_sha256 if plan is not None else None,
        state="not-ready",
        source_set_sha256=None,
        source_breadth=EvidenceCoverageSourceBreadthOut(
            frozen_source_count=0,
            sources_with_covered_evidence_count=0,
            sources_without_covered_evidence_count=0,
            verified_referenced_span_count=0,
        ),
        facets=[],
        claim_coverage=EvidenceCoverageClaimCoverageOut(
            state="not-generated",
            total_claim_count=0,
            evidence_linked_claim_count=0,
            supported_claim_count=0,
            unresolved_question_count=0,
        ),
    )


def _claim_coverage(
    session: Session,
    workflow: WorkflowRecord,
) -> EvidenceCoverageClaimCoverageOut:
    """Project structural claim counts from the canonical workflow snapshot only."""

    snapshot = workflow_snapshot(session, workflow)
    result = snapshot.result
    if result is None:
        return EvidenceCoverageClaimCoverageOut(
            state="not-generated",
            total_claim_count=0,
            evidence_linked_claim_count=0,
            supported_claim_count=0,
            unresolved_question_count=0,
        )

    total_claim_count = len(result.claims)
    unresolved_question_count = len(result.unresolved_questions)
    if total_claim_count == 0:
        return EvidenceCoverageClaimCoverageOut(
            state="not-generated",
            total_claim_count=0,
            evidence_linked_claim_count=0,
            supported_claim_count=0,
            unresolved_question_count=unresolved_question_count,
        )

    verified_frozen = (
        result.integrity_status == "verified-frozen-v2"
        and snapshot.latest_review is not None
        and snapshot.latest_review.verdict == "passed"
    )
    if not verified_frozen:
        return EvidenceCoverageClaimCoverageOut(
            state="not-verified",
            total_claim_count=total_claim_count,
            evidence_linked_claim_count=0,
            supported_claim_count=0,
            unresolved_question_count=unresolved_question_count,
        )

    evidence_linked_claim_count = sum(
        any(
            relationship.verified
            and relationship.source_content_hash is not None
            and relationship.source_page_manifest_hash is not None
            for relationship in claim.evidence
        )
        for claim in result.claims
    )
    # Retained for schema-v1 clients only. It is not surfaced as a coverage score.
    supported_claim_count = sum(
        claim.support_status == "supported" for claim in result.claims
    )
    return EvidenceCoverageClaimCoverageOut(
        state="verified-frozen",
        total_claim_count=total_claim_count,
        evidence_linked_claim_count=evidence_linked_claim_count,
        supported_claim_count=supported_claim_count,
        unresolved_question_count=unresolved_question_count,
    )


def _frozen_descriptors(task: TaskRecord) -> list[FrozenSourceDescriptor] | None:
    raw = task.outputs.get("sourceDescriptors")
    if raw is None:
        return None
    try:
        return parse_source_descriptors(raw)
    except Exception as error:
        raise WorkflowConflict(
            "evidence-coverage-frozen-source-invalid",
            "The completed source inspection has no valid immutable source set.",
        ) from error


def _evidence_links(
    session: Session,
    project_id: str,
    source_ids: list[str],
    cell_ids: list[str],
) -> dict[str, list[str]]:
    if not cell_ids:
        return {}
    rows = session.execute(
        select(ExtractionCellEvidenceRecord.cell_id, ExtractionCellEvidenceRecord.evidence_id)
        .where(
            ExtractionCellEvidenceRecord.project_id == project_id,
            ExtractionCellEvidenceRecord.source_id.in_(source_ids),
            ExtractionCellEvidenceRecord.cell_id.in_(cell_ids),
        )
        .order_by(
            ExtractionCellEvidenceRecord.cell_id,
            ExtractionCellEvidenceRecord.evidence_id,
        )
    )
    result: dict[str, list[str]] = defaultdict(list)
    for cell_id, evidence_id in rows:
        result[str(cell_id)].append(str(evidence_id))
    return dict(result)


def _verified_referenced_evidence_or_conflict(
    session: Session,
    workflow: WorkflowRecord,
    source_ids: list[str],
    linked_evidence_ids: Iterable[list[str]],
) -> dict[str, EvidenceSpanRecord]:
    ids = sorted({item for group in linked_evidence_ids for item in group})
    if not ids:
        return {}
    evidence_rows = list(
        session.scalars(
            select(EvidenceSpanRecord)
            .where(EvidenceSpanRecord.id.in_(ids))
            .order_by(EvidenceSpanRecord.id)
        )
    )
    rows_by_id = {record.id: record for record in evidence_rows}
    verified: dict[str, EvidenceSpanRecord] = {}
    for evidence_id in ids:
        evidence = rows_by_id.get(evidence_id)
        # A broken foreign key cannot occur in a healthy database. Fail closed
        # rather than silently treating it as an unverified extraction.
        if evidence is None or evidence.source_id not in source_ids:
            raise WorkflowConflict(
                "evidence-coverage-link-invalid",
                "An extraction cell points outside the frozen source set.",
            )
        if evidence.verified:
            assert_verified_evidence_span_current(session, workflow, evidence)
            verified[evidence.id] = evidence
    return verified


def assert_verified_evidence_span_current(
    session: Session,
    workflow: WorkflowRecord,
    evidence: EvidenceSpanRecord,
) -> None:
    page = session.scalar(
        select(SourcePageRecord).where(
            SourcePageRecord.source_id == evidence.source_id,
            SourcePageRecord.page_index == evidence.page_index,
        )
    )
    if page is None:
        raise WorkflowConflict(
            "evidence-coverage-span-invalid",
            "A referenced verified evidence span no longer has its frozen source page.",
        )
    located = locate_quote(
        evidence.text,
        [
            PdfPage(
                page_index=page.page_index,
                page_label=page.page_label,
                width=page.width,
                height=page.height,
                text=page.text,
                words=page.words,
            )
        ],
    )
    valid = (
        evidence.quote_hash == hashlib.sha256(evidence.text.encode("utf-8")).hexdigest()
        and normalized_contains(page.text, evidence.text)
        and located is not None
        and located.verified
        and evidence.page_label == located.page_label
        and evidence.bbox == located.bbox
        and evidence.coordinate_space == "normalized-rotated-top-left-v1"
    )
    if not valid:
        raise WorkflowConflict(
            "evidence-coverage-span-invalid",
            "A referenced verified evidence span no longer matches its page, quote, or location.",
        )


__all__ = (
    "assert_verified_evidence_span_current",
    "workflow_evidence_coverage",
)


def _facet_state(
    *, total: int, covered: int, unverified: int
) -> Literal["complete", "partial", "unverified", "missing"]:
    if total > 0 and covered == total:
        return "complete"
    if covered > 0:
        return "partial"
    if unverified > 0:
        return "unverified"
    return "missing"
