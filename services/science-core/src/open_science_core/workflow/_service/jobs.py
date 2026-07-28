from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    AgentDecisionRecord,
    AnalysisIntentRecord,
    AnswerRecord,
    ApprovalRecord,
    ArtifactRecord,
    ClaimEvidenceRecord,
    ClaimRecord,
    EvidenceSpanRecord,
    IntentDecisionRecord,
    InteractionRequestRecord,
    JobRecord,
    PlanRecord,
    RunRecord,
    SourceRecord,
    StepObservationRecord,
    TaskRecord,
    UserResponseRecord,
    WorkflowRecord,
    utc_now,
)
from .integrity import (
    LEGACY_HANDLER_VERSIONS,
    PLAN_HANDLER_VERSION,
    REVIEW_HANDLER_VERSION,
    ROUTER_HANDLER_VERSION,
    TASK_HANDLER_VERSION,
    WorkflowConflict,
    content_sha256,
    task_materialization_hash,
)


def plan_job_envelope(
    session: Session,
    workflow: WorkflowRecord,
    plan: PlanRecord | None,
) -> dict[str, Any] | None:
    if plan is None:
        return None
    approval = session.scalar(
        select(ApprovalRecord).where(
            ApprovalRecord.workflow_id == workflow.id,
            ApprovalRecord.plan_id == plan.id,
            ApprovalRecord.subject_type == "plan",
        )
    )
    return {
        "approvalIntentHash": approval.intent_hash if approval is not None else None,
        "approvalSchemaVersion": (
            approval.payload_schema_version if approval is not None else None
        ),
        "goalSha256": hashlib.sha256(workflow.goal.encode("utf-8")).hexdigest(),
        "planGenerator": plan.generator,
        "planId": plan.id,
        "planModel": plan.model,
        "planPromptVersion": plan.prompt_version,
        "planSha256": plan.spec_sha256,
    }


