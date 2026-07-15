from __future__ import annotations

import uuid
from typing import Any, Sequence, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from ...models import EventRecord, TaskRecord, WorkflowRecord, utc_now
from ..schemas import (
    StatusChangedEventData,
    WorkflowEventData,
    WorkflowEventOut,
    WorkflowEventsOut,
    WorkflowEventType,
    WorkflowStatus,
)
from ..state import task_transition_allowed, workflow_transition_allowed
from .integrity import WorkflowConflict, model_payload


def append_workflow_events(
    session: Session,
    workflow: WorkflowRecord,
    entries: Sequence[tuple[str, WorkflowEventData, str | None, str | None]],
) -> list[EventRecord]:
    if not entries:
        return []
    sequence_result = session.execute(
        update(WorkflowRecord)
        .where(WorkflowRecord.id == workflow.id)
        .values(event_sequence=WorkflowRecord.event_sequence + len(entries))
        .returning(WorkflowRecord.event_sequence)
        .execution_options(synchronize_session=False)
    ).scalar_one()
    first_sequence = sequence_result - len(entries) + 1
    records: list[EventRecord] = []
    for offset, (event_type, data, task_id, job_id) in enumerate(entries):
        record = EventRecord(
            id=str(uuid.uuid4()),
            project_id=workflow.project_id,
            workflow_id=workflow.id,
            task_id=task_id,
            job_id=job_id,
            sequence=first_sequence + offset,
            event_type=event_type,
            payload=model_payload(data),
        )
        records.append(record)
        session.add(record)
    return records


def transition_workflow(
    session: Session,
    workflow: WorkflowRecord,
    target: str,
    *,
    expected_revision: int | None = None,
    reason_code: str | None = None,
    blocking_message: str | None = None,
    retryable: bool = False,
) -> WorkflowRecord:
    current = workflow.status
    if current == target:
        return workflow
    if not workflow_transition_allowed(current, target):
        raise WorkflowConflict(
            "invalid-workflow-transition",
            f"Workflow cannot move from {current} to {target}.",
        )
    revision = workflow.row_version if expected_revision is None else expected_revision
    now = utc_now()
    values: dict[str, Any] = {
        "status": target,
        "row_version": WorkflowRecord.row_version + 1,
        "updated_at": now,
        "blocking_code": reason_code if target == "blocked" else None,
        "blocking_message": blocking_message if target == "blocked" else None,
    }
    if target in {"completed", "cancelled"}:
        values["finished_at"] = now
    elif current in {"failed", "blocked"}:
        values["finished_at"] = None
    result = session.execute(
        update(WorkflowRecord)
        .where(
            WorkflowRecord.id == workflow.id,
            WorkflowRecord.row_version == revision,
            WorkflowRecord.status == current,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[object], result).rowcount != 1:
        session.expire_all()
        raise WorkflowConflict(
            "workflow-revision-conflict",
            "The workflow changed before this action was applied. Reload it and try again.",
            retryable=True,
        )
    session.flush()
    session.refresh(workflow)
    append_workflow_events(
        session,
        workflow,
        [
            (
                "workflow.status-changed",
                StatusChangedEventData(
                    previous_status=cast(WorkflowStatus, current),
                    status=cast(WorkflowStatus, target),
                    reason_code=reason_code,
                ),
                None,
                None,
            )
        ],
    )
    if target == "blocked" and reason_code:
        # Retryability is intentionally present only in the public snapshot. The
        # durable blocker itself is a stable code plus user-safe message.
        workflow.last_error_code = reason_code if retryable else workflow.last_error_code
    return workflow


def transition_task(session: Session, task: TaskRecord, target: str) -> TaskRecord:
    current = task.status
    if current == target:
        return task
    if not task_transition_allowed(current, target):
        raise WorkflowConflict(
            "invalid-task-transition",
            f"Task cannot move from {current} to {target}.",
        )
    now = utc_now()
    values: dict[str, Any] = {
        "status": target,
        "row_version": TaskRecord.row_version + 1,
        "updated_at": now,
    }
    if target == "running" and task.started_at is None:
        values["started_at"] = now
    if target in {"completed", "failed", "blocked", "cancelled"}:
        values["finished_at"] = now
    if target in {"queued", "waiting-approval"}:
        values["finished_at"] = None
    result = session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.id == task.id,
            TaskRecord.row_version == task.row_version,
            TaskRecord.status == current,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[object], result).rowcount != 1:
        session.expire_all()
        raise WorkflowConflict(
            "task-revision-conflict",
            "The workflow step changed before this action was applied.",
            retryable=True,
        )
    session.flush()
    session.refresh(task)
    return task


def workflow_events(
    session: Session,
    workflow: WorkflowRecord,
    *,
    after: int,
    limit: int,
) -> WorkflowEventsOut:
    records = list(
        session.scalars(
            select(EventRecord)
            .where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.sequence > after,
            )
            .order_by(EventRecord.sequence)
            .limit(limit + 1)
        )
    )
    has_more = len(records) > limit
    page = records[:limit]
    events = [
        WorkflowEventOut(
            id=record.id,
            sequence=record.sequence or 0,
            type=cast(WorkflowEventType, record.event_type),
            task_id=record.task_id,
            job_id=record.job_id,
            data=cast(WorkflowEventData, record.payload),
            created_at=record.created_at,
        )
        for record in page
    ]
    return WorkflowEventsOut(
        events=events,
        next_after=events[-1].sequence if events else after,
        has_more=has_more,
    )
