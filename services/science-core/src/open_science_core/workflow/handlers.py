from __future__ import annotations

import hashlib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..analysis import sha256_file
from ..models import (
    AnswerRecord,
    ApprovalRecord,
    ClaimEvidenceRecord,
    ClaimRecord,
    EvidenceSpanRecord,
    JobRecord,
    PlanRecord,
    ProjectRecord,
    ReviewRecord,
    SourcePageRecord,
    SourceRecord,
    TaskRecord,
    WorkflowRecord,
    utc_now,
)
from ..pdf import PdfPage, locate_quote
from .schemas import (
    ApprovalEventData,
    ClaimReviewResult,
    DeterministicReviewResult,
    ExtractLocalEvidenceInput,
    InspectSourcesInput,
    PlanEventData,
    PlanSpec,
    ReviewCheck,
    ReviewEventData,
    SequentialStepSpec,
    SynthesizeExtractiveClaimsInput,
    TaskEventData,
)
from .service import (
    MAX_JOB_ATTEMPTS,
    append_workflow_events,
    assert_plan_integrity,
    content_sha256,
    current_job_input_hash,
    enqueue_job,
    plan_approval_hash,
    retry_delay_seconds,
    transition_task,
    transition_workflow,
)
from .state import WorkflowBlockedError, WorkflowFailure


_ENGLISH_STOPWORDS = {
    "about",
    "after",
    "also",
    "among",
    "and",
    "are",
    "been",
    "between",
    "can",
    "could",
    "does",
    "for",
    "from",
    "has",
    "have",
    "into",
    "its",
    "may",
    "more",
    "most",
    "not",
    "our",
    "should",
    "that",
    "the",
    "their",
    "these",
    "this",
    "those",
    "through",
    "using",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
}


@dataclass(frozen=True, slots=True)
class PassageCandidate:
    source_id: str
    page: SourcePageRecord
    text: str
    score: float


def template_plan(goal: str) -> PlanSpec:
    return PlanSpec(
        goal=goal,
        steps=[
            SequentialStepSpec(
                key="inspect-sources",
                type="inspect-sources",
                objective="Validate and select every ready local PDF in this project.",
                inputs=InspectSourcesInput(),
                expected_outputs=["sources"],
                acceptance_criteria=["at-least-one-ready-pdf"],
            ),
            SequentialStepSpec(
                key="extract-local-evidence",
                type="extract-local-evidence",
                objective="Find bounded, source-diverse passages relevant to the research goal.",
                inputs=ExtractLocalEvidenceInput(query=goal),
                expected_outputs=["evidence"],
                acceptance_criteria=["at-least-one-verified-evidence"],
            ),
            SequentialStepSpec(
                key="synthesize-extractive-claims",
                type="synthesize-extractive-claims",
                objective="Create atomic extractive claims and a concise evidence-map summary.",
                inputs=SynthesizeExtractiveClaimsInput(),
                expected_outputs=["claims", "evidence-map"],
                acceptance_criteria=[
                    "at-least-one-claim",
                    "every-claim-has-verified-evidence",
                ],
            ),
        ],
    )


def mark_leased_job_started(session: Session, job_id: str, lease_token: str) -> None:
    job = session.get(JobRecord, job_id)
    if job is None or job.status != "leased" or job.lease_token != lease_token:
        raise WorkflowFailure(
            "job-lease-lost",
            "The background job lease is no longer valid.",
            retryable=True,
        )
    workflow = session.get(WorkflowRecord, job.workflow_id)
    if workflow is None:
        raise WorkflowFailure("workflow-missing", "The workflow record is missing.")
    if workflow.cancel_requested_at is not None:
        acknowledge_cancellation(session, job, workflow)
        session.commit()
        return
    if job.task_id is None:
        return
    task = session.get(TaskRecord, job.task_id)
    if task is None:
        raise WorkflowFailure("task-missing", "The workflow step record is missing.")
    if task.status == "queued":
        transition_task(session, task, "running")
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "step.started",
                    TaskEventData(
                        task_id=task.id,
                        step_key=task.step_key or "",
                        order_index=task.order_index or 0,
                        status="running",
                    ),
                    task.id,
                    job.id,
                )
            ],
        )
        session.commit()


