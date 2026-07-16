from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Sequence, cast

from pydantic import ValidationError
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from ...analysis import sha256_file
from ...analysis_service import (
    AnalysisServiceError,
    analysis_run_out,
    validate_workflow_analysis_intent,
)
from ...dataset_inspector import dataset_profile_sha256
from ...models import (
    AnalysisIntentRecord,
    AnswerRecord,
    ApprovalRecord,
    ClaimEvidenceRecord,
    ClaimRecord,
    EventRecord,
    EvidenceSpanRecord,
    JobRecord,
    PlanRecord,
    ProjectRecord,
    ReviewRecord,
    RunRecord,
    SourcePageRecord,
    SourceRecord,
    TaskRecord,
    WorkflowRecord,
)
from ...schemas import BoundingBoxOut
from ..schemas import (
    AllowedAction,
    AnalysisApprovalEventData,
    AnalysisErrorSummaryOut,
    AnalysisExecutionPendingApprovalOut,
    BlockingReasonOut,
    ClaimSupportStatus,
    DatasetAnalysisReviewResult,
    DatasetPlanPendingApprovalOut,
    DatasetProfile,
    DatasetReviewWarningAcceptanceOut,
    DatasetReviewWarningsAcceptedEventData,
    DeterministicReviewResult,
    EvidenceRelationshipOut,
    FrozenSourceDescriptor,
    GenerationMode,
    MaterializedStepOut,
    PendingApprovalOut,
    PlanSnapshotOut,
    PlanStatus,
    ResearchWorkflowSnapshot,
    ReviewEventData,
    ReviewSnapshotOut,
    ReviewType,
    ReviewVerdict,
    TaskStatus,
    TaskStepType,
    WorkflowAnalysisArtifactOut,
    WorkflowAnalysisIntentOut,
    WorkflowAnalysisRunOut,
    WorkflowClaimOut,
    WorkflowPendingApprovalOut,
    WorkflowResultOut,
    WorkflowRiskLevel,
    WorkflowStateOut,
    WorkflowStatus,
)
from .integrity import (
    LEGACY_HANDLER_VERSIONS,
    REVIEW_HANDLER_VERSION,
    WorkflowConflict,
    assert_plan_approval_integrity,
    assert_plan_for_workflow,
    content_sha256,
)
from .jobs import job_input_hash_for_handler_version


def _event_payload_mapping(event: EventRecord) -> dict[str, Any] | None:
    return _mapping_or_none(event.payload)


def _mapping_or_none(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, Any], value)


def task_output_summary(task: TaskRecord) -> str | None:
    if not task.outputs:
        return None
    for key, noun in (
        ("sourceIds", "source"),
        ("evidenceIds", "evidence passage"),
        ("claimIds", "claim"),
    ):
        value = task.outputs.get(key)
        if isinstance(value, list):
            values = cast(list[object], value)
            suffix = "" if len(values) == 1 else "s"
            return f"{len(values)} {noun}{suffix}"
    return "Output recorded"


def allowed_actions(
    workflow: WorkflowRecord,
    pending_approvals: Sequence[ApprovalRecord],
    jobs: Sequence[JobRecord],
    *,
    review: ReviewRecord | None = None,
    review_warnings_accepted: bool = False,
) -> list[AllowedAction]:
    if workflow.cancel_requested_at is not None and workflow.status != "cancelled":
        return []
    actions: list[AllowedAction] = []
    if workflow.status == "waiting-plan-approval" and pending_approvals:
        actions.append("approve-plan")
    if (
        workflow.workflow_type == "dataset-analysis"
        and workflow.status == "running"
        and any(
            approval.subject_type == "analysis-intent"
            for approval in pending_approvals
        )
    ):
        actions.extend(["approve-analysis", "reject-analysis"])
    if (
        workflow.workflow_type == "dataset-analysis"
        and workflow.status == "reviewing"
        and review is not None
        and review.verdict == "passed-with-warnings"
        and not review_warnings_accepted
    ):
        actions.append("accept-review-warnings")
    if workflow.status not in {"completed", "cancelled"}:
        actions.append("cancel")
    if workflow.status == "failed" and any(job.status == "failed" for job in jobs):
        actions.append("retry")
    if workflow.status == "blocked" and workflow.blocking_code in {
        "no-ready-pdf",
        "analysis-execution-rejected",
        "analysis-repair-not-safe",
        "analysis-repair-limit-exceeded",
    }:
        actions.append("resume")
    return actions


def result_source_descriptors(
    session: Session,
    workflow: WorkflowRecord,
) -> list[FrozenSourceDescriptor]:
    inspect_task = session.scalar(
        select(TaskRecord).where(
            TaskRecord.workflow_id == workflow.id,
            TaskRecord.order_index == 0,
        )
    )
    raw_descriptors = (
        inspect_task.outputs.get("sourceDescriptors") if inspect_task is not None else None
    )
    if not isinstance(raw_descriptors, list):
        return []
    try:
        return [
            FrozenSourceDescriptor.model_validate(item)
            for item in cast(list[Any], raw_descriptors)
        ]
    except ValidationError:
        return []


def source_page_manifest_hash(
    session: Session,
    source_id: str,
) -> tuple[str, int] | None:
    pages = list(
        session.scalars(
            select(SourcePageRecord)
            .where(SourcePageRecord.source_id == source_id)
            .order_by(SourcePageRecord.page_index)
        )
    )
    if not pages:
        return None
    return (
        content_sha256(
            [
                {
                    "height": page.height,
                    "pageIndex": page.page_index,
                    "pageLabel": page.page_label,
                    "text": page.text,
                    "width": page.width,
                    "words": page.words,
                }
                for page in pages
            ]
        ),
        len(pages),
    )


