from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import (
    AnalysisIntentRecord,
    ArtifactRecord,
    EventRecord,
    ProjectRecord,
    RunRecord,
    TaskRecord,
    WorkflowRecord,
    utc_now,
)
from .errors import AnalysisServiceError, recovery_error_summary
from .filesystem import clear_recovered_run_outputs
from .integrity import (
    assert_completed_run_binding,
    assert_failed_run_binding,
    assert_persisted_intent_approval,
)
from .outputs import resolve_analysis_intent_for_run


def recover_interrupted_analysis_state(session: Session) -> None:
    """Stage deterministic crash recovery without committing the caller session."""

    recovered_at = utc_now()
    running_runs = list(session.scalars(select(RunRecord).where(RunRecord.status == "running")))
    for run in running_runs:
        task = session.get(TaskRecord, run.task_id)
        if task is None or task.task_type != "python-data-analysis":
            continue
        intent: AnalysisIntentRecord | None
        try:
            intent = resolve_analysis_intent_for_run(session, run)
        except AnalysisServiceError:
            intent = None
        _normalize_recovered_run_failure(session, run, recovered_at)
        if intent is None and run.analysis_intent_id is not None:
            canonical_intent = session.get(AnalysisIntentRecord, run.analysis_intent_id)
            canonical_task = (
                session.get(TaskRecord, canonical_intent.task_id)
                if canonical_intent is not None
                else None
            )
            if canonical_intent is not None and canonical_intent.status == "executing":
                canonical_intent.status = "failed"
                if canonical_intent.workflow_id is not None:
                    canonical_intent.error_summary = recovery_error_summary()
            if canonical_task is not None and canonical_task.status != "cancelled":
                canonical_task.status = "failed"
        else:
            if task.status != "cancelled":
                task.status = "failed"
        if intent is not None and intent.status == "executing":
            intent.status = "failed"
            if intent.workflow_id is not None:
                intent.error_summary = recovery_error_summary()
        if (
            intent is None
            and run.analysis_intent_id is None
            and task.workflow_id is None
        ) or (
            intent is not None and intent.workflow_id is None
        ):
            session.add(
                EventRecord(
                    id=str(uuid.uuid4()),
                    project_id=task.project_id,
                    event_type="analysis.run.recovered-after-crash",
                    payload={
                        "analysisIntentId": intent.id if intent is not None else None,
                        "runId": run.id,
                        "recoveredAt": recovered_at.isoformat(),
                        "errorCode": "analysis-interrupted",
                    },
                )
            )

    session.flush()
    orphaned_intents = list(
        session.scalars(
            select(AnalysisIntentRecord).where(AnalysisIntentRecord.status == "executing")
        )
    )
    for intent in orphaned_intents:
        exact_run = session.scalar(
            select(RunRecord).where(RunRecord.analysis_intent_id == intent.id)
        )
        if exact_run is not None:
            task = session.get(TaskRecord, intent.task_id)
            approved_workflow_revision: int | None = None
            try:
                resolved = resolve_analysis_intent_for_run(session, exact_run)
                if resolved is not None:
                    approved_workflow_revision = assert_persisted_intent_approval(
                        session, resolved
                    )
            except AnalysisServiceError:
                _normalize_recovered_run_failure(session, exact_run, recovered_at)
                intent.status = "failed"
                if intent.workflow_id is not None:
                    intent.error_summary = recovery_error_summary()
                if task is not None and task.status != "cancelled":
                    task.status = "failed"
                continue
            if resolved is None or resolved.id != intent.id:
                _normalize_recovered_run_failure(session, exact_run, recovered_at)
                intent.status = "failed"
                if intent.workflow_id is not None:
                    intent.error_summary = recovery_error_summary()
                if task is not None and task.status != "cancelled":
                    task.status = "failed"
                continue
            workflow = (
                session.get(WorkflowRecord, intent.workflow_id)
                if intent.workflow_id is not None
                else None
            )
            execution_cancelled = intent.workflow_id is not None and (
                (task is not None and task.status == "cancelled")
                or (
                    workflow is not None
                    and (
                        workflow.status == "cancelled"
                        or workflow.cancel_requested_at is not None
                    )
                )
            )
            completed_reconcile_blocked = exact_run.status == "completed" and (
                task is None
                or task.status != "running"
                or (
                    intent.workflow_id is not None
                    and (
                        workflow is None
                        or workflow.status != "running"
                        or workflow.cancel_requested_at is not None
                        or workflow.row_version != approved_workflow_revision
                    )
                )
            )
            if execution_cancelled or completed_reconcile_blocked:
                _normalize_recovered_run_failure(session, exact_run, recovered_at)
                intent.status = "failed"
                intent.error_summary = recovery_error_summary()
                if task is not None and task.status != "cancelled":
                    task.status = "failed"
                continue
            if exact_run.status in {"completed", "failed"} and _terminal_run_can_reconcile(
                session, exact_run, intent
            ):
                intent.status = exact_run.status
                if task is not None and task.status != "cancelled":
                    task.status = exact_run.status
                if exact_run.status == "failed":
                    intent.error_summary = recovery_error_summary()
                continue
            _normalize_recovered_run_failure(session, exact_run, recovered_at)
            intent.status = "failed"
            intent.error_summary = recovery_error_summary()
            if task is not None and task.status != "cancelled":
                task.status = "failed"
            continue
        legacy_runs = (
            list(
                session.scalars(
                    select(RunRecord).where(
                        RunRecord.analysis_intent_id.is_(None),
                        RunRecord.task_id == intent.task_id,
                    )
                )
            )
            if intent.workflow_id is None
            else []
        )
        if len(legacy_runs) == 1 and legacy_runs[0].status == "running":
            continue
        task = session.get(TaskRecord, intent.task_id)
        intent.status = "failed"
        if intent.workflow_id is not None:
            intent.error_summary = recovery_error_summary()
        if task is not None:
            task.status = "failed"
            if intent.workflow_id is None:
                session.add(
                    EventRecord(
                        id=str(uuid.uuid4()),
                        project_id=intent.project_id,
                        event_type="analysis.run.recovered-after-crash",
                        payload={
                            "analysisIntentId": intent.id,
                            "runId": None,
                            "recoveredAt": recovered_at.isoformat(),
                            "errorCode": "analysis-interrupted",
                        },
                    )
                )
    session.flush()


def _normalize_recovered_run_failure(
    session: Session,
    run: RunRecord,
    recovered_at: datetime,
) -> None:
    task = session.get(TaskRecord, run.task_id)
    project = (
        session.get(ProjectRecord, task.project_id) if task is not None else None
    )
    if project is not None:
        clear_recovered_run_outputs(Path(project.project_path), run.id)
    session.execute(delete(ArtifactRecord).where(ArtifactRecord.run_id == run.id))
    run.environment_hash = None
    run.output_artifacts = []
    run.logs_path = None
    run.status = "failed"
    run.finished_at = recovered_at


def _terminal_run_can_reconcile(
    session: Session,
    run: RunRecord,
    intent: AnalysisIntentRecord,
) -> bool:
    try:
        if run.status == "completed":
            assert_completed_run_binding(session, run, intent)
        elif run.status == "failed":
            assert_failed_run_binding(session, run, intent)
        else:
            return False
    except AnalysisServiceError:
        return False
    return True