def execute_leased_job(session: Session, job_id: str, lease_token: str) -> None:
    job = session.get(JobRecord, job_id)
    if job is None or job.status != "leased" or job.lease_token != lease_token:
        raise WorkflowFailure(
            "job-lease-lost",
            "The background job lease is no longer valid.",
            retryable=True,
        )
    workflow = session.get(WorkflowRecord, job.workflow_id)
    if workflow is None:
        raise WorkflowFailure("workflow-missing", "The workflow record is missing.")
    task = session.get(TaskRecord, job.task_id) if job.task_id is not None else None
    if workflow.cancel_requested_at is not None:
        acknowledge_cancellation(session, job, workflow, task)
        session.commit()
        return
    current_hash = current_job_input_hash(session, workflow, kind=job.kind, task=task)
    if current_hash != job.input_sha256:
        raise WorkflowFailure(
            "job-input-changed",
            "The workflow input changed after this job was queued.",
            outcome_unknown=False,
        )

    if job.kind == "generate-plan":
        _handle_generate_plan(session, workflow, job)
    elif job.kind == "execute-task" and task is not None:
        _handle_task(session, workflow, task, job)
    elif job.kind == "review-workflow":
        _handle_review(session, workflow, job)
    else:
        raise WorkflowFailure("unsupported-job-kind", "The workflow job type is unsupported.")
    # A cancellation may commit while a deterministic handler is doing a long
    # read-only calculation. Guard the final transaction with both the aggregate
    # revision and cancellation flag; on failure the whole handler transaction
    # rolls back and the worker's settlement path acknowledges cancellation.
    cancellation_guard = session.execute(
        update(WorkflowRecord)
        .where(
            WorkflowRecord.id == workflow.id,
            WorkflowRecord.row_version == workflow.row_version,
            WorkflowRecord.cancel_requested_at.is_(None),
        )
        .values(updated_at=WorkflowRecord.updated_at)
        .execution_options(synchronize_session=False)
    )
    if cancellation_guard.rowcount != 1:
        raise WorkflowFailure(
            "workflow-cancelled-during-job",
            "The workflow was cancelled before this job could publish its result.",
        )
    session.commit()


def _handle_generate_plan(
    session: Session, workflow: WorkflowRecord, job: JobRecord
) -> None:
    if workflow.status != "planning":
        raise WorkflowFailure(
            "workflow-not-planning",
            "The workflow is no longer in the planning phase.",
        )
    existing = session.scalar(
        select(PlanRecord)
        .where(PlanRecord.workflow_id == workflow.id)
        .order_by(PlanRecord.version.desc())
    )
    if existing is not None and existing.status == "pending-approval":
        assert_plan_integrity(existing)
        _finish_job(session, job, "succeeded")
        transition_workflow(session, workflow, "waiting-plan-approval")
        return
    version = (existing.version + 1) if existing is not None else 1
    spec = template_plan(workflow.goal)
    spec_json = spec.model_dump(mode="json", by_alias=True)
    plan = PlanRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        version=version,
        spec_json=spec_json,
        spec_sha256=content_sha256(spec_json),
        status="pending-approval",
        generator="template-v1",
        prompt_version="template-v1",
    )
    session.add(plan)
    session.flush()
    affected_resources = [f"project:{workflow.project_id}"]
    approval = ApprovalRecord(
        id=str(uuid.uuid4()),
        task_id=None,
        workflow_id=workflow.id,
        plan_id=plan.id,
        subject_type="plan",
        subject_id=plan.id,
        payload_schema_version="workflow-plan-approval-v1",
        row_version=1,
        intent_hash=plan_approval_hash(plan, affected_resources),
        requested_action="approve-research-plan",
        risk_level="low",
        reason="Approve the displayed immutable local literature plan before it runs.",
        affected_resources=affected_resources,
    )
    session.add(approval)
    _finish_job(session, job, "succeeded")
    transition_workflow(session, workflow, "waiting-plan-approval")
    append_workflow_events(
        session,
        workflow,
        [
            (
                "plan.generated",
                PlanEventData(
                    plan_id=plan.id,
                    version=plan.version,
                    plan_sha256=plan.spec_sha256,
                ),
                None,
                job.id,
            ),
            (
                "approval.requested",
                ApprovalEventData(
                    approval_id=approval.id,
                    subject_type="plan",
                    subject_id=plan.id,
                    action=approval.requested_action,
                    payload_sha256=approval.intent_hash,
                ),
                None,
                job.id,
            ),
        ],
    )