def assert_result_sources_current(
    session: Session,
    workflow: WorkflowRecord,
    descriptors: list[FrozenSourceDescriptor],
) -> None:
    project = session.get(ProjectRecord, workflow.project_id)
    if (
        project is None
        or not descriptors
        or len({item.source_id for item in descriptors}) != len(descriptors)
    ):
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The reviewed result no longer has a verifiable immutable source set.",
        )
    project_root = Path(project.project_path).resolve()
    for descriptor in descriptors:
        source = session.get(SourceRecord, descriptor.source_id)
        page_manifest = (
            source_page_manifest_hash(session, descriptor.source_id)
            if source is not None
            else None
        )
        file_matches = False
        if source is not None:
            raw_path = Path(source.local_path)
            if not raw_path.is_symlink():
                try:
                    path = raw_path.resolve(strict=True)
                    path.relative_to(project_root)
                    file_matches = path.is_file() and sha256_file(path) == descriptor.content_hash
                except (OSError, ValueError):
                    file_matches = False
        if not (
            source is not None
            and source.project_id == workflow.project_id
            and source.source_kind == "pdf"
            and source.ingestion_status == "ready"
            and source.title == descriptor.title
            and source.content_hash == descriptor.content_hash
            and page_manifest is not None
            and page_manifest[0] == descriptor.page_manifest_hash
            and source.page_count in {None, page_manifest[1]}
            and file_matches
        ):
            raise WorkflowConflict(
                "workflow-result-integrity-failed",
                "A reviewed citation source no longer matches its frozen file and page "
                "fingerprints.",
            )


def build_workflow_result(
    session: Session,
    workflow: WorkflowRecord,
    *,
    integrity_status: Literal["verified-frozen-v2", "unfrozen"] = "unfrozen",
    review_completed: bool = False,
) -> WorkflowResultOut | None:
    answer = session.scalar(
        select(AnswerRecord)
        .where(AnswerRecord.workflow_id == workflow.id)
        .order_by(AnswerRecord.created_at.desc())
    )
    if answer is None:
        return None
    source_descriptors = {
        descriptor.source_id: descriptor
        for descriptor in result_source_descriptors(session, workflow)
    }
    claims = list(
        session.scalars(
            select(ClaimRecord).where(ClaimRecord.answer_id == answer.id).order_by(ClaimRecord.id)
        )
    )
    claim_order = answer.metadata_json.get("claimOrder")
    if isinstance(claim_order, list):
        ordered_ids = [
            item for item in cast(list[object], claim_order) if isinstance(item, str)
        ]
        claims_by_id = {claim.id: claim for claim in claims}
        if (
            len(ordered_ids) == len(claims)
            and len(set(ordered_ids)) == len(ordered_ids)
            and set(ordered_ids) == set(claims_by_id)
        ):
            claims = [claims_by_id[claim_id] for claim_id in ordered_ids]
    claim_outputs: list[WorkflowClaimOut] = []
    for claim in claims:
        links = list(
            session.scalars(
                select(ClaimEvidenceRecord)
                .where(ClaimEvidenceRecord.claim_id == claim.id)
                .order_by(
                    ClaimEvidenceRecord.evidence_id,
                    ClaimEvidenceRecord.relationship_kind,
                )
            )
        )
        evidence_outputs: list[EvidenceRelationshipOut] = []
        for link in links:
            evidence = session.get(EvidenceSpanRecord, link.evidence_id)
            if evidence is None:
                continue
            relationship = (
                "contradicting" if link.relationship_kind == "contradicting" else "supporting"
            )
            descriptor = source_descriptors.get(evidence.source_id)
            evidence_outputs.append(
                EvidenceRelationshipOut(
                    evidence_id=evidence.id,
                    source_id=evidence.source_id,
                    source_title=(descriptor.title if descriptor is not None else None),
                    source_content_hash=(
                        descriptor.content_hash if descriptor is not None else None
                    ),
                    source_page_manifest_hash=(
                        descriptor.page_manifest_hash if descriptor is not None else None
                    ),
                    page_index=evidence.page_index,
                    page_label=evidence.page_label,
                    text=evidence.text,
                    bbox=(
                        BoundingBoxOut.model_validate(evidence.bbox)
                        if evidence.bbox is not None
                        else None
                    ),
                    coordinate_space=cast(
                        Literal["normalized-rotated-top-left-v1"],
                        evidence.coordinate_space,
                    ),
                    quote_hash=evidence.quote_hash,
                    extraction_method=evidence.extraction_method,
                    confidence=evidence.confidence,
                    verified=evidence.verified,
                    relationship=relationship,
                )
            )
        support_status = {
            "verified": "supported",
            "rejected": "contradicted",
        }.get(
            claim.review_status,
            "insufficient-evidence" if review_completed else "pending-review",
        )
        claim_outputs.append(
            WorkflowClaimOut(
                id=claim.id,
                statement=claim.statement,
                support_status=cast(ClaimSupportStatus, support_status),
                confidence=claim.confidence,
                evidence=evidence_outputs,
            )
        )
    return WorkflowResultOut(
        answer_id=answer.id,
        summary=answer.answer,
        generator=answer.generator,
        model=answer.model,
        prompt_version=answer.prompt_version,
        integrity_status=integrity_status,
        claims=claim_outputs,
        unresolved_questions=answer.unresolved_questions,
    )


def workflow_result_hash(result: WorkflowResultOut) -> str:
    return content_sha256(result.model_dump(mode="json", by_alias=True, exclude_none=False))