def job_input_payload(
    session: Session,
    workflow: WorkflowRecord,
    *,
    kind: str,
    task: TaskRecord | None,
    handler_version: str | None = None,
) -> dict[str, Any]:
    selected_handler_version = handler_version or handler_version_for(kind)
    if kind == "route-intent":
        source_records = {
            source.id: source
            for source in session.scalars(
                select(SourceRecord).where(SourceRecord.id.in_(workflow.selected_source_ids))
            )
        }
        interactions = list(
            session.scalars(
                select(InteractionRequestRecord)
                .where(InteractionRequestRecord.workflow_id == workflow.id)
                .order_by(
                    InteractionRequestRecord.created_at,
                    InteractionRequestRecord.id,
                )
            )
        )
        response_envelopes: list[dict[str, Any]] = []
        for interaction in interactions:
            latest_response = session.scalar(
                select(UserResponseRecord)
                .where(UserResponseRecord.interaction_id == interaction.id)
                .order_by(UserResponseRecord.revision.desc())
            )
            if latest_response is None:
                continue
            response_envelopes.append(
                {
                    "interactionId": interaction.id,
                    "requestType": interaction.request_type,
                    "responseRevision": latest_response.revision,
                    "responseSha256": latest_response.response_sha256,
                }
            )
        return {
            "answers": response_envelopes,
            "generationMode": workflow.generation_mode,
            "goalSha256": hashlib.sha256(workflow.goal.encode("utf-8")).hexdigest(),
            "handlerVersion": selected_handler_version,
            "kind": kind,
            "sources": [
                {
                    "contentHash": source_records[source_id].content_hash,
                    "id": source_id,
                    "ingestionStatus": source_records[source_id].ingestion_status,
                    "sourceKind": source_records[source_id].source_kind,
                }
                if source_id in source_records
                else {"id": source_id, "missing": True}
                for source_id in workflow.selected_source_ids
            ],
            "workflowId": workflow.id,
        }
    if kind == "generate-plan":
        payload: dict[str, Any] = {
            "goalSha256": hashlib.sha256(workflow.goal.encode("utf-8")).hexdigest(),
            "handlerVersion": selected_handler_version,
            "kind": kind,
            "workflowId": workflow.id,
        }
        if selected_handler_version != LEGACY_HANDLER_VERSIONS["generate-plan"]:
            payload["generationMode"] = workflow.generation_mode
        if workflow.creation_mode == "autonomous":
            decision = (
                session.get(IntentDecisionRecord, workflow.current_intent_decision_id)
                if workflow.current_intent_decision_id is not None
                else None
            )
            if decision is None or decision.workflow_id != workflow.id:
                raise WorkflowConflict(
                    "intent-decision-binding-invalid",
                    "An autonomous plan job requires its current validated intent decision.",
                )
            sources = {
                source.id: source
                for source in session.scalars(
                    select(SourceRecord).where(SourceRecord.id.in_(decision.selected_source_ids))
                )
            }
            payload["intentDecision"] = {
                "id": decision.id,
                "outputSha256": decision.output_sha256,
                "selectedSources": [
                    {
                        "contentHash": sources[source_id].content_hash,
                        "id": source_id,
                        "sourceKind": sources[source_id].source_kind,
                    }
                    if source_id in sources
                    else {"id": source_id, "missing": True}
                    for source_id in decision.selected_source_ids
                ],
            }
        if workflow.workflow_type == "dataset-analysis":
            payload.update(
                {
                    "datasetContentHash": workflow.dataset_content_hash,
                    "datasetSourceId": workflow.dataset_source_id,
                    "workflowType": workflow.workflow_type,
                }
            )
        return payload
    if kind == "review-workflow":
        if workflow.workflow_type == "dataset-analysis":
            approved_plan = session.scalar(
                select(PlanRecord).where(
                    PlanRecord.workflow_id == workflow.id,
                    PlanRecord.status == "approved",
                )
            )
            tasks = list(
                session.scalars(
                    select(TaskRecord)
                    .where(
                        TaskRecord.workflow_id == workflow.id,
                        TaskRecord.plan_id
                        == (approved_plan.id if approved_plan is not None else None),
                    )
                    .order_by(TaskRecord.order_index)
                )
            )
            intent_id = tasks[-1].outputs.get("analysisIntentId") if tasks else None
            run_id = tasks[-1].outputs.get("runId") if tasks else None
            intent = (
                session.get(AnalysisIntentRecord, intent_id) if isinstance(intent_id, str) else None
            )
            run = session.get(RunRecord, run_id) if isinstance(run_id, str) else None
            approval = (
                session.scalar(
                    select(ApprovalRecord).where(
                        ApprovalRecord.subject_type == "analysis-intent",
                        ApprovalRecord.subject_id == intent.id,
                    )
                )
                if intent is not None
                else None
            )
            artifacts = (
                list(
                    session.scalars(
                        select(ArtifactRecord)
                        .where(ArtifactRecord.run_id == run.id)
                        .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
                    )
                )
                if run is not None
                else []
            )
            return {
                "analysisApproval": (
                    {
                        "decision": approval.user_decision,
                        "intentHash": approval.intent_hash,
                        "schemaVersion": approval.payload_schema_version,
                    }
                    if approval is not None
                    else None
                ),
                "analysisIntent": (
                    {
                        "codeSha256": hashlib.sha256(intent.code.encode("utf-8")).hexdigest(),
                        "decision": intent.decision,
                        "expectedOutputs": intent.expected_outputs,
                        "id": intent.id,
                        "payloadSha256": intent.payload_sha256,
                        "status": intent.status,
                    }
                    if intent is not None
                    else None
                ),
                "analysisRun": (
                    {
                        "analysisIntentId": run.analysis_intent_id,
                        "environmentHash": run.environment_hash,
                        "id": run.id,
                        "inputArtifacts": run.input_artifacts,
                        "logsPath": run.logs_path,
                        "outputArtifacts": run.output_artifacts,
                        "status": run.status,
                    }
                    if run is not None
                    else None
                ),
                "artifacts": [
                    {
                        "contentHash": artifact.content_hash,
                        "id": artifact.id,
                        "metadata": artifact.metadata_json,
                        "mimeType": artifact.mime_type,
                        "parentArtifacts": artifact.parent_artifacts,
                        "path": artifact.path,
                        "type": artifact.artifact_type,
                    }
                    for artifact in artifacts
                ],
                "datasetContentHash": workflow.dataset_content_hash,
                "datasetSourceId": workflow.dataset_source_id,
                "handlerVersion": selected_handler_version,
                "kind": kind,
                "planEnvelope": plan_job_envelope(session, workflow, approved_plan),
                "tasks": [
                    {
                        "id": task.id,
                        "materializationSha256": task_materialization_hash(task),
                        "outputs": task.outputs,
                        "status": task.status,
                    }
                    for task in tasks
                ],
                "workflowId": workflow.id,
            }
        legacy_handler = selected_handler_version == LEGACY_HANDLER_VERSIONS["review-workflow"]
        approved_plan = None
        if not legacy_handler:
            approved_plan = session.scalar(
                select(PlanRecord).where(
                    PlanRecord.workflow_id == workflow.id,
                    PlanRecord.status == "approved",
                )
            )
        answers = list(
            session.scalars(
                select(AnswerRecord)
                .where(AnswerRecord.workflow_id == workflow.id)
                .order_by(AnswerRecord.created_at)
            )
        )
        if not legacy_handler:
            answers.sort(key=lambda answer: (answer.created_at, answer.id))
        claims: list[dict[str, Any]] = []
        answer_inputs: list[dict[str, Any]] = []
        for answer in answers:
            answer_inputs.append(
                {
                    "answerId": answer.id,
                    "projectId": answer.project_id,
                    "questionSha256": hashlib.sha256(answer.question.encode("utf-8")).hexdigest(),
                    "summarySha256": hashlib.sha256(answer.answer.encode("utf-8")).hexdigest(),
                    "taskId": answer.task_id,
                    "unresolvedQuestionsSha256": content_sha256(answer.unresolved_questions),
                    "generator": answer.generator,
                    "model": answer.model,
                    "promptVersion": answer.prompt_version,
                    "metadataSha256": content_sha256(answer.metadata_json),
                    "workflowId": answer.workflow_id,
                }
            )
            for claim in session.scalars(
                select(ClaimRecord).where(ClaimRecord.answer_id == answer.id)
            ):
                links = list(
                    session.scalars(
                        select(ClaimEvidenceRecord).where(ClaimEvidenceRecord.claim_id == claim.id)
                    )
                )
                evidence_inputs: list[dict[str, Any]] = []
                for link in links:
                    evidence = session.get(EvidenceSpanRecord, link.evidence_id)
                    evidence_input: dict[str, Any] = {
                        "evidenceId": link.evidence_id,
                        "relationship": link.relationship_kind,
                        "sourceId": evidence.source_id if evidence is not None else None,
                        "pageIndex": evidence.page_index if evidence is not None else None,
                        "textSha256": hashlib.sha256(evidence.text.encode("utf-8")).hexdigest()
                        if evidence is not None
                        else None,
                        "quoteHash": evidence.quote_hash if evidence is not None else None,
                        "verified": evidence.verified if evidence is not None else None,
                    }
                    if not legacy_handler:
                        evidence_input.update(
                            {
                                "bboxSha256": content_sha256(evidence.bbox)
                                if evidence is not None
                                else None,
                                "confidence": evidence.confidence if evidence is not None else None,
                                "coordinateSpace": evidence.coordinate_space
                                if evidence is not None
                                else None,
                                "extractionMethod": evidence.extraction_method
                                if evidence is not None
                                else None,
                                "pageLabel": evidence.page_label if evidence is not None else None,
                            }
                        )
                    evidence_inputs.append(evidence_input)
                claim_input: dict[str, Any] = {
                    "claimId": claim.id,
                    "statementSha256": hashlib.sha256(claim.statement.encode("utf-8")).hexdigest(),
                    "evidence": sorted(
                        evidence_inputs,
                        key=lambda item: item["evidenceId"],
                    ),
                }
                if not legacy_handler:
                    claim_input.update(
                        {
                            "claimType": claim.claim_type,
                            "confidence": claim.confidence,
                            "reviewStatus": claim.review_status,
                        }
                    )
                claims.append(claim_input)
        if not legacy_handler:
            claims.sort(key=lambda claim: claim["claimId"])
        payload = {
            "claims": claims,
            "handlerVersion": selected_handler_version,
            "kind": kind,
            "workflowId": workflow.id,
        }
        if not legacy_handler:
            payload["answers"] = answer_inputs
            payload["planEnvelope"] = plan_job_envelope(
                session,
                workflow,
                approved_plan,
            )
        return payload
    if task is None:
        raise ValueError("execute-task jobs require a task")
    # Discovery operations deliberately bind the job identity to the exact
    # approved query/provider envelope.  Adding generic plan history here
    # would weaken the adapter's persist-before-send authority check.
    if task.task_type == "paper-discovery":
        return task.inputs
    previous = list(
        session.scalars(
            select(TaskRecord)
            .where(
                TaskRecord.workflow_id == workflow.id,
                TaskRecord.plan_id == task.plan_id,
                TaskRecord.order_index < task.order_index,
            )
            .order_by(TaskRecord.order_index)
        )
    )
    payload = {
        "handlerVersion": selected_handler_version,
        "kind": kind,
        "previousOutputs": [item.outputs for item in previous],
        "taskId": task.id,
        "taskInputSha256": task.input_sha256,
        "taskType": task.task_type,
        "workflowId": workflow.id,
    }
    if selected_handler_version != LEGACY_HANDLER_VERSIONS["execute-task"]:
        plan = session.get(PlanRecord, task.plan_id) if task.plan_id is not None else None
        payload.update(
            {
                "planEnvelope": plan_job_envelope(session, workflow, plan),
                "taskMaterializationSha256": task_materialization_hash(task),
            }
        )
    if workflow.workflow_type == "dataset-analysis" and task.task_type == "python-data-analysis":
        intent = session.scalar(
            select(AnalysisIntentRecord).where(
                AnalysisIntentRecord.workflow_id == workflow.id,
                AnalysisIntentRecord.task_id == task.id,
                AnalysisIntentRecord.status.in_(["approved", "executing"]),
                AnalysisIntentRecord.decision == "approved",
            )
        )
        approval = (
            session.scalar(
                select(ApprovalRecord).where(
                    ApprovalRecord.workflow_id == workflow.id,
                    ApprovalRecord.task_id == task.id,
                    ApprovalRecord.subject_type == "analysis-intent",
                    ApprovalRecord.subject_id == intent.id,
                    ApprovalRecord.user_decision == "approved",
                )
            )
            if intent is not None
            else None
        )
        payload["analysisIntentEnvelope"] = (
            {
                "analysisIntentId": intent.id,
                "approvalIntentHash": approval.intent_hash if approval is not None else None,
                "approvalSchemaVersion": (
                    approval.payload_schema_version if approval is not None else None
                ),
                "payloadSha256": intent.payload_sha256,
            }
            if intent is not None
            else None
        )
    return payload