def _handle_task(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord,
    job: JobRecord,
) -> None:
    if workflow.status != "running" or task.status != "running":
        raise WorkflowFailure(
            "task-not-running",
            "The workflow step is no longer running.",
        )
    if task.task_type == "inspect-sources":
        outputs = _inspect_sources(session, workflow)
    elif task.task_type == "extract-local-evidence":
        outputs = _extract_local_evidence(session, workflow, task)
    elif task.task_type == "synthesize-extractive-claims":
        outputs = _synthesize_extractive_claims(session, workflow, task)
    else:
        raise WorkflowFailure("unsupported-task-type", "The workflow step type is unsupported.")
    task.outputs = outputs
    transition_task(session, task, "completed")
    _finish_job(session, job, "succeeded")
    output_count = max(
        (
            len(value)
            for value in outputs.values()
            if isinstance(value, list)
        ),
        default=0,
    )
    append_workflow_events(
        session,
        workflow,
        [
            (
                "step.completed",
                TaskEventData(
                    task_id=task.id,
                    step_key=task.step_key or "",
                    order_index=task.order_index or 0,
                    status="completed",
                    output_count=output_count,
                ),
                task.id,
                job.id,
            )
        ],
    )
    _advance_after_task(session, workflow, task)


def _inspect_sources(session: Session, workflow: WorkflowRecord) -> dict[str, Any]:
    project = session.get(ProjectRecord, workflow.project_id)
    if project is None:
        raise WorkflowFailure("project-missing", "The workflow project is missing.")
    project_root = Path(project.project_path).resolve()
    candidates = list(
        session.scalars(
            select(SourceRecord)
            .where(
                SourceRecord.project_id == workflow.project_id,
                SourceRecord.source_kind == "pdf",
                SourceRecord.ingestion_status == "ready",
            )
            .order_by(SourceRecord.created_at)
        )
    )
    selected: list[SourceRecord] = []
    for source in candidates:
        raw_path = Path(source.local_path)
        if raw_path.is_symlink():
            continue
        path = raw_path.resolve()
        try:
            path.relative_to(project_root)
        except ValueError:
            continue
        if not path.is_file() or sha256_file(path) != source.content_hash:
            continue
        selected.append(source)
    if not selected:
        raise WorkflowBlockedError(
            "no-ready-pdf",
            "Import and finish parsing at least one valid PDF before continuing.",
        )
    return {
        "sourceIds": [source.id for source in selected],
        "sourceContentHashes": {
            source.id: source.content_hash for source in selected
        },
    }