def validated_review_result(
    session: Session,
    workflow: WorkflowRecord,
    plan: PlanRecord | None,
    review: ReviewRecord | None,
) -> DeterministicReviewResult | None:
    if review is None:
        if workflow.status == "completed":
            raise WorkflowConflict(
                "workflow-result-integrity-failed",
                "The completed workflow has no deterministic review result.",
            )
        return None
    try:
        result = DeterministicReviewResult.model_validate(review.result_json)
    except ValidationError:
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The stored deterministic review result is invalid.",
        ) from None
    expected_schema = {
        "deterministic-claims-v1": "1",
        "deterministic-claims-v2": "2",
    }.get(review.review_type)
    if expected_schema is None or result.schema_version != expected_schema:
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The deterministic review type does not match its result schema.",
        )
    if review.verdict != result.verdict:
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The deterministic review verdict does not match its stored result.",
        )
    if workflow.status == "completed" and result.verdict != "passed":
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The completed workflow is not bound to a passed deterministic review.",
        )
    if (
        plan is None
        or review.plan_id != plan.id
        or plan.workflow_id != workflow.id
        or plan.status != "approved"
    ):
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The deterministic review is not bound to the approved workflow plan.",
        )
    matching_events = [
        event
        for event in session.scalars(
            select(EventRecord).where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.event_type == "review.completed",
            )
        )
        if (payload := _event_payload_mapping(event)) is not None
        and payload.get("reviewId") == review.id
    ]
    if len(matching_events) != 1:
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The deterministic review has no unique completion event.",
        )
    completion_event = matching_events[0]
    try:
        completion_data = ReviewEventData.model_validate(completion_event.payload)
    except ValidationError:
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The deterministic review completion event is invalid.",
        ) from None
    expected_handler = {
        "1": LEGACY_HANDLER_VERSIONS["review-workflow"],
        "2": REVIEW_HANDLER_VERSION,
    }[result.schema_version]
    review_job = (
        session.get(JobRecord, completion_event.job_id)
        if completion_event.job_id is not None
        else None
    )
    if (
        completion_event.task_id is not None
        or completion_data.verdict != review.verdict
        or review_job is None
        or review_job.workflow_id != workflow.id
        or review_job.kind != "review-workflow"
        or review_job.task_id is not None
        or review_job.status != "succeeded"
        or review_job.handler_version != expected_handler
        or review_job.input_sha256 != review.input_sha256
    ):
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The deterministic review does not match its completed execution job.",
        )
    approval = session.scalar(
        select(ApprovalRecord).where(
            ApprovalRecord.workflow_id == workflow.id,
            ApprovalRecord.plan_id == plan.id,
            ApprovalRecord.subject_type == "plan",
        )
    )
    expected_approval_schema = (
        "workflow-plan-approval-v1" if result.schema_version == "1" else "workflow-plan-approval-v2"
    )
    if approval is None or approval.payload_schema_version != expected_approval_schema:
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The deterministic review schema does not match its plan approval provenance.",
        )
    if result.schema_version == "1":
        creation_events = list(
            session.scalars(
                select(EventRecord).where(
                    EventRecord.workflow_id == workflow.id,
                    EventRecord.event_type == "workflow.created",
                )
            )
        )
        approval_events = [
            event
            for event in session.scalars(
                select(EventRecord).where(
                    EventRecord.workflow_id == workflow.id,
                    EventRecord.event_type == "approval.requested",
                )
            )
            if (payload := _event_payload_mapping(event)) is not None
            and payload.get("approvalId") == approval.id
        ]
        inspect_inputs = plan.spec_json.get("steps", [{}])[0].get("inputs", {})
        legacy_jobs = list(
            session.scalars(select(JobRecord).where(JobRecord.workflow_id == workflow.id))
        )
        try:
            expected_review_hash = job_input_hash_for_handler_version(
                session,
                workflow,
                kind="review-workflow",
                task=None,
                handler_version=LEGACY_HANDLER_VERSIONS["review-workflow"],
            )
        except (AttributeError, TypeError, ValueError):
            expected_review_hash = None
        if (
            workflow.generation_mode != "local-deterministic"
            or len(creation_events) != 1
            or _event_payload_mapping(creation_events[0]) is None
            or "generationMode" in creation_events[0].payload
            or len(approval_events) != 1
            or any(
                key in approval_events[0].payload
                for key in {
                    "riskLevel",
                    "reason",
                    "affectedResources",
                    "approvalSchemaVersion",
                }
            )
            or not isinstance(inspect_inputs, dict)
            or "sourceIds" in inspect_inputs
            or "frozenSources" in inspect_inputs
            or any(
                job.handler_version != LEGACY_HANDLER_VERSIONS.get(job.kind) for job in legacy_jobs
            )
            or review_job.input_sha256 != expected_review_hash
        ):
            raise WorkflowConflict(
                "workflow-result-integrity-failed",
                "The schema 1 review has no complete legacy execution provenance.",
            )
    return result


