from __future__ import annotations

from typing import Any, Literal, cast

from sqlalchemy.orm import Session

from ..model_gateway import model_gateway
from ..models import (
    EvidenceSpanRecord,
    JobRecord,
    TaskRecord,
    WorkflowRecord,
)
from ._handlers.dataset import (
    PreparedAnalysis,
    handle_collect_artifacts,
    handle_dataset_inspection,
    handle_prepare_analysis,
)
from ._handlers.evidence import (
    evidence_fingerprint as evidence_fingerprint,
)
from ._handlers.evidence import (
    extract_local_evidence as extract_local_evidence,
)
from ._handlers.evidence import (
    validate_evidence_integrity as validate_evidence_integrity,
)
from ._handlers.lifecycle import (
    acknowledge_cancellation as acknowledge_cancellation,
)
from ._handlers.lifecycle import (
    advance_after_task as advance_after_task,
)
from ._handlers.lifecycle import (
    append_failed_task_event as append_failed_task_event,
)
from ._handlers.lifecycle import (
    assert_current_task_contract as assert_current_task_contract,
)
from ._handlers.lifecycle import (
    execute_leased_job as _execute_leased_job,
)
from ._handlers.lifecycle import (
    finish_job as finish_job,
)
from ._handlers.lifecycle import (
    mark_leased_job_started as mark_leased_job_started,
)
from ._handlers.lifecycle import (
    previous_task as previous_task,
)
from ._handlers.lifecycle import (
    settle_leased_job_error as settle_leased_job_error,
)
from ._handlers.lifecycle import (
    workflow_failure_from_conflict as workflow_failure_from_conflict,
)
from ._handlers.planning import (
    REMOTE_PLAN_PROMPT_VERSION as REMOTE_PLAN_PROMPT_VERSION,
)
from ._handlers.planning import (
    assert_remote_gateway_matches_creation as _assert_remote_gateway_matches_creation_impl,
)
from ._handlers.planning import (
    complete_model_json as _complete_model_json_impl,
)
from ._handlers.planning import (
    handle_generate_plan as _handle_generate_plan_impl,
)
from ._handlers.planning import (
    model_plan as _model_plan_impl,
)
from ._handlers.planning import (
    template_plan as template_plan,
)
from ._handlers.review import (
    assert_current_review_contract as assert_current_review_contract,
)
from ._handlers.review import (
    handle_review as handle_review,
)
from ._handlers.sources import (
    inspect_sources as inspect_sources,
)
from ._handlers.sources import (
    parse_source_descriptors as parse_source_descriptors,
)
from ._handlers.sources import (
    ready_source_descriptors as ready_source_descriptors,
)
from ._handlers.sources import (
    source_file_matches as source_file_matches,
)
from ._handlers.sources import (
    source_page_manifest_hash as source_page_manifest_hash,
)
from ._handlers.sources import (
    upgrade_legacy_inspect_descriptors as upgrade_legacy_inspect_descriptors,
)
from ._handlers.sources import (
    validate_source_descriptors as validate_source_descriptors,
)
from ._handlers.sources import (
    validated_source_descriptors_for_task as validated_source_descriptors_for_task,
)
from ._handlers.synthesis import (
    LOCAL_SYNTHESIS_PROMPT_VERSION as LOCAL_SYNTHESIS_PROMPT_VERSION,
)
from ._handlers.synthesis import (
    REMOTE_SYNTHESIS_PROMPT_VERSION as REMOTE_SYNTHESIS_PROMPT_VERSION,
)
from ._handlers.synthesis import (
    answer_summary_matches as answer_summary_matches,
)
from ._handlers.synthesis import (
    assert_remote_passage_approval as _assert_remote_passage_approval_impl,
)
from ._handlers.synthesis import (
    deterministic_extract_summary as deterministic_extract_summary,
)
from ._handlers.synthesis import (
    persist_extractively_grounded_answer as persist_extractively_grounded_answer,
)
from ._handlers.synthesis import (
    synthesize_extractive_claims as _synthesize_extractive_claims_impl,
)
from ._handlers.synthesis import (
    synthesize_legacy_local_claims as synthesize_legacy_local_claims,
)
from ._handlers.synthesis import (
    synthesize_local_claims as synthesize_local_claims,
)
from ._handlers.synthesis import (
    synthesize_remote_claims as _synthesize_remote_claims_impl,
)
from ._handlers.text import (
    ENGLISH_STOPWORDS as ENGLISH_STOPWORDS,
)
from ._handlers.text import (
    PassageCandidate as PassageCandidate,
)
from ._handlers.text import (
    atomic_statement as atomic_statement,
)
from ._handlers.text import (
    is_exact_atomic_sentence as is_exact_atomic_sentence,
)
from ._handlers.text import (
    normalize_text as normalize_text,
)
from ._handlers.text import (
    normalized_contains as normalized_contains,
)
from ._handlers.text import (
    rank_passages as rank_passages,
)
from ._handlers.text import (
    select_diverse_passages as select_diverse_passages,
)
from ._handlers.text import (
    sentence_candidates as sentence_candidates,
)
from ._handlers.text import (
    string_list as string_list,
)
from ._handlers.text import (
    terms as terms,
)
from .agent_service import handle_route_intent as _handle_route_intent_impl
from .schemas import (
    AnalysisApprovalEventData,
    AnalysisCompiledEventData,
    AnalysisIntentCreatedEventData,
    FrozenSourceDescriptor,
    PlanSpec,
    SynthesizeExtractiveClaimsInput,
    TaskEventData,
)
from .service import append_workflow_events, transition_task
from .state import WorkflowFailure