def _extract_local_evidence(
    session: Session, workflow: WorkflowRecord, task: TaskRecord
) -> dict[str, Any]:
    previous = _previous_task(session, task)
    source_ids = _string_list(previous.outputs.get("sourceIds"))
    if not source_ids:
        raise WorkflowFailure(
            "source-selection-missing",
            "The source inspection step did not produce a source selection.",
        )
    payload = ExtractLocalEvidenceInput.model_validate(task.inputs)
    pages = list(
        session.scalars(
            select(SourcePageRecord)
            .where(SourcePageRecord.source_id.in_(source_ids))
            .order_by(SourcePageRecord.source_id, SourcePageRecord.page_index)
        )
    )
    candidates = _rank_passages(payload.query, pages)
    selected = _select_diverse_passages(
        candidates,
        max_passages=payload.max_passages,
        max_per_source=payload.max_per_source,
    )
    pages_by_source: dict[str, list[PdfPage]] = defaultdict(list)
    for page in pages:
        pages_by_source[page.source_id].append(
            PdfPage(
                page_index=page.page_index,
                page_label=page.page_label,
                width=page.width,
                height=page.height,
                text=page.text,
                words=page.words,
            )
        )
    evidence_ids: list[str] = []
    seen: set[tuple[str, int, str]] = set()
    for candidate in selected:
        located = locate_quote(candidate.text, pages_by_source[candidate.source_id])
        if located is None or not located.verified:
            continue
        quote_hash = hashlib.sha256(located.text.encode("utf-8")).hexdigest()
        identity = (candidate.source_id, located.page_index, quote_hash)
        if identity in seen:
            continue
        seen.add(identity)
        evidence = EvidenceSpanRecord(
            id=str(uuid.uuid4()),
            source_id=candidate.source_id,
            page_index=located.page_index,
            page_label=located.page_label,
            text=located.text,
            bbox=located.bbox,
            coordinate_space="normalized-rotated-top-left-v1",
            quote_hash=quote_hash,
            extraction_method="local-token-overlap-v1",
            confidence=located.confidence,
            verified=True,
        )
        session.add(evidence)
        evidence_ids.append(evidence.id)
    if not evidence_ids:
        raise WorkflowBlockedError(
            "no-local-evidence",
            "No locally verifiable PDF passage matched the research goal.",
        )
    return {"evidenceIds": evidence_ids, "sourceIds": source_ids}


def _synthesize_extractive_claims(
    session: Session, workflow: WorkflowRecord, task: TaskRecord
) -> dict[str, Any]:
    existing = session.scalar(select(AnswerRecord).where(AnswerRecord.task_id == task.id))
    if existing is not None:
        claim_ids = list(
            session.scalars(select(ClaimRecord.id).where(ClaimRecord.answer_id == existing.id))
        )
        return {"answerId": existing.id, "claimIds": claim_ids}
    previous = _previous_task(session, task)
    evidence_ids = _string_list(previous.outputs.get("evidenceIds"))
    if not evidence_ids:
        raise WorkflowFailure(
            "evidence-selection-missing",
            "The evidence extraction step did not produce verified evidence.",
        )
    payload = SynthesizeExtractiveClaimsInput.model_validate(task.inputs)
    evidence_records = list(
        session.scalars(
            select(EvidenceSpanRecord)
            .where(EvidenceSpanRecord.id.in_(evidence_ids))
            .order_by(EvidenceSpanRecord.source_id, EvidenceSpanRecord.page_index)
        )
    )
    claim_candidates: list[tuple[str, EvidenceSpanRecord]] = []
    seen_statements: set[str] = set()
    for evidence in evidence_records:
        statement = _atomic_statement(evidence.text)
        normalized = " ".join(statement.lower().split())
        if len(statement) < 20 or normalized in seen_statements:
            continue
        seen_statements.add(normalized)
        claim_candidates.append((statement, evidence))
        if len(claim_candidates) >= payload.max_claims:
            break
    if not claim_candidates:
        raise WorkflowBlockedError(
            "no-atomic-claims",
            "Verified passages were found, but no bounded extractive claim could be formed.",
        )
    source_count = len({evidence.source_id for _, evidence in claim_candidates})
    summary = (
        f"Evidence map: {len(claim_candidates)} extractive claim"
        f"{'s' if len(claim_candidates) != 1 else ''} across {source_count} local PDF source"
        f"{'s' if source_count != 1 else ''}. Claims preserve source wording and add no causal inference."
    )
    answer = AnswerRecord(
        id=str(uuid.uuid4()),
        project_id=workflow.project_id,
        workflow_id=workflow.id,
        task_id=task.id,
        question=workflow.goal,
        answer=summary,
        unresolved_questions=[
            "This first workflow is extractive; broader semantic synthesis requires separate model review."
        ],
    )
    session.add(answer)
    claim_ids: list[str] = []
    for statement, evidence in claim_candidates:
        claim = ClaimRecord(
            id=str(uuid.uuid4()),
            answer_id=answer.id,
            statement=statement,
            claim_type="finding",
            confidence=evidence.confidence,
            review_status="unreviewed",
        )
        session.add(claim)
        session.add(
            ClaimEvidenceRecord(
                claim_id=claim.id,
                evidence_id=evidence.id,
                relationship_kind="supporting",
            )
        )
        claim_ids.append(claim.id)
    return {"answerId": answer.id, "claimIds": claim_ids}


