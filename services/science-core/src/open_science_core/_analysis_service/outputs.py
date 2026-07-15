from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AnalysisIntentRecord,
    ArtifactRecord,
    ProjectRecord,
    RunRecord,
    SourceRecord,
    TaskRecord,
    WorkflowRecord,
)
from ..schemas import AnalysisArtifactOut, AnalysisRunOut
from .errors import AnalysisServiceError
from .filesystem import read_named_artifact
from .integrity import (
    assert_completed_run_binding,
    assert_failed_run_binding,
    assert_intent_binding,
    assert_persisted_intent_approval,
    project_or_error,
)


def list_project_analysis_runs(
    session: Session,
    project_id: str,
) -> list[AnalysisRunOut]:
    project = project_or_error(session, project_id)
    runs = list(
        session.scalars(
            select(RunRecord)
            .join(TaskRecord, RunRecord.task_id == TaskRecord.id)
            .where(
                TaskRecord.project_id == project_id,
                TaskRecord.task_type == "python-data-analysis",
            )
            .order_by(RunRecord.created_at.desc())
        )
    )
    response: list[AnalysisRunOut] = []
    for run in runs:
        intent = resolve_analysis_intent_for_run(session, run)
        if intent is not None:
            response.append(analysis_run_out(session, run, intent, project))
    return response


def resolve_analysis_intent_for_run(
    session: Session,
    run: RunRecord,
) -> AnalysisIntentRecord | None:
    """Resolve exact lineage, with a unique fallback only for pre-link legacy rows."""

    if run.analysis_intent_id is not None:
        intent = session.get(AnalysisIntentRecord, run.analysis_intent_id)
        if intent is None:
            raise AnalysisServiceError(
                409,
                "Analysis run references a missing intent",
                code="analysis-run-lineage-invalid",
            )
        _assert_resolved_run_identity(session, run, intent, exact_link=True)
        return intent
    candidates = list(
        session.scalars(
            select(AnalysisIntentRecord)
            .where(
                AnalysisIntentRecord.task_id == run.task_id,
                AnalysisIntentRecord.workflow_id.is_(None),
            )
            .order_by(AnalysisIntentRecord.created_at, AnalysisIntentRecord.id)
        )
    )
    if len(candidates) > 1:
        raise AnalysisServiceError(
            409,
            "Legacy analysis run lineage is ambiguous",
            code="analysis-run-lineage-ambiguous",
        )
    if not candidates:
        return None
    intent = candidates[0]
    _assert_resolved_run_identity(session, run, intent, exact_link=False)
    return intent


def _assert_resolved_run_identity(
    session: Session,
    run: RunRecord,
    intent: AnalysisIntentRecord,
    *,
    exact_link: bool,
) -> None:
    task = session.get(TaskRecord, run.task_id)
    project = session.get(ProjectRecord, intent.project_id)
    dataset = session.get(SourceRecord, intent.dataset_source_id)
    workflow = (
        session.get(WorkflowRecord, intent.workflow_id) if intent.workflow_id is not None else None
    )
    if (
        task is None
        or project is None
        or dataset is None
        or (exact_link and run.analysis_intent_id != intent.id)
        or intent.task_id != run.task_id
        or task.project_id != intent.project_id
        or project.id != intent.project_id
        or dataset.project_id != intent.project_id
        or task.workflow_id != intent.workflow_id
        or run.input_artifacts != [intent.dataset_source_id]
        or (intent.workflow_id is not None and workflow is None)
        or (workflow is not None and workflow.project_id != intent.project_id)
    ):
        raise AnalysisServiceError(
            409,
            "Analysis run, intent, task, project, and workflow bindings do not match",
            code="analysis-run-lineage-invalid",
        )


def analysis_run_out(
    session: Session,
    run: RunRecord,
    intent: AnalysisIntentRecord,
    project: ProjectRecord,
) -> AnalysisRunOut:
    task = session.get(TaskRecord, run.task_id)
    dataset = session.get(SourceRecord, intent.dataset_source_id)
    if task is None or dataset is None:
        raise AnalysisServiceError(
            409,
            "Analysis run provenance records are incomplete",
            code="analysis-records-incomplete",
        )
    allow_legacy_null_run_link = False
    if run.analysis_intent_id is None:
        resolved = resolve_analysis_intent_for_run(session, run)
        allow_legacy_null_run_link = resolved is not None and resolved.id == intent.id
    assert_intent_binding(
        session,
        intent,
        task,
        dataset,
        project,
        run=run,
        allow_legacy_null_run_link=allow_legacy_null_run_link,
    )
    assert_persisted_intent_approval(session, intent)
    _assert_run_status_binding(run, intent, task)
    if run.analysis_intent_id is not None and run.status == "completed":
        artifacts = assert_completed_run_binding(session, run, intent)
    elif run.analysis_intent_id is not None and run.status == "failed":
        artifacts = assert_failed_run_binding(session, run, intent)
    else:
        artifacts = list(
            session.scalars(
                select(ArtifactRecord)
                .where(ArtifactRecord.run_id == run.id)
                .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
            )
        )
    run_prefix = f"runs/{run.id}"
    stdout = read_named_artifact(project, artifacts, (f"{run_prefix}/stdout.txt",))
    stderr = read_named_artifact(project, artifacts, (f"{run_prefix}/stderr.txt",))
    log = read_named_artifact(project, artifacts, (run.logs_path,) if run.logs_path else ())
    artifact_outputs: list[AnalysisArtifactOut] = []
    for artifact in artifacts:
        raw_size = artifact.metadata_json.get("sizeBytes", 0)
        size_bytes = raw_size if isinstance(raw_size, int) and raw_size >= 0 else 0
        artifact_outputs.append(
            AnalysisArtifactOut(
                id=artifact.id,
                artifact_type=artifact.artifact_type,
                path=artifact.path,
                mime_type=artifact.mime_type,
                content_hash=artifact.content_hash,
                size_bytes=size_bytes,
                created_at=artifact.created_at,
            )
        )
    return AnalysisRunOut(
        id=run.id,
        intent_id=intent.id,
        task_id=run.task_id,
        project_id=intent.project_id,
        dataset_source_id=intent.dataset_source_id,
        objective=intent.objective,
        code=intent.code,
        payload_sha256=intent.payload_sha256,
        status=run.status,
        environment_hash=run.environment_hash,
        input_artifacts=run.input_artifacts,
        output_artifacts=run.output_artifacts,
        stdout=stdout,
        stderr=stderr,
        log=log,
        logs=log,
        error=(stderr.strip() or log.strip() or "Analysis execution failed")
        if run.status == "failed"
        else None,
        artifacts=artifact_outputs,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def _assert_run_status_binding(
    run: RunRecord,
    intent: AnalysisIntentRecord,
    task: TaskRecord,
) -> None:
    valid = False
    if run.status == "running":
        valid = intent.status == "executing" and task.status == "running"
    elif run.status == "completed":
        valid = intent.status == "completed" and task.status == "completed"
    elif run.status == "failed":
        # A workflow task aggregates every approved repair attempt. After one
        # attempt is sealed as failed, that same task can legitimately advance
        # to a new approval or execution. The historical Run and Intent retain
        # their own exact terminal integrity, so their readability must not
        # depend on the aggregate task remaining in its earlier state.
        # Standalone analysis keeps the strict one-task/one-run binding.
        valid = intent.status == "failed" and (
            intent.workflow_id is not None or task.status == "failed"
        )
    if not valid:
        raise AnalysisServiceError(
            409,
            "Analysis run, intent, and task statuses do not match",
            code="analysis-run-status-invalid",
        )