def current_job_input_hash(
    session: Session,
    workflow: WorkflowRecord,
    *,
    kind: str,
    task: TaskRecord | None,
) -> str:
    return content_sha256(job_input_payload(session, workflow, kind=kind, task=task))


def agent_control_job_input_payload(
    session: Session,
    workflow: WorkflowRecord,
    *,
    kind: str,
    operation_key: str,
    handler_version: str,
) -> dict[str, Any]:
    if kind == "observe-step":
        source_job_id = _agent_operation_subject(workflow.id, operation_key, "observe")
        source = session.get(JobRecord, source_job_id)
        if source is None or source.workflow_id != workflow.id:
            raise WorkflowConflict(
                "agent-observation-source-missing",
                "The observation job has no workflow-owned source job.",
            )
        return {
            "attempt": source.attempt,
            "errorCode": source.error_code,
            "handlerVersion": handler_version,
            "kind": kind,
            "sourceInputSha256": source.input_sha256,
            "sourceJobId": source.id,
            "sourceJobKind": source.kind,
            "sourceStatus": source.status,
            "taskId": source.task_id,
            "workflowId": workflow.id,
        }
    if kind == "decide-next-action":
        observation_id = _agent_operation_subject(workflow.id, operation_key, "decide")
        observation = session.get(StepObservationRecord, observation_id)
        if observation is None or observation.workflow_id != workflow.id:
            raise WorkflowConflict(
                "agent-observation-missing",
                "The decision job has no workflow-owned observation.",
            )
        return {
            "handlerVersion": handler_version,
            "kind": kind,
            "observationId": observation.id,
            "observationSha256": observation.output_sha256,
            "workflowId": workflow.id,
        }
    if kind == "apply-agent-decision":
        decision_id = _agent_operation_subject(workflow.id, operation_key, "apply-decision")
        decision = session.get(AgentDecisionRecord, decision_id)
        if decision is None or decision.workflow_id != workflow.id:
            raise WorkflowConflict(
                "agent-decision-missing",
                "The apply job has no workflow-owned decision.",
            )
        return {
            "decisionId": decision.id,
            "decisionSha256": decision.output_sha256,
            "handlerVersion": handler_version,
            "kind": kind,
            "workflowId": workflow.id,
        }
    raise ValueError("unsupported agent control job kind")