def _handle_review(session: Session, workflow: WorkflowRecord, job: JobRecord) -> None:
    if workflow.status != "reviewing":
        raise WorkflowFailure(
            "workflow-not-reviewing",
            "The workflow is no longer ready for deterministic review.",
        )
    plan = session.scalar(
        select(PlanRecord).where(
            PlanRecord.workflow_id == workflow.id,
            PlanRecord.status == "approved",
        )
    )
    answer = session.scalar(
        select(AnswerRecord).where(AnswerRecord.workflow_id == workflow.id)
    )
    checks: list[ReviewCheck] = []
    claim_results: list[ClaimReviewResult] = []
    required_revisions: list[str] = []
    if plan is None:
        raise WorkflowFailure("approved-plan-missing", "The approved workflow plan is missing.")
    if answer is None or answer.task_id is None or answer.project_id != workflow.project_id:
        checks.append(
            ReviewCheck(
                code="answer-workflow-ownership",
                status="failed",
                message="No workflow-owned answer was produced.",
            )
        )
        required_revisions.append("Produce a workflow-owned answer before review.")
        claims: list[ClaimRecord] = []
    else:
        answer_task = session.get(TaskRecord, answer.task_id)
        ownership_ok = (
            answer.workflow_id == workflow.id
            and answer_task is not None
            and answer_task.workflow_id == workflow.id
            and answer_task.project_id == workflow.project_id
            and answer_task.plan_id == plan.id
        )
        checks.append(
            ReviewCheck(
                code="answer-workflow-ownership",
                status="passed" if ownership_ok else "failed",
                message=(
                    "Answer provenance belongs to this workflow and task."
                    if ownership_ok
                    else "Answer provenance does not belong to this workflow and task."
                ),
            )
        )
        if not ownership_ok:
            required_revisions.append("Repair answer workflow/task provenance.")
        claims = list(
            session.scalars(
                select(ClaimRecord).where(ClaimRecord.answer_id == answer.id)
            )
        )
    checks.append(
        ReviewCheck(
            code="claims-present",
            status="passed" if claims else "failed",
            message="At least one atomic claim exists." if claims else "No atomic claim exists.",
        )
    )
    if not claims:
        required_revisions.append("Produce at least one atomic extractive claim.")

    all_supported = bool(claims)
    for claim in claims:
        links = list(
            session.scalars(
                select(ClaimEvidenceRecord).where(ClaimEvidenceRecord.claim_id == claim.id)
            )
        )
        evidence_ids: list[str] = []
        relationships: list[str] = []
        claim_supported = bool(links)
        if not links:
            checks.append(
                ReviewCheck(
                    code="claim-evidence-link",
                    status="failed",
                    message="Claim has no evidence relationship.",
                    claim_id=claim.id,
                )
            )
        for link in links:
            evidence = session.get(EvidenceSpanRecord, link.evidence_id)
            evidence_ids.append(link.evidence_id)
            relationships.append(link.relationship_kind)
            relationship_ok = link.relationship_kind in {"supporting", "contradicting"}
            evidence_ok = False
            if evidence is not None:
                source = session.get(SourceRecord, evidence.source_id)
                page = session.scalar(
                    select(SourcePageRecord).where(
                        SourcePageRecord.source_id == evidence.source_id,
                        SourcePageRecord.page_index == evidence.page_index,
                    )
                )
                quote_ok = evidence.quote_hash == hashlib.sha256(
                    evidence.text.encode("utf-8")
                ).hexdigest()
                page_ok = page is not None and _normalized_contains(page.text, evidence.text)
                claim_is_extractive = _normalized_contains(evidence.text, claim.statement)
                evidence_ok = bool(
                    source is not None
                    and source.project_id == workflow.project_id
                    and page_ok
                    and quote_ok
                    and claim_is_extractive
                    and evidence.verified
                    and relationship_ok
                    and link.relationship_kind == "supporting"
                )
            checks.append(
                ReviewCheck(
                    code="evidence-integrity",
                    status="passed" if evidence_ok else "failed",
                    message=(
                        "Evidence relationship, source, page, text, and quote hash are valid."
                        if evidence_ok
                        else "Evidence failed relationship, ownership, page, text, or quote-hash validation."
                    ),
                    claim_id=claim.id,
                    evidence_id=link.evidence_id,
                )
            )
            claim_supported = claim_supported and evidence_ok
        claim.review_status = "verified" if claim_supported else "unreviewed"
        claim_results.append(
            ClaimReviewResult(
                claim_id=claim.id,
                status="supported" if claim_supported else "insufficient-evidence",
                evidence_ids=evidence_ids,
                relationships=[
                    "contradicting" if item == "contradicting" else "supporting"
                    for item in relationships
                ],
            )
        )
        if not claim_supported:
            all_supported = False
            required_revisions.append(
                f"Add valid, project-owned evidence for claim {claim.id}."
            )

    verdict = "passed" if all_supported and all(
        check.status == "passed" for check in checks
    ) else "revision-required"
    result = DeterministicReviewResult(
        verdict=verdict,
        checks=checks,
        claim_results=claim_results,
        required_revisions=list(dict.fromkeys(required_revisions)),
    )
    review = ReviewRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        plan_id=plan.id,
        task_id=answer.task_id if answer is not None else None,
        review_type="deterministic-claims-v1",
        input_sha256=job.input_sha256,
        verdict=verdict,
        result_json=result.model_dump(mode="json", by_alias=True),
    )
    session.add(review)
    _finish_job(session, job, "succeeded")
    append_workflow_events(
        session,
        workflow,
        [
            (
                "review.completed",
                ReviewEventData(
                    review_id=review.id,
                    verdict=verdict,
                    claim_count=len(claims),
                ),
                None,
                job.id,
            )
        ],
    )
    if verdict == "passed":
        transition_workflow(session, workflow, "completed")
    else:
        transition_workflow(
            session,
            workflow,
            "blocked",
            reason_code="review-required",
            blocking_message="Deterministic review found unsupported or invalid evidence links.",
        )