def reviewed_result_snapshot(
    session: Session,
    workflow: WorkflowRecord,
    review: ReviewRecord | None,
    review_result: DeterministicReviewResult | None,
) -> WorkflowResultOut | None:
    if review is None:
        return build_workflow_result(
            session,
            workflow,
            review_completed=False,
        )
    if review_result is None:
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The stored deterministic review result is unavailable.",
        )
    if review_result.verdict != "passed":
        return build_workflow_result(
            session,
            workflow,
            review_completed=True,
        )
    if review_result.schema_version == "1":
        return build_workflow_result(
            session,
            workflow,
            review_completed=True,
        )
    live_result = build_workflow_result(
        session,
        workflow,
        integrity_status="verified-frozen-v2",
        review_completed=True,
    )
    frozen_result = review_result.result_snapshot
    frozen_hash = review_result.result_snapshot_sha256
    if (
        frozen_result is None
        or frozen_hash is None
        or workflow_result_hash(frozen_result) != frozen_hash
        or live_result is None
        or workflow_result_hash(live_result) != frozen_hash
    ):
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "The published workflow result changed after deterministic review.",
        )
    descriptor_by_id = {
        descriptor.source_id: descriptor
        for descriptor in result_source_descriptors(session, workflow)
    }
    cited_source_ids = {
        evidence.source_id for claim in frozen_result.claims for evidence in claim.evidence
    }
    if not cited_source_ids.issubset(descriptor_by_id):
        raise WorkflowConflict(
            "workflow-result-integrity-failed",
            "A reviewed citation has no matching frozen source descriptor.",
        )
    assert_result_sources_current(
        session,
        workflow,
        [descriptor_by_id[source_id] for source_id in sorted(cited_source_ids)],
    )
    return frozen_result


def _dataset_profile_snapshot(
    workflow: WorkflowRecord,
    current_plan_tasks: Sequence[TaskRecord],
) -> DatasetProfile | None:
    inspect_task = next(
        (task for task in current_plan_tasks if task.step_key == "inspect-dataset"),
        None,
    )
    if inspect_task is None:
        return None
    raw_profile = inspect_task.outputs.get("datasetProfile")
    raw_hash = inspect_task.outputs.get("datasetProfileSha256")
    if raw_profile is None and raw_hash is None and inspect_task.status != "completed":
        return None
    try:
        profile = DatasetProfile.model_validate(raw_profile)
    except ValidationError:
        raise WorkflowConflict(
            "dataset-profile-integrity-failed",
            "The current dataset profile is missing or invalid.",
        ) from None
    if (
        inspect_task.status != "completed"
        or not isinstance(raw_hash, str)
        or dataset_profile_sha256(profile) != raw_hash
        or profile.dataset_source_id != workflow.dataset_source_id
        or profile.content_hash != workflow.dataset_content_hash
    ):
        raise WorkflowConflict(
            "dataset-profile-integrity-failed",
            "The current dataset profile no longer matches its workflow and stored hash.",
        )
    return profile


def _analysis_approval_request(
    session: Session,
    workflow: WorkflowRecord,
    intent: AnalysisIntentRecord,
    approval: ApprovalRecord,
) -> AnalysisApprovalEventData:
    matching_events = [
        event
        for event in session.scalars(
            select(EventRecord).where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.task_id == intent.task_id,
                EventRecord.event_type == "analysis.approval-requested",
            )
        )
        if event.payload.get("analysisIntentId") == intent.id
    ]
    if len(matching_events) != 1:
        raise WorkflowConflict(
            "analysis-approval-integrity-failed",
            "The current analysis intent has no unique approval request event.",
        )
    try:
        payload = AnalysisApprovalEventData.model_validate(matching_events[0].payload)
    except ValidationError:
        raise WorkflowConflict(
            "analysis-approval-integrity-failed",
            "The current analysis approval request event is invalid.",
        ) from None
    if (
        payload.approval_id != approval.id
        or payload.analysis_intent_id != intent.id
        or payload.task_id != intent.task_id
        or payload.payload_sha256 != intent.payload_sha256
        or payload.approval_schema_version != approval.payload_schema_version
        or payload.expected_workflow_revision > workflow.row_version
    ):
        raise WorkflowConflict(
            "analysis-approval-integrity-failed",
            "The current analysis approval request does not match its immutable intent.",
        )
    return payload


