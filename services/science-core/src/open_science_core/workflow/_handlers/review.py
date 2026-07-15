from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    AnswerRecord,
    ClaimEvidenceRecord,
    ClaimRecord,
    EventRecord,
    EvidenceSpanRecord,
    JobRecord,
    PlanRecord,
    ReviewRecord,
    SourcePageRecord,
    SourceRecord,
    TaskRecord,
    WorkflowRecord,
)
from ...pdf import PdfPage, locate_quote
from ..schemas import (
    ClaimReviewResult,
    DeterministicReviewResult,
    ReviewCheck,
    ReviewEventData,
)
from ..service import (
    WorkflowConflict,
    append_workflow_events,
    assert_approved_plan_for_workflow,
    assert_plan_approval_integrity,
    assert_task_matches_approved_plan,
    build_workflow_result,
    transition_workflow,
    workflow_result_hash,
)
from ..state import WorkflowFailure
from .dataset import handle_dataset_review
from .lifecycle import finish_job, workflow_failure_from_conflict
from .sources import validated_source_descriptors_for_task
from .synthesis import (
    LOCAL_SYNTHESIS_PROMPT_VERSION,
    REMOTE_SYNTHESIS_PROMPT_VERSION,
    answer_summary_matches,
)
from .text import is_exact_atomic_sentence, normalized_contains