def _advance_after_task(
    session: Session, workflow: WorkflowRecord, completed_task: TaskRecord
) -> None:
    tasks = list(
        session.scalars(
            select(TaskRecord)
            .where(TaskRecord.plan_id == completed_task.plan_id)
            .order_by(TaskRecord.order_index)
        )
    )
    next_task = next((task for task in tasks if task.status == "pending"), None)
    if next_task is not None:
        transition_task(session, next_task, "queued")
        next_job = enqueue_job(
            session,
            workflow,
            kind="execute-task",
            task=next_task,
            operation_key=f"workflow:{workflow.id}:task:{next_task.id}",
        )
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "step.queued",
                    TaskEventData(
                        task_id=next_task.id,
                        step_key=next_task.step_key or "",
                        order_index=next_task.order_index or 0,
                        status="queued",
                    ),
                    next_task.id,
                    next_job.id,
                )
            ],
        )
        return
    if not tasks or any(task.status != "completed" for task in tasks):
        raise WorkflowFailure(
            "workflow-step-gap",
            "The sequential workflow cannot identify its next step.",
        )
    transition_workflow(session, workflow, "reviewing")
    enqueue_job(
        session,
        workflow,
        kind="review-workflow",
        operation_key=f"workflow:{workflow.id}:review:{completed_task.plan_id}",
    )