def complete_model_json(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    return _complete_model_json_impl(model_gateway, system_prompt, user_prompt)


def model_plan(
    goal: str,
    frozen_sources: list[FrozenSourceDescriptor],
) -> PlanSpec:
    return _model_plan_impl(goal, frozen_sources, model_gateway)


def handle_generate_plan(
    session: Session,
    workflow: WorkflowRecord,
    job: JobRecord,
    *,
    legacy_handler: bool,
) -> None:
    _handle_generate_plan_impl(
        session,
        workflow,
        job,
        model_gateway,
        legacy_handler=legacy_handler,
    )


def handle_route_intent(
    session: Session,
    workflow: WorkflowRecord,
    job: JobRecord,
) -> None:
    _handle_route_intent_impl(
        session,
        workflow,
        job,
        gateway=model_gateway,
    )


def _handle_task(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord,
    job: JobRecord,
    *,
    legacy_handler: bool,
) -> None:
    if workflow.status != "running" or task.status != "running":
        raise WorkflowFailure(
            "task-not-running",
            "The workflow step is no longer running.",
        )
    assert_current_task_contract(session, workflow, task)
    prepared_analysis: PreparedAnalysis | None = None
    if task.task_type == "inspect-sources":
        outputs = inspect_sources(session, workflow, task)
    elif task.task_type == "extract-local-evidence":
        outputs = extract_local_evidence(
            session,
            workflow,
            task,
            legacy_handler=legacy_handler,
        )
    elif task.task_type == "synthesize-extractive-claims":
        outputs = synthesize_extractive_claims(
            session,
            workflow,
            task,
            legacy_handler=legacy_handler,
        )
    elif task.task_type == "dataset-inspection":
        outputs = handle_dataset_inspection(session, workflow, task)
    elif task.task_type == "prepare-analysis":
        prepared_analysis = handle_prepare_analysis(session, workflow, task)
        outputs = prepared_analysis.outputs
    elif task.task_type == "collect-artifacts":
        outputs = handle_collect_artifacts(session, workflow, task)
    else:
        raise WorkflowFailure(
            "unsupported-task-type",
            "The workflow step type is unsupported.",
        )
    task.outputs = outputs
    transition_task(session, task, "completed")
    finish_job(session, job, "succeeded")
    output_count = max(
        (
            len(cast(list[object], value))
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
    if prepared_analysis is not None:
        execution_task = prepared_analysis.execution_task
        bundle = prepared_analysis.intent_bundle
        transition_task(session, execution_task, "waiting-approval")
        analysis_events: list[tuple[str, Any, str | None, str | None]] = [
            (
                "analysis.intent-created",
                AnalysisIntentCreatedEventData(
                    analysis_intent_id=bundle.intent.id,
                    task_id=execution_task.id,
                    job_id=job.id,
                    plan_step_id="execute-analysis",
                    dataset_source_id=bundle.intent.dataset_source_id,
                    dataset_content_hash=bundle.intent.dataset_content_hash or "",
                    payload_sha256=bundle.intent.payload_sha256,
                    repair_attempt=0,
                ),
                execution_task.id,
                job.id,
            )
        ]
        if bundle.intent.analysis_spec_id is not None:
            if not all(
                (
                    bundle.intent.spec_sha256,
                    bundle.intent.dataset_profile_sha256,
                    bundle.intent.compiler_version,
                    bundle.intent.code_sha256,
                    bundle.intent.runtime_policy_id,
                )
            ):
                raise WorkflowFailure(
                    "analysis-compiled-provenance-missing",
                    "The compiled analysis intent is missing immutable provenance.",
                )
            analysis_events.append(
                (
                    "analysis.compiled",
                    AnalysisCompiledEventData(
                        analysis_intent_id=bundle.intent.id,
                        analysis_spec_id=bundle.intent.analysis_spec_id,
                        spec_sha256=cast(str, bundle.intent.spec_sha256),
                        dataset_profile_sha256=cast(
                            str, bundle.intent.dataset_profile_sha256
                        ),
                        compiler_version=cast(str, bundle.intent.compiler_version),
                        approved_code_sha256=cast(str, bundle.intent.code_sha256),
                        runtime_policy_id=cast(str, bundle.intent.runtime_policy_id),
                    ),
                    execution_task.id,
                    job.id,
                )
            )
        approval_event = AnalysisApprovalEventData(
            approval_id=bundle.approval.id,
            analysis_intent_id=bundle.intent.id,
            task_id=execution_task.id,
            job_id=job.id,
            payload_sha256=bundle.intent.payload_sha256,
            approval_schema_version=cast(
                Literal[
                    "analysis-intent-v2",
                    "analysis-intent-v3",
                    "analysis-intent-v4",
                ],
                bundle.approval.payload_schema_version,
            ),
            expected_workflow_revision=bundle.expected_workflow_revision,
        )
        analysis_events.append(
            (
                "analysis.approval-requested",
                approval_event,
                execution_task.id,
                job.id,
            )
        )
        if bundle.intent.analysis_spec_id is not None:
            analysis_events.append(
                (
                    "analysis.execution-approval-requested",
                    approval_event,
                    execution_task.id,
                    job.id,
                )
            )
        append_workflow_events(
            session,
            workflow,
            analysis_events,
        )
        return
    advance_after_task(
        session,
        workflow,
        task,
        preserve_legacy_review=legacy_handler
        and task.task_type == "synthesize-extractive-claims",
    )


def synthesize_extractive_claims(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord,
    *,
    legacy_handler: bool,
) -> dict[str, Any]:
    return _synthesize_extractive_claims_impl(
        session,
        workflow,
        task,
        model_gateway,
        legacy_handler=legacy_handler,
    )


def synthesize_remote_claims(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord,
    payload: SynthesizeExtractiveClaimsInput,
    evidence_records: list[EvidenceSpanRecord],
) -> dict[str, Any]:
    return _synthesize_remote_claims_impl(
        session,
        workflow,
        task,
        payload,
        evidence_records,
        model_gateway,
    )


def assert_remote_gateway_matches_creation(
    session: Session,
    workflow: WorkflowRecord,
) -> None:
    _assert_remote_gateway_matches_creation_impl(session, workflow, model_gateway)


def assert_remote_passage_approval(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord,
    evidence_records: list[EvidenceSpanRecord],
) -> None:
    _assert_remote_passage_approval_impl(
        session,
        workflow,
        task,
        evidence_records,
        model_gateway,
    )


def execute_leased_job(session: Session, job_id: str, lease_token: str) -> None:
    _execute_leased_job(
        session,
        job_id,
        lease_token,
        handle_route_intent=handle_route_intent,
        handle_generate_plan=handle_generate_plan,
        handle_task=_handle_task,
        handle_review=handle_review,
    )