def _dataset_analysis_intent_snapshot(
    session: Session,
    workflow: WorkflowRecord,
    current_plan_tasks: Sequence[TaskRecord],
) -> tuple[
    WorkflowAnalysisIntentOut | None,
    AnalysisIntentRecord | None,
    ApprovalRecord | None,
    int | None,
]:
    execute_task = next(
        (task for task in current_plan_tasks if task.step_key == "execute-analysis"),
        None,
    )
    if execute_task is None:
        return None, None, None, None
    intents = list(
        session.scalars(
            select(AnalysisIntentRecord)
            .where(
                AnalysisIntentRecord.workflow_id == workflow.id,
                AnalysisIntentRecord.task_id == execute_task.id,
            )
            .order_by(
                AnalysisIntentRecord.repair_attempt.desc(),
                AnalysisIntentRecord.created_at.desc(),
                AnalysisIntentRecord.id.desc(),
            )
        )
    )
    if not intents:
        return None, None, None, None
    attempts = [intent.repair_attempt for intent in intents]
    if (
        any(attempt is None for attempt in attempts)
        or len(set(attempts)) != len(attempts)
        or set(cast(list[int], attempts)) != set(range(max(cast(list[int], attempts)) + 1))
    ):
        raise WorkflowConflict(
            "analysis-lineage-integrity-failed",
            "The current analysis intent repair lineage is not contiguous and unique.",
        )
    intent = intents[0]
    approvals = list(
        session.scalars(
            select(ApprovalRecord).where(
                ApprovalRecord.workflow_id == workflow.id,
                ApprovalRecord.task_id == execute_task.id,
                ApprovalRecord.subject_type == "analysis-intent",
                ApprovalRecord.subject_id == intent.id,
            )
        )
    )
    if len(approvals) != 1:
        raise WorkflowConflict(
            "analysis-approval-integrity-failed",
            "The current analysis intent has no unique approval record.",
        )
    approval = approvals[0]
    request = _analysis_approval_request(session, workflow, intent, approval)
    try:
        validate_workflow_analysis_intent(
            session,
            intent,
            expected_workflow_id=workflow.id,
            expected_workflow_revision=request.expected_workflow_revision,
            require_approval=True,
            require_current_revision=False,
        )
    except AnalysisServiceError as error:
        raise WorkflowConflict(error.code, error.detail) from None
    expected_decision = (
        None if intent.status == "waiting-approval" else (
            "rejected" if intent.status == "rejected" else "approved"
        )
    )
    if intent.decision != expected_decision or approval.user_decision != expected_decision:
        raise WorkflowConflict(
            "analysis-decision-integrity-failed",
            "The current analysis intent and approval decisions do not agree.",
        )
    if expected_decision is not None:
        decision_event_type = (
            "analysis.approved" if expected_decision == "approved" else "analysis.rejected"
        )
        decision_events = [
            event
            for event in session.scalars(
                select(EventRecord).where(
                    EventRecord.workflow_id == workflow.id,
                    EventRecord.task_id == intent.task_id,
                    EventRecord.event_type == decision_event_type,
                )
            )
            if event.payload.get("analysisIntentId") == intent.id
        ]
        try:
            decision_data = (
                AnalysisApprovalEventData.model_validate(decision_events[0].payload)
                if len(decision_events) == 1
                else None
            )
        except ValidationError:
            decision_data = None
        decision_job = (
            session.get(JobRecord, decision_events[0].job_id)
            if len(decision_events) == 1 and decision_events[0].job_id is not None
            else None
        )
        approved_job_valid = expected_decision != "approved" or (
            decision_job is not None
            and decision_job.workflow_id == workflow.id
            and decision_job.task_id == intent.task_id
            and decision_job.kind == "execute-task"
        )
        rejected_job_valid = expected_decision != "rejected" or (
            decision_events[0].job_id is None if len(decision_events) == 1 else False
        )
        if (
            len(decision_events) != 1
            or decision_data is None
            or decision_data.approval_id != approval.id
            or decision_data.analysis_intent_id != intent.id
            or decision_data.task_id != intent.task_id
            or decision_data.payload_sha256 != intent.payload_sha256
            or decision_data.expected_workflow_revision
            != request.expected_workflow_revision
            or not approved_job_valid
            or not rejected_job_valid
        ):
            raise WorkflowConflict(
                "analysis-decision-integrity-failed",
                "The current analysis decision has no exact workflow audit event.",
            )
    try:
        output = WorkflowAnalysisIntentOut(
            id=intent.id,
            task_id=intent.task_id,
            project_id=intent.project_id,
            dataset_source_id=intent.dataset_source_id,
            dataset_content_hash=intent.dataset_content_hash or "",
            objective=intent.objective,
            code=intent.code,
            payload_sha256=intent.payload_sha256,
            risk_level="high",
            affected_resources=approval.affected_resources,
            status=cast(Any, intent.status),
            decision=cast(Any, intent.decision),
            workflow_id=workflow.id,
            plan_step_id="execute-analysis",
            previous_intent_id=intent.previous_intent_id,
            expected_outputs=cast(Any, intent.expected_outputs or []),
            timeout_seconds=intent.timeout_seconds or 0,
            repair_attempt=cast(Any, intent.repair_attempt),
            error_summary=(
                AnalysisErrorSummaryOut.model_validate(intent.error_summary)
                if intent.error_summary is not None
                else None
            ),
            code_diff=intent.code_diff,
            created_at=intent.created_at,
            updated_at=intent.updated_at,
        )
    except ValidationError:
        raise WorkflowConflict(
            "analysis-intent-integrity-failed",
            "The current analysis intent does not satisfy its public contract.",
        ) from None
    return output, intent, approval, request.expected_workflow_revision


def _dataset_analysis_run_snapshot(
    session: Session,
    workflow: WorkflowRecord,
    intent: AnalysisIntentRecord | None,
) -> WorkflowAnalysisRunOut | None:
    if intent is None:
        return None
    runs = list(
        session.scalars(
            select(RunRecord).where(RunRecord.analysis_intent_id == intent.id)
        )
    )
    if not runs:
        return None
    if len(runs) != 1:
        raise WorkflowConflict(
            "analysis-run-lineage-invalid",
            "The current analysis intent has more than one execution run.",
        )
    project = session.get(ProjectRecord, workflow.project_id)
    if project is None:
        raise WorkflowConflict(
            "analysis-records-incomplete",
            "The workflow project is missing from the analysis run lineage.",
        )
    try:
        run = analysis_run_out(session, runs[0], intent, project)
        return WorkflowAnalysisRunOut(
            id=run.id,
            intent_id=run.intent_id,
            task_id=run.task_id,
            project_id=run.project_id,
            dataset_source_id=run.dataset_source_id,
            objective=run.objective,
            code=run.code,
            payload_sha256=run.payload_sha256,
            status=cast(Any, run.status),
            environment_hash=run.environment_hash,
            input_artifacts=run.input_artifacts,
            output_artifacts=run.output_artifacts,
            stdout=run.stdout,
            stderr=run.stderr,
            log=run.log,
            logs=run.logs,
            error=run.error,
            artifacts=[
                WorkflowAnalysisArtifactOut(
                    id=artifact.id,
                    artifact_type=cast(Any, artifact.artifact_type),
                    path=artifact.path,
                    mime_type=artifact.mime_type,
                    content_hash=artifact.content_hash,
                    size_bytes=artifact.size_bytes,
                    created_at=artifact.created_at,
                )
                for artifact in run.artifacts
            ],
            created_at=run.created_at,
            finished_at=run.finished_at,
        )
    except (AnalysisServiceError, ValidationError) as error:
        detail = error.detail if isinstance(error, AnalysisServiceError) else (
            "The current analysis run does not satisfy its public integrity contract."
        )
        code = error.code if isinstance(error, AnalysisServiceError) else (
            "analysis-run-integrity-failed"
        )
        raise WorkflowConflict(code, detail) from None