def _agent_operation_subject(
    workflow_id: str,
    operation_key: str,
    operation: str,
) -> str:
    prefix = f"workflow:{workflow_id}:{operation}:"
    if not operation_key.startswith(prefix):
        raise WorkflowConflict(
            "agent-operation-key-invalid",
            "The agent control job identity is invalid.",
        )
    subject = operation_key.removeprefix(prefix)
    if not subject or ":" in subject or len(subject) > 36:
        raise WorkflowConflict(
            "agent-operation-key-invalid",
            "The agent control job subject is invalid.",
        )
    return subject


def handler_version_for(kind: str) -> str:
    return {
        "route-intent": ROUTER_HANDLER_VERSION,
        "generate-plan": PLAN_HANDLER_VERSION,
        "execute-task": TASK_HANDLER_VERSION,
        "review-workflow": REVIEW_HANDLER_VERSION,
        "observe-step": "agent-observer-v1",
        "decide-next-action": "agent-next-action-v1",
        "apply-agent-decision": "agent-decision-apply-v1",
    }[kind]


def analysis_execution_operation_key(workflow_id: str, intent_id: str) -> str:
    return f"workflow:{workflow_id}:analysis-intent:{intent_id}"


def job_input_hash_for_handler_version(
    session: Session,
    workflow: WorkflowRecord,
    *,
    kind: str,
    task: TaskRecord | None,
    handler_version: str,
    operation_key: str | None = None,
) -> str:
    if kind in {"observe-step", "decide-next-action", "apply-agent-decision"}:
        if operation_key is None:
            raise ValueError("agent control jobs require an operation key")
        return content_sha256(
            agent_control_job_input_payload(
                session,
                workflow,
                kind=kind,
                operation_key=operation_key,
                handler_version=handler_version,
            )
        )
    return content_sha256(
        job_input_payload(
            session,
            workflow,
            kind=kind,
            task=task,
            handler_version=handler_version,
        )
    )