def handle_review(
    session: Session,
    workflow: WorkflowRecord,
    job: JobRecord,
    *,
    legacy_handler: bool,
) -> None:
    if workflow.workflow_type == "dataset-analysis":
        handle_dataset_review(session, workflow, job)
        return
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
    assert_current_review_contract(
        session,
        workflow,
        plan,
        legacy_handler=legacy_handler,
    )
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
            and answer.question == workflow.goal
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

    if not legacy_handler:
        summary_ok = bool(
            answer is not None and answer_summary_matches(session, answer, claims)
        )
        checks.append(
            ReviewCheck(
                code="answer-extractive-summary",
                status="passed" if summary_ok else "failed",
                message=(
                    "The answer summary is a deterministic rendering of its "
                    "evidence-linked claims."
                    if summary_ok
                    else "The answer summary contains content outside its ordered "
                    "evidence-linked claims."
                ),
            )
        )
        if not summary_ok:
            required_revisions.append(
                "Regenerate the summary only from ordered, evidence-linked exact claim "
                "sentences."
            )

        recorded_remote_approval = session.scalar(
            select(EventRecord)
            .where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.event_type == "remote-data.approved",
            )
            .order_by(EventRecord.sequence)
        )
        recorded_remote_payload = (
            recorded_remote_approval.payload
            if recorded_remote_approval is not None
            else {}
        )
        provenance_ok = bool(
            answer is not None
            and (
                (
                    workflow.generation_mode == "local-deterministic"
                    and answer.generator == "local-extractive-v1"
                    and answer.model is None
                    and answer.prompt_version == LOCAL_SYNTHESIS_PROMPT_VERSION
                    and answer.metadata_json.get("generationMode")
                    == "local-deterministic"
                )
                or (
                    workflow.generation_mode == "remote-model-assisted"
                    and answer.generator == "remote-model-assisted-v1"
                    and answer.model == plan.model
                    and answer.prompt_version == REMOTE_SYNTHESIS_PROMPT_VERSION
                    and answer.metadata_json.get("generationMode")
                    == "remote-model-assisted"
                    and answer.metadata_json.get("endpointHost")
                    == recorded_remote_payload.get("endpointHost")
                    and answer.metadata_json.get("endpointIdentity")
                    == recorded_remote_payload.get("endpointIdentity")
                    and answer.model == recorded_remote_payload.get("model")
                )
            )
        )
        checks.append(
            ReviewCheck(
                code="answer-generation-provenance",
                status="passed" if provenance_ok else "failed",
                message=(
                    "Answer generation provenance matches the approved workflow mode."
                    if provenance_ok
                    else "Answer generation provenance does not match the approved "
                    "workflow mode."
                ),
            )
        )
        if not provenance_ok:
            required_revisions.append("Repair the answer generation provenance.")

    all_supported = bool(claims)
    for claim in claims:
        links = list(
            session.scalars(
                select(ClaimEvidenceRecord).where(ClaimEvidenceRecord.claim_id == claim.id)
            )
        )
        evidence_ids: list[str] = []
        relationships: list[str] = []
        claim_shape_ok = claim.claim_type == "finding" and 0.0 <= claim.confidence <= 1.0
        checks.append(
            ReviewCheck(
                code="claim-materialization",
                status="passed" if claim_shape_ok else "failed",
                message=(
                    "Claim type and confidence match the extractive result contract."
                    if claim_shape_ok
                    else "Claim type or confidence is outside the extractive result contract."
                ),
                claim_id=claim.id,
            )
        )
        claim_supported = bool(links) and claim_shape_ok
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
                page_ok = page is not None and normalized_contains(page.text, evidence.text)
                located = (
                    locate_quote(
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
                    if page is not None
                    else None
                )
                location_ok = bool(
                    located is not None
                    and located.verified
                    and evidence.page_label == located.page_label
                    and evidence.bbox == located.bbox
                    and evidence.coordinate_space
                    == "normalized-rotated-top-left-v1"
                    and evidence.extraction_method == "local-token-overlap-v1"
                    and abs(evidence.confidence - located.confidence) < 1e-12
                    and abs(claim.confidence - evidence.confidence) < 1e-12
                )
                claim_is_extractive = (
                    normalized_contains(evidence.text, claim.statement)
                    if legacy_handler
                    else is_exact_atomic_sentence(evidence.text, claim.statement)
                )
                evidence_ok = bool(
                    source is not None
                    and source.project_id == workflow.project_id
                    and page_ok
                    and location_ok
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
    result_arguments: dict[str, Any] = {
        "schema_version": "1" if legacy_handler else "2",
        "verdict": verdict,
        "checks": checks,
        "claim_results": claim_results,
        "required_revisions": list(dict.fromkeys(required_revisions)),
    }
    if not legacy_handler:
        result_snapshot = build_workflow_result(
            session,
            workflow,
            integrity_status="verified-frozen-v2",
            review_completed=True,
        )
        if result_snapshot is None:
            raise WorkflowFailure(
                "review-result-missing",
                "Deterministic review could not materialize the workflow result.",
            )
        result_arguments.update(
            {
                "result_snapshot_sha256": workflow_result_hash(result_snapshot),
                "result_snapshot": result_snapshot,
            }
        )
    result = DeterministicReviewResult(**result_arguments)
    result_json = result.model_dump(
        mode="json",
        by_alias=True,
        exclude=(
            {"schema_version", "result_snapshot_sha256", "result_snapshot"}
            if legacy_handler
            else None
        ),
    )
    review = ReviewRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        plan_id=plan.id,
        task_id=answer.task_id if answer is not None else None,
        review_type=(
            "deterministic-claims-v1" if legacy_handler else "deterministic-claims-v2"
        ),
        input_sha256=job.input_sha256,
        verdict=verdict,
        result_json=result_json,
    )
    session.add(review)
    finish_job(session, job, "succeeded")
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


def assert_current_review_contract(
    session: Session,
    workflow: WorkflowRecord,
    plan: PlanRecord,
    *,
    legacy_handler: bool,
) -> None:
    try:
        spec = assert_approved_plan_for_workflow(workflow, plan)
        assert_plan_approval_integrity(session, workflow, plan)
        tasks = list(
            session.scalars(
                select(TaskRecord)
                .where(TaskRecord.plan_id == plan.id)
                .order_by(TaskRecord.order_index)
            )
        )
        if len(tasks) != len(spec.steps) or any(
            task.status != "completed" for task in tasks
        ):
            raise WorkflowConflict(
                "review-task-set-invalid",
                "Deterministic review requires every approved plan step to be complete.",
            )
        for task in tasks:
            assert_task_matches_approved_plan(workflow, plan, task)
        validated_source_descriptors_for_task(
            session,
            workflow,
            tasks[-1],
            inspect_task=tasks[0],
            allow_legacy_upgrade=legacy_handler,
        )
    except WorkflowConflict as error:
        raise workflow_failure_from_conflict(error) from None