def _dataset_review_snapshot(
    session: Session,
    workflow: WorkflowRecord,
    plan: PlanRecord | None,
    current_plan_tasks: Sequence[TaskRecord],
    review: ReviewRecord | None,
) -> ReviewSnapshotOut | None:
    if review is None:
        return None
    collect_task = next(
        (task for task in current_plan_tasks if task.step_key == "collect-artifacts"),
        None,
    )
    completion_events = [
        event
        for event in session.scalars(
            select(EventRecord).where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.event_type == "review.completed",
            )
        )
        if event.payload.get("reviewId") == review.id
    ]
    review_job = (
        session.get(JobRecord, completion_events[0].job_id)
        if len(completion_events) == 1 and completion_events[0].job_id is not None
        else None
    )
    try:
        result = DatasetAnalysisReviewResult.model_validate(review.result_json)
        event_data = (
            ReviewEventData.model_validate(completion_events[0].payload)
            if len(completion_events) == 1
            else None
        )
    except ValidationError:
        result = None
        event_data = None
    if (
        plan is None
        or plan.status != "approved"
        or review.workflow_id != workflow.id
        or review.plan_id != plan.id
        or review.review_type != "deterministic-analysis-v1"
        or result is None
        or review.verdict != result.verdict
        or collect_task is None
        or review.task_id != collect_task.id
        or collect_task.status != "completed"
        or len(current_plan_tasks) != 4
        or any(task.status != "completed" for task in current_plan_tasks)
        or len(completion_events) != 1
        or completion_events[0].task_id != collect_task.id
        or event_data is None
        or event_data.review_id != review.id
        or event_data.verdict != review.verdict
        or review_job is None
        or review_job.workflow_id != workflow.id
        or review_job.kind != "review-workflow"
        or review_job.task_id is not None
        or review_job.status != "succeeded"
        or review_job.handler_version != REVIEW_HANDLER_VERSION
        or review_job.input_sha256 != review.input_sha256
    ):
        raise WorkflowConflict(
            "analysis-review-integrity-failed",
            "The current deterministic analysis review has invalid provenance.",
        )
    return ReviewSnapshotOut(
        id=review.id,
        review_type="deterministic-analysis-v1",
        verdict=cast(Any, review.verdict),
        input_sha256=review.input_sha256,
        result=result,
        created_at=review.created_at,
    )


def _review_warning_acceptance_snapshot(
    session: Session,
    workflow: WorkflowRecord,
    review: ReviewRecord | None,
) -> DatasetReviewWarningAcceptanceOut | None:
    events = list(
        session.scalars(
            select(EventRecord).where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.event_type == "analysis.review-warnings-accepted",
            )
        )
    )
    if not events:
        return None
    if len(events) != 1 or review is None:
        raise WorkflowConflict(
            "review-warning-acceptance-integrity-failed",
            "The workflow warning acceptance is missing its unique review binding.",
        )
    event = events[0]
    try:
        data = DatasetReviewWarningsAcceptedEventData.model_validate(event.payload)
    except ValidationError:
        raise WorkflowConflict(
            "review-warning-acceptance-integrity-failed",
            "The workflow warning acceptance event is invalid.",
        ) from None
    completion_transitions = [
        candidate
        for candidate in session.scalars(
            select(EventRecord).where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.event_type == "workflow.status-changed",
            )
        )
        if candidate.payload.get("status") == "completed"
        and candidate.sequence is not None
        and event.sequence is not None
        and candidate.sequence > event.sequence
    ]
    if (
        workflow.status != "completed"
        or review.verdict != "passed-with-warnings"
        or data.review_id != review.id
        or data.review_input_sha256 != review.input_sha256
        or event.task_id != review.task_id
        or event.job_id is not None
        or workflow.finished_at is None
        or len(completion_transitions) != 1
    ):
        raise WorkflowConflict(
            "review-warning-acceptance-integrity-failed",
            "The workflow warning acceptance does not match its completed review.",
        )
    return DatasetReviewWarningAcceptanceOut(
        event_id=event.id,
        review_id=review.id,
        review_input_sha256=review.input_sha256,
        expected_workflow_revision=data.expected_workflow_revision,
        decision="accepted",
        accepted_at=event.created_at,
    )


