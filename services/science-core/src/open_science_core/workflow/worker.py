from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from ..analysis_service import (
    execute_workflow_analysis_intent,
    recover_interrupted_analysis_state,
)
from ..db import SessionLocal
from ..models import (
    AnalysisIntentRecord,
    JobRecord,
    RunRecord,
    TaskRecord,
    WorkflowRecord,
    utc_now,
)
from ._handlers.dataset import (
    AnalysisServiceExecutor,
    execute_leased_analysis_job,
    recover_leased_analysis_job,
)
from .handlers import (
    execute_leased_job,
    mark_leased_job_started,
    settle_leased_job_error,
)
from .service import cancel_pending_interactions, transition_task, transition_workflow
from .state import WorkflowBlockedError, WorkflowFailure

SessionFactory = Callable[[], Session]
logger = logging.getLogger(__name__)


class WorkflowWorker:
    def __init__(
        self,
        session_factory: SessionFactory = SessionLocal,
        *,
        poll_interval_seconds: float = 0.5,
        lease_seconds: float = 30.0,
        heartbeat_seconds: float = 10.0,
        analysis_executor: AnalysisServiceExecutor = execute_workflow_analysis_intent,
    ) -> None:
        self._session_factory = session_factory
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._analysis_executor = analysis_executor
        self._worker_id = f"science-core-{uuid.uuid4()}"
        self._stop = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._loop_task is not None:
            return
        self._stop.clear()
        await asyncio.to_thread(self.recover)
        self._loop_task = asyncio.create_task(
            self._run_loop(), name="science-core-workflow-worker"
        )

    async def stop(self) -> None:
        self._stop.set()
        task = self._loop_task
        self._loop_task = None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=max(5.0, self._lease_seconds))
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = await self.run_once()
            except Exception as error:
                # A transient SQLite busy/transport error must not permanently
                # kill the only durable worker loop. Do not log the message: it
                # can contain a local database path.
                logger.warning(
                    "workflow worker iteration failed (%s)", type(error).__name__
                )
                worked = False
            if worked:
                continue
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._poll_interval_seconds
                )
            except TimeoutError:
                continue

    async def run_once(self) -> bool:
        # Recovery is part of the steady-state loop, not only startup. A
        # transient settlement failure must not leave a leased job invisible
        # until the process is restarted.
        await asyncio.to_thread(self._recover_expired_jobs)
        await asyncio.to_thread(self._sweep_cancellations)
        claimed = await asyncio.to_thread(self.claim_next_job)
        if claimed is None:
            return False
        job_id, lease_token = claimed
        try:
            await asyncio.to_thread(self._mark_started, job_id, lease_token)
        except Exception as error:
            await asyncio.to_thread(self._settle_error, job_id, lease_token, error)
            return True

        heartbeat = asyncio.create_task(
            self._heartbeat_loop(job_id, lease_token),
            name=f"workflow-heartbeat-{job_id}",
        )
        try:
            await asyncio.to_thread(self._execute, job_id, lease_token)
        except (WorkflowBlockedError, WorkflowFailure) as error:
            await asyncio.to_thread(self._settle_error, job_id, lease_token, error)
        except Exception:
            # The exception object is deliberately not persisted. It may include
            # local paths or document-derived content.
            await asyncio.to_thread(
                self._settle_error,
                job_id,
                lease_token,
                WorkflowFailure(
                    "workflow-handler-error",
                    "The local workflow handler failed unexpectedly.",
                ),
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await heartbeat
        return True

    def recover(self) -> None:
        with self._session_factory() as session:
            recover_interrupted_analysis_state(session)
            session.commit()
        self._recover_expired_jobs()
        self._sweep_cancellations()
        self._recover_orphaned_workflows()

    def _sweep_cancellations(self) -> None:
        now = utc_now()
        with self._session_factory() as session:
            workflows = list(
                session.scalars(
                    select(WorkflowRecord).where(
                        WorkflowRecord.cancel_requested_at.is_not(None),
                        WorkflowRecord.status.not_in(
                            ["completed", "unsupported", "cancelled"]
                        ),
                    )
                )
            )
            for workflow in workflows:
                leased = session.scalar(
                    select(JobRecord.id).where(
                        JobRecord.workflow_id == workflow.id,
                        JobRecord.status == "leased",
                    )
                )
                if leased is not None:
                    continue
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
                        TaskRecord.status.in_(
                            [
                                "pending",
                                "queued",
                                "running",
                                "waiting-approval",
                                "blocked",
                                "failed",
                            ]
                        ),
                    )
                    .values(status="cancelled", finished_at=now, updated_at=now)
                )
                cancel_pending_interactions(session, workflow.id)
                transition_workflow(session, workflow, "cancelled")
            session.commit()

    def claim_next_job(self) -> tuple[str, str] | None:
        now = utc_now()
        token = str(uuid.uuid4())
        with self._session_factory() as session:
            candidate_id = session.scalar(
                select(JobRecord.id)
                .join(WorkflowRecord, WorkflowRecord.id == JobRecord.workflow_id)
                .where(
                    JobRecord.status == "queued",
                    JobRecord.available_at <= now,
                    WorkflowRecord.status.not_in(
                        ["completed", "unsupported", "cancelled"]
                    ),
                    WorkflowRecord.cancel_requested_at.is_(None),
                )
                .order_by(JobRecord.available_at, JobRecord.created_at)
                .limit(1)
            )
            if candidate_id is None:
                return None
            result = session.execute(
                update(JobRecord)
                .where(JobRecord.id == candidate_id, JobRecord.status == "queued")
                .values(
                    status="leased",
                    lease_owner=self._worker_id,
                    lease_token=token,
                    lease_expires_at=now + timedelta(seconds=self._lease_seconds),
                    heartbeat_at=now,
                    updated_at=now,
                )
            )
            if cast(CursorResult[object], result).rowcount != 1:
                session.rollback()
                return None
            session.commit()
            return candidate_id, token

    def _mark_started(self, job_id: str, lease_token: str) -> None:
        with self._session_factory() as session:
            mark_leased_job_started(session, job_id, lease_token)

    def _execute(self, job_id: str, lease_token: str) -> None:
        with self._session_factory() as session:
            job = session.get(JobRecord, job_id)
            task = session.get(TaskRecord, job.task_id) if job and job.task_id else None
            is_analysis_job = (
                job is not None
                and job.kind == "execute-task"
                and task is not None
                and task.task_type == "python-data-analysis"
            )
        if is_analysis_job:
            execute_leased_analysis_job(
                self._session_factory,
                job_id,
                lease_token,
                self._analysis_executor,
            )
            return
        with self._session_factory() as session:
            execute_leased_job(session, job_id, lease_token)

    def _settle_error(self, job_id: str, lease_token: str, error: Exception) -> None:
        with self._session_factory() as session:
            settle_leased_job_error(session, job_id, lease_token, error)

    async def _heartbeat_loop(self, job_id: str, lease_token: str) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._heartbeat_seconds
                )
                return
            except TimeoutError:
                try:
                    alive = await asyncio.to_thread(self._heartbeat, job_id, lease_token)
                    failures = 0
                except Exception as error:
                    failures += 1
                    logger.warning(
                        "workflow heartbeat failed (%s)", type(error).__name__
                    )
                    if failures >= 3:
                        return
                    continue
                if not alive:
                    return

    def _heartbeat(self, job_id: str, lease_token: str) -> bool:
        now = utc_now()
        with self._session_factory() as session:
            result = session.execute(
                update(JobRecord)
                .where(
                    JobRecord.id == job_id,
                    JobRecord.status == "leased",
                    JobRecord.lease_token == lease_token,
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=self._lease_seconds),
                    updated_at=now,
                )
            )
            session.commit()
            return cast(CursorResult[object], result).rowcount == 1

    def _recover_expired_jobs(self) -> None:
        now = utc_now()
        with self._session_factory() as session:
            expired = list(
                session.scalars(
                    select(JobRecord).where(
                        JobRecord.status == "leased",
                        JobRecord.lease_expires_at < now,
                    )
                    .order_by(JobRecord.lease_expires_at, JobRecord.created_at)
                    .limit(20)
                )
            )
            identities: list[tuple[str, str, bool]] = []
            for job in expired:
                if job.lease_token is None:
                    continue
                recovery_token = str(uuid.uuid4())
                claimed = session.execute(
                    update(JobRecord)
                    .where(
                        JobRecord.id == job.id,
                        JobRecord.status == "leased",
                        JobRecord.lease_token == job.lease_token,
                        JobRecord.lease_expires_at < now,
                    )
                    .values(
                        lease_owner=self._worker_id,
                        lease_token=recovery_token,
                        lease_expires_at=now,
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                if cast(CursorResult[object], claimed).rowcount == 1:
                    task = (
                        session.get(TaskRecord, job.task_id)
                        if job.task_id is not None
                        else None
                    )
                    identities.append(
                        (
                            job.id,
                            recovery_token,
                            job.kind == "execute-task"
                            and task is not None
                            and task.task_type == "python-data-analysis",
                        )
                    )
            session.commit()
        for job_id, recovery_token, is_analysis_job in identities:
            if is_analysis_job:
                try:
                    recovered = recover_leased_analysis_job(
                        self._session_factory,
                        job_id,
                        recovery_token,
                    )
                except Exception as error:
                    self._settle_error(job_id, recovery_token, error)
                    continue
                if recovered:
                    continue
            if is_analysis_job and not self._analysis_job_is_unclaimed(job_id):
                self._settle_error(
                    job_id,
                    recovery_token,
                    WorkflowFailure(
                        "analysis-execution-outcome-unknown",
                        "The analysis worker lease expired after execution was claimed; "
                        "reconcile the durable run before retrying.",
                        outcome_unknown=True,
                    ),
                )
                continue
            self._settle_error(
                job_id,
                recovery_token,
                WorkflowFailure(
                    "lease-expired",
                    "The local worker stopped before the deterministic job completed.",
                    retryable=True,
                ),
            )

    def _analysis_job_is_unclaimed(self, job_id: str) -> bool:
        with self._session_factory() as session:
            job = session.get(JobRecord, job_id)
            workflow = (
                session.get(WorkflowRecord, job.workflow_id)
                if job is not None
                else None
            )
            if job is None or workflow is None:
                return False
            prefix = f"workflow:{workflow.id}:analysis-intent:"
            if not job.operation_key.startswith(prefix):
                return False
            intent_id = job.operation_key.removeprefix(prefix)
            intent = session.get(AnalysisIntentRecord, intent_id)
            run = session.scalar(
                select(RunRecord.id).where(RunRecord.analysis_intent_id == intent_id)
            )
            return intent is not None and intent.status == "approved" and run is None

    def _recover_orphaned_workflows(self) -> None:
        with self._session_factory() as session:
            running_tasks = list(
                session.scalars(
                    select(TaskRecord).where(TaskRecord.status == "running")
                )
            )
            for task in running_tasks:
                if task.workflow_id is None:
                    continue
                active = session.scalar(
                    select(JobRecord.id).where(
                        JobRecord.task_id == task.id,
                        JobRecord.status.in_(["queued", "leased"]),
                    )
                )
                if active is not None:
                    continue
                workflow = session.get(WorkflowRecord, task.workflow_id)
                if workflow is None or workflow.status != "running":
                    continue
                transition_task(session, task, "blocked")
                transition_workflow(
                    session,
                    workflow,
                    "blocked",
                    reason_code="orphan-running-task",
                    blocking_message=(
                        "A running step has no durable worker job; inspect it before retrying."
                    ),
                )
            session.commit()