def job_input_compatibility(
    session: Session,
    workflow: WorkflowRecord,
    job: JobRecord,
    task: TaskRecord | None,
) -> str | None:
    current_version = handler_version_for(job.kind)
    if job.handler_version == current_version:
        expected_hash = (
            content_sha256(
                agent_control_job_input_payload(
                    session,
                    workflow,
                    kind=job.kind,
                    operation_key=job.operation_key,
                    handler_version=job.handler_version,
                )
            )
            if job.kind in {"observe-step", "decide-next-action", "apply-agent-decision"}
            else current_job_input_hash(session, workflow, kind=job.kind, task=task)
        )
        return "current" if job.input_sha256 == expected_hash else None
    legacy_version = LEGACY_HANDLER_VERSIONS.get(job.kind)
    if (
        legacy_version is None
        or job.handler_version != legacy_version
        or workflow.generation_mode != "local-deterministic"
    ):
        return None
    expected_hash = job_input_hash_for_handler_version(
        session,
        workflow,
        kind=job.kind,
        task=task,
        handler_version=legacy_version,
    )
    return "legacy" if job.input_sha256 == expected_hash else None


def enqueue_job(
    session: Session,
    workflow: WorkflowRecord,
    *,
    kind: str,
    operation_key: str,
    task: TaskRecord | None = None,
    attempt: int = 1,
    previous_job_id: str | None = None,
    request_idempotency_key: str | None = None,
    request_payload_sha256: str | None = None,
    delay_seconds: float = 0,
    handler_version: str | None = None,
) -> JobRecord:
    if (request_idempotency_key is None) != (request_payload_sha256 is None):
        raise WorkflowConflict(
            "job-request-binding-invalid",
            "A workflow job request key and its canonical payload hash must be stored together.",
        )
    selected_handler_version = handler_version or handler_version_for(kind)
    current_handler_version = handler_version_for(kind)
    legacy_handler_version = LEGACY_HANDLER_VERSIONS.get(kind)
    allowed_versions = {current_handler_version}
    if legacy_handler_version is not None:
        allowed_versions.add(legacy_handler_version)
    if selected_handler_version not in allowed_versions:
        raise WorkflowConflict(
            "unsupported-handler-version",
            "The workflow job handler version is not supported.",
        )
    if (
        legacy_handler_version is not None
        and selected_handler_version == legacy_handler_version
        and workflow.generation_mode != "local-deterministic"
    ):
        raise WorkflowConflict(
            "legacy-handler-mode-invalid",
            "Previous workflow handlers may only resume local deterministic workflows.",
        )
    input_hash = job_input_hash_for_handler_version(
        session,
        workflow,
        kind=kind,
        task=task,
        handler_version=selected_handler_version,
        operation_key=operation_key,
    )
    existing = session.scalar(
        select(JobRecord).where(
            JobRecord.operation_key == operation_key,
            JobRecord.attempt == attempt,
        )
    )
    if existing is not None:
        if existing.input_sha256 != input_hash:
            raise WorkflowConflict(
                "job-input-conflict",
                "A job with this identity already exists for different inputs.",
            )
        if request_idempotency_key is not None:
            if (
                existing.request_idempotency_key is not None
                and existing.request_idempotency_key != request_idempotency_key
            ):
                raise WorkflowConflict(
                    "workflow-revision-conflict",
                    "The workflow changed before this action was applied. Reload it and try again.",
                    retryable=True,
                )
            if (
                existing.request_idempotency_key != request_idempotency_key
                or existing.request_payload_sha256 != request_payload_sha256
            ):
                raise WorkflowConflict(
                    "idempotency-key-reused",
                    "This Idempotency-Key was already used with a different workflow request.",
                )
        return existing
    job = JobRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        task_id=task.id if task is not None else None,
        kind=kind,
        operation_key=operation_key,
        attempt=attempt,
        input_sha256=input_hash,
        handler_version=selected_handler_version,
        status="queued",
        available_at=utc_now() + timedelta(seconds=max(0, delay_seconds)),
        request_idempotency_key=request_idempotency_key,
        request_payload_sha256=request_payload_sha256,
        previous_job_id=previous_job_id,
    )
    session.add(job)
    session.flush()
    return job


def latest_active_job(session: Session, workflow_id: str) -> JobRecord | None:
    return session.scalar(
        select(JobRecord)
        .where(
            JobRecord.workflow_id == workflow_id,
            JobRecord.status.in_(["queued", "leased"]),
        )
        .order_by(JobRecord.created_at.desc())
    )


def retry_delay_seconds(attempt: int) -> float:
    return float(min(30, 2 ** max(0, attempt - 1)))