def _dataset_workflow_snapshot(
    session: Session,
    workflow: WorkflowRecord,
    *,
    plan: PlanRecord | None,
    plan_out: PlanSnapshotOut | None,
    current_plan_tasks: Sequence[TaskRecord],
    current_task: TaskRecord | None,
    approvals: Sequence[ApprovalRecord],
    jobs: Sequence[JobRecord],
    review: ReviewRecord | None,
    retry_count: int,
    blocker: BlockingReasonOut | None,
) -> ResearchWorkflowSnapshot:
    profile = _dataset_profile_snapshot(workflow, current_plan_tasks)
    intent_out, intent, intent_approval, intent_revision = (
        _dataset_analysis_intent_snapshot(session, workflow, current_plan_tasks)
    )
    run_out = _dataset_analysis_run_snapshot(session, workflow, intent)
    review_out = _dataset_review_snapshot(
        session,
        workflow,
        plan,
        current_plan_tasks,
        review,
    )
    warning_acceptance = _review_warning_acceptance_snapshot(session, workflow, review)

    pending_outputs: list[WorkflowPendingApprovalOut] = []
    selected_pending_ids: set[str] = set()
    if plan is not None and plan.status == "pending-approval":
        plan_approvals = [
            approval
            for approval in approvals
            if approval.plan_id == plan.id
            and approval.subject_type == "plan"
            and approval.subject_id == plan.id
        ]
        revisions = [
            resource.removeprefix("workflow-revision:")
            for approval in plan_approvals
            for resource in approval.affected_resources
            if resource.startswith("workflow-revision:")
        ]
        if len(plan_approvals) != 1 or len(revisions) != 1 or not revisions[0].isdigit():
            raise WorkflowConflict(
                "dataset-plan-approval-invalid",
                "The current dataset plan has no unique revision-bound approval.",
            )
        approval = plan_approvals[0]
        expected_revision = int(revisions[0])
        if (
            approval.payload_schema_version != "workflow-plan-approval-v3"
            or approval.risk_level != "medium"
            or expected_revision != workflow.row_version
            or workflow.dataset_source_id is None
            or workflow.dataset_content_hash is None
        ):
            raise WorkflowConflict(
                "dataset-plan-approval-invalid",
                "The current dataset plan approval envelope is invalid.",
            )
        pending_outputs.append(
            DatasetPlanPendingApprovalOut(
                id=approval.id,
                workflow_id=workflow.id,
                plan_id=plan.id,
                task_id=None,
                kind="plan",
                status="waiting",
                subject_type="plan",
                subject_id=plan.id,
                workflow_type="dataset-analysis",
                action="approve-plan",
                payload_sha256=approval.intent_hash,
                risk_level="medium",
                reason=approval.reason,
                affected_resources=approval.affected_resources,
                approval_schema_version="workflow-plan-approval-v3",
                plan_version=plan.version,
                plan_sha256=plan.spec_sha256,
                expected_workflow_revision=expected_revision,
                dataset_source_id=workflow.dataset_source_id,
                dataset_content_hash=workflow.dataset_content_hash,
                created_at=approval.created_at,
                decided_at=approval.decided_at,
            )
        )
        selected_pending_ids.add(approval.id)
    if (
        intent_out is not None
        and intent is not None
        and intent.status == "waiting-approval"
    ):
        if intent_approval is None or intent_revision is None or plan is None:
            raise WorkflowConflict(
                "analysis-approval-integrity-failed",
                "The waiting analysis intent has no exact approval envelope.",
            )
        pending_outputs.append(
            AnalysisExecutionPendingApprovalOut(
                id=intent_approval.id,
                workflow_id=workflow.id,
                plan_id=plan.id,
                task_id=intent.task_id,
                kind="analysis-execution",
                status="waiting",
                subject_type="analysis-intent",
                subject_id=intent.id,
                action="execute-python-data-analysis",
                payload_sha256=intent.payload_sha256,
                risk_level="high",
                reason=intent_approval.reason,
                affected_resources=intent_approval.affected_resources,
                approval_schema_version=cast(
                    Literal["analysis-intent-v2", "analysis-intent-v3"],
                    intent_approval.payload_schema_version,
                ),
                expected_workflow_revision=intent_revision,
                analysis_intent_id=intent.id,
                plan_step_id="execute-analysis",
                dataset_source_id=intent.dataset_source_id,
                dataset_content_hash=intent.dataset_content_hash or "",
                expected_outputs=cast(Any, intent.expected_outputs or []),
                timeout_seconds=intent.timeout_seconds or 0,
                code=intent.code,
                code_diff=intent.code_diff,
                created_at=intent_approval.created_at,
                decided_at=intent_approval.decided_at,
            )
        )
        selected_pending_ids.add(intent_approval.id)
    if {approval.id for approval in approvals} != selected_pending_ids:
        raise WorkflowConflict(
            "workflow-approval-integrity-failed",
            "The workflow contains a pending approval outside its current plan and intent chain.",
        )
    actions = allowed_actions(
        workflow,
        approvals,
        jobs,
        review=review,
        review_warnings_accepted=warning_acceptance is not None,
    )
    return ResearchWorkflowSnapshot(
        workflow=WorkflowStateOut(
            id=workflow.id,
            project_id=workflow.project_id,
            workflow_type="dataset-analysis",
            dataset_source_id=workflow.dataset_source_id,
            dataset_content_hash=workflow.dataset_content_hash,
            goal=workflow.goal,
            generation_mode=cast(GenerationMode, workflow.generation_mode),
            status=cast(WorkflowStatus, workflow.status),
            revision=workflow.row_version,
            plan_version=plan.version if plan is not None else None,
            current_step_id=current_task.id if current_task is not None else None,
            retry_count=retry_count,
            blocking_reason=blocker,
            cancel_requested_at=workflow.cancel_requested_at,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
            completed_at=workflow.finished_at,
        ),
        plan=plan_out,
        pending_approvals=pending_outputs,
        result=None,
        latest_review=review_out,
        dataset_profile=profile,
        analysis_intent=intent_out,
        analysis_run=run_out,
        review_warning_acceptance=warning_acceptance,
        allowed_actions=actions,
        event_cursor=workflow.event_sequence,
    )