def settle_leased_job_error(
    session: Session,
    job_id: str,
    lease_token: str,
    error: WorkflowBlockedError | WorkflowFailure | Exception,
) -> None:
    job = session.get(JobRecord, job_id)
    if job is None or job.status != "leased" or job.lease_token != lease_token:
        session.rollback()
        return
    workflow = session.get(WorkflowRecord, job.workflow_id)
    task = session.get(TaskRecord, job.task_id) if job.task_id is not None else None
    if workflow is None:
        _finish_job(session, job, "failed", "workflow-missing", str(error))
        session.commit()
        return
    if workflow.cancel_requested_at is not None:
        acknowledge_cancellation(session, job, workflow, task)
        session.commit()
        return
    if isinstance(error, WorkflowBlockedError):
        _finish_job(session, job, "failed", error.code, error.user_message)
        if task is not None and task.status in {"queued", "running"}:
            transition_task(session, task, "blocked")
        transition_workflow(
            session,
            workflow,
            "blocked",
            reason_code=error.code,
            blocking_message=error.user_message,
            retryable=error.code == "no-ready-pdf",
        )
        _append_failed_task_event(session, workflow, task, job, error.code)
        session.commit()
        return
    failure = error if isinstance(error, WorkflowFailure) else WorkflowFailure(
        "workflow-handler-error",
        "The local workflow handler failed unexpectedly. Retry after checking the project inputs.",
        retryable=False,
    )
    _finish_job(session, job, "failed", failure.code, failure.user_message)
    retryable = (
        failure.retryable
        and not failure.outcome_unknown
        and job.attempt < MAX_JOB_ATTEMPTS
    )
    if retryable:
        if task is not None and task.status == "running":
            # The handler transaction rolled back; in case start was committed,
            # return the deterministic step to queued for the next attempt.
            task.status = "failed"
            task.finished_at = utc_now()
            task.retries += 1
            session.flush()
            transition_task(session, task, "queued")
        enqueue_job(
            session,
            workflow,
            kind=job.kind,
            task=task,
            operation_key=job.operation_key,
            attempt=job.attempt + 1,
            previous_job_id=job.id,
            delay_seconds=retry_delay_seconds(job.attempt + 1),
        )
        session.commit()
        return
    if task is not None and task.status in {"queued", "running"}:
        transition_task(session, task, "failed")
    workflow.last_error_code = failure.code
    workflow.last_error_message = failure.user_message[:2_000]
    if failure.outcome_unknown:
        transition_workflow(
            session,
            workflow,
            "blocked",
            reason_code="execution-outcome-unknown",
            blocking_message="Execution outcome is unknown; reconcile the existing run before retrying.",
        )
    else:
        transition_workflow(session, workflow, "failed", reason_code=failure.code)
    _append_failed_task_event(session, workflow, task, job, failure.code)
    session.commit()


def acknowledge_cancellation(
    session: Session,
    job: JobRecord,
    workflow: WorkflowRecord,
    task: TaskRecord | None = None,
) -> None:
    _finish_job(session, job, "cancelled")
    now = utc_now()
    session.execute(
        update(JobRecord)
        .where(
            JobRecord.workflow_id == workflow.id,
            JobRecord.status == "queued",
        )
        .values(status="cancelled", finished_at=now, updated_at=now)
    )
    session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.workflow_id == workflow.id,
            TaskRecord.status.in_(["pending", "queued", "running", "blocked", "failed"]),
        )
        .values(status="cancelled", finished_at=now, updated_at=now)
    )
    if workflow.status != "cancelled":
        transition_workflow(session, workflow, "cancelled")