def workflow_snapshot(session: Session, workflow: WorkflowRecord) -> ResearchWorkflowSnapshot:
    plan = session.scalar(
        select(PlanRecord)
        .where(PlanRecord.workflow_id == workflow.id)
        .order_by(PlanRecord.version.desc())
    )
    tasks = list(
        session.scalars(
            select(TaskRecord)
            .where(TaskRecord.workflow_id == workflow.id)
            .order_by(TaskRecord.order_index)
        )
    )
    jobs = list(
        session.scalars(
            select(JobRecord)
            .where(JobRecord.workflow_id == workflow.id)
            .order_by(JobRecord.created_at)
        )
    )
    approvals = list(
        session.scalars(
            select(ApprovalRecord)
            .where(
                ApprovalRecord.workflow_id == workflow.id,
                ApprovalRecord.user_decision.is_(None),
            )
            .order_by(ApprovalRecord.created_at)
        )
    )
    review_query = select(ReviewRecord).where(ReviewRecord.workflow_id == workflow.id)
    if workflow.workflow_type == "dataset-analysis" and plan is not None:
        review_query = review_query.where(ReviewRecord.plan_id == plan.id)
    review = session.scalar(
        review_query.order_by(ReviewRecord.created_at.desc(), ReviewRecord.id.desc())
    )
    current_plan_tasks = (
        [task for task in tasks if task.plan_id == plan.id] if plan is not None else []
    )
    current_task = next(
        (
            task
            for task in current_plan_tasks
            if task.status not in {"completed", "cancelled"}
        ),
        None,
    )
    retry_count = sum(max(0, job.attempt - 1) for job in jobs)
    blocker = (
        BlockingReasonOut(
            code=workflow.blocking_code or "blocked",
            user_message=workflow.blocking_message or "The workflow is blocked.",
            retryable=workflow.blocking_code
            in {
                "no-ready-pdf",
                "analysis-execution-rejected",
                "analysis-repair-not-safe",
                "analysis-repair-limit-exceeded",
            },
        )
        if workflow.status == "blocked"
        else None
    )
    plan_out = None
    if plan is not None:
        validated_plan_spec = assert_plan_for_workflow(workflow, plan)
        if plan.status in {"pending-approval", "approved"}:
            assert_plan_approval_integrity(session, workflow, plan)
        plan_tasks = [task for task in tasks if task.plan_id == plan.id]
        plan_out = PlanSnapshotOut(
            id=plan.id,
            workflow_id=plan.workflow_id,
            version=plan.version,
            status=cast(PlanStatus, plan.status),
            plan_sha256=plan.spec_sha256,
            generator=plan.generator,
            model=plan.model,
            prompt_version=plan.prompt_version,
            spec=validated_plan_spec,
            steps=[
                MaterializedStepOut(
                    id=task.id,
                    key=task.step_key or "",
                    order_index=task.order_index or 0,
                    type=cast(TaskStepType, task.task_type),
                    objective=task.objective,
                    status=cast(TaskStatus, task.status),
                    retry_count=task.retries,
                    started_at=task.started_at,
                    completed_at=task.finished_at,
                    output_summary=task_output_summary(task),
                )
                for task in plan_tasks
            ],
            created_at=plan.created_at,
            approved_at=plan.approved_at,
        )
    if workflow.workflow_type == "dataset-analysis":
        return _dataset_workflow_snapshot(
            session,
            workflow,
            plan=plan,
            plan_out=plan_out,
            current_plan_tasks=current_plan_tasks,
            current_task=current_task,
            approvals=approvals,
            jobs=jobs,
            review=review,
            retry_count=retry_count,
            blocker=blocker,
        )
    parsed_review_result = validated_review_result(
        session,
        workflow,
        plan,
        review,
    )
    review_out = None
    if review is not None:
        if parsed_review_result is None:
            raise WorkflowConflict(
                "workflow-result-integrity-failed",
                "The stored deterministic review result is unavailable.",
            )
        review_out = ReviewSnapshotOut(
            id=review.id,
            review_type=cast(ReviewType, review.review_type),
            verdict=cast(ReviewVerdict, review.verdict),
            input_sha256=review.input_sha256,
            result=parsed_review_result,
            created_at=review.created_at,
        )
    return ResearchWorkflowSnapshot(
        workflow=WorkflowStateOut(
            id=workflow.id,
            project_id=workflow.project_id,
            workflow_type="literature-synthesis",
            goal=workflow.goal,
            generation_mode=cast(GenerationMode, workflow.generation_mode),
            status=cast(WorkflowStatus, workflow.status),
            revision=workflow.row_version,
            plan_version=plan.version if plan is not None else None,
            current_step_id=current_task.id if current_task is not None else None,
            retry_count=retry_count,
            blocking_reason=blocker,
            cancel_requested_at=workflow.cancel_requested_at,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
            completed_at=workflow.finished_at,
        ),
        plan=plan_out,
        pending_approvals=cast(
            list[WorkflowPendingApprovalOut],
            [
                PendingApprovalOut(
                    id=approval.id,
                    workflow_id=workflow.id,
                    plan_id=approval.plan_id or "",
                    task_id=None,
                    kind="plan",
                    status="waiting",
                    subject_type="plan",
                    subject_id=approval.subject_id or approval.plan_id or "",
                    action=approval.requested_action,
                    payload_sha256=approval.intent_hash,
                    risk_level=cast(WorkflowRiskLevel, approval.risk_level),
                    reason=approval.reason,
                    affected_resources=approval.affected_resources,
                    created_at=approval.created_at,
                    decided_at=approval.decided_at,
                )
                for approval in approvals
            ],
        ),
        result=reviewed_result_snapshot(
            session,
            workflow,
            review,
            parsed_review_result,
        ),
        latest_review=review_out,
        allowed_actions=allowed_actions(workflow, approvals, jobs),
        event_cursor=workflow.event_sequence,
    )


def list_workflows(
    session: Session,
    project_id: str,
    *,
    active_only: bool,
    limit: int,
) -> list[WorkflowRecord]:
    query: Select[tuple[WorkflowRecord]] = select(WorkflowRecord).where(
        WorkflowRecord.project_id == project_id,
        WorkflowRecord.creation_mode == "fixed-workflow",
    )
    if active_only:
        query = query.where(WorkflowRecord.status.not_in(["completed", "cancelled"]))
    return list(session.scalars(query.order_by(WorkflowRecord.updated_at.desc()).limit(limit)))