def _finish_job(
    session: Session,
    job: JobRecord,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    now = utc_now()
    result = session.execute(
        update(JobRecord)
        .where(
            JobRecord.id == job.id,
            JobRecord.status == "leased",
            JobRecord.lease_token == job.lease_token,
        )
        .values(
            status=status,
            error_code=error_code,
            error_message=error_message[:2_000] if error_message else None,
            finished_at=now,
            updated_at=now,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise WorkflowFailure(
            "job-lease-lost",
            "The job lease expired before its result could be saved.",
            retryable=True,
        )
    session.flush()


def _append_failed_task_event(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord | None,
    job: JobRecord,
    error_code: str,
) -> None:
    if task is None:
        return
    append_workflow_events(
        session,
        workflow,
        [
            (
                "step.failed",
                TaskEventData(
                    task_id=task.id,
                    step_key=task.step_key or "",
                    order_index=task.order_index or 0,
                    status="failed" if task.status == "failed" else "blocked",
                    error_code=error_code,
                ),
                task.id,
                job.id,
            )
        ],
    )


def _previous_task(session: Session, task: TaskRecord) -> TaskRecord:
    previous = session.scalar(
        select(TaskRecord)
        .where(
            TaskRecord.plan_id == task.plan_id,
            TaskRecord.order_index == (task.order_index or 0) - 1,
        )
    )
    if previous is None or previous.status != "completed":
        raise WorkflowFailure(
            "previous-step-incomplete",
            "The preceding sequential workflow step is not complete.",
        )
    return previous


def _string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _terms(text: str) -> set[str]:
    lowered = text.lower()
    terms = {
        item
        for item in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", lowered)
        if item not in _ENGLISH_STOPWORDS
    }
    for run in re.findall(r"[\u3400-\u9fff]+", lowered):
        if len(run) == 1:
            terms.add(run)
        else:
            terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms


def _sentence_candidates(text: str) -> Iterable[str]:
    normalized = re.sub(r"[ \t\r\f\v]+", " ", text)
    for item in re.split(r"(?<=[.!?。！？])\s+|\n+", normalized):
        candidate = item.strip()
        if 30 <= len(candidate) <= 1_200:
            yield candidate


def _rank_passages(
    query: str, pages: Iterable[SourcePageRecord]
) -> list[PassageCandidate]:
    query_terms = _terms(query)
    if not query_terms:
        raise WorkflowBlockedError(
            "query-has-no-search-terms",
            "The research goal contains no usable local-search terms.",
        )
    candidates: list[PassageCandidate] = []
    for page in pages:
        for sentence in _sentence_candidates(page.text):
            sentence_terms = _terms(sentence)
            overlap = query_terms.intersection(sentence_terms)
            if not overlap:
                continue
            coverage = len(overlap) / len(query_terms)
            density = len(overlap) / max(1, len(sentence_terms))
            score = coverage * 0.8 + density * 0.2
            candidates.append(
                PassageCandidate(
                    source_id=page.source_id,
                    page=page,
                    text=sentence,
                    score=score,
                )
            )
    return sorted(candidates, key=lambda item: (-item.score, item.source_id, item.page.page_index))


def _select_diverse_passages(
    candidates: list[PassageCandidate], *, max_passages: int, max_per_source: int
) -> list[PassageCandidate]:
    by_source: dict[str, list[PassageCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_source[candidate.source_id].append(candidate)
    selected: list[PassageCandidate] = []
    seen: set[tuple[str, int, str]] = set()

    def add(candidate: PassageCandidate) -> bool:
        key = (candidate.source_id, candidate.page.page_index, candidate.text)
        if key in seen:
            return False
        source_count = sum(item.source_id == candidate.source_id for item in selected)
        if source_count >= max_per_source:
            return False
        seen.add(key)
        selected.append(candidate)
        return True

    # Give every source one opportunity before filling remaining slots by score.
    for source_id in sorted(by_source):
        if by_source[source_id] and len(selected) < max_passages:
            add(by_source[source_id][0])
    for candidate in candidates:
        if len(selected) >= max_passages:
            break
        add(candidate)
    return selected


def _atomic_statement(text: str) -> str:
    for candidate in _sentence_candidates(text):
        return candidate[:800].strip()
    return " ".join(text.split())[:800].strip()


def _normalized_contains(haystack: str, needle: str) -> bool:
    def normalize(value: str) -> str:
        return " ".join(value.casefold().split())

    return normalize(needle) in normalize(haystack)
