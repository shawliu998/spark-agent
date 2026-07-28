# pyright: reportPrivateUsage=false

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from typing import Any, Protocol, cast

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from open_science_core.api.screening import _begin_immediate, get_session, router
from open_science_core.db import Base
from open_science_core.models import (
    ProjectRecord,
    ScreeningDecisionRecord,
    SourceRecord,
)


class _RequestClient(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Response: ...


class TypedTestClient(TestClient):
    def get(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("GET", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("PUT", url, **kwargs)


class _CodedSqliteOperationalError(sqlite3.OperationalError):
    sqlite_errorcode: int

    def __init__(self, code: int) -> None:
        super().__init__(f"sqlite failure {code}")
        self.sqlite_errorcode = code


class _FailingSession:
    def __init__(self, error: OperationalError) -> None:
        self.error = error
        self.rollback_called = False

    def execute(self, _statement: object) -> None:
        raise self.error

    def rollback(self) -> None:
        self.rollback_called = True


def _project(project_id: str) -> ProjectRecord:
    return ProjectRecord(
        id=project_id,
        title=project_id,
        description="",
        project_path=f"/tmp/{project_id}",
        research_domain=None,
        execution_mode="safe",
    )


def _source(source_id: str, project_id: str, kind: str = "pdf") -> SourceRecord:
    return SourceRecord(
        id=source_id,
        project_id=project_id,
        title=source_id,
        source_kind=kind,
        authors=[],
        local_path=f"/tmp/{project_id}/{source_id}",
        ingestion_status="ready",
        content_hash=hashlib.sha256(source_id.encode()).hexdigest(),
    )


class TestScreeningApi:
    def setup_method(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        def enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
            dbapi_connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]

        event.listen(self.engine, "connect", enable_foreign_keys)

        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add_all([_project("project-a"), _project("project-b")])
            session.flush()
            pending = _source("a-pending", "project-a")
            pending.ingestion_status = "pending"
            processing = _source("a-processing", "project-a")
            processing.ingestion_status = "processing"
            failed = _source("a-failed", "project-a")
            failed.ingestion_status = "failed"
            session.add_all(
                [
                    _source("a-source", "project-a"),
                    _source("b-source", "project-b"),
                    _source("a-dataset", "project-a", "dataset"),
                    pending,
                    processing,
                    failed,
                ]
            )
            session.commit()

        api = FastAPI()
        api.include_router(router)

        def session_dependency() -> Generator[Session, None, None]:
            with Session(self.engine) as session:
                yield session

        api.dependency_overrides[get_session] = session_dependency
        self.client = TypedTestClient(api)

    def test_create_update_and_stale_version(self) -> None:
        created = self.client.put(
            "/v1/projects/project-a/screening-decisions/a-source",
            json={
                "decision": "exclude",
                "reason": "  Wrong population  ",
                "expectedVersion": 0,
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["rowVersion"] == 1
        assert created.json()["reason"] == "Wrong population"
        assert created.json()["criteriaVersion"] == "screening-v1"

        updated = self.client.put(
            "/v1/projects/project-a/screening-decisions/a-source",
            json={
                "decision": "include",
                "reason": None,
                "criteriaVersion": "protocol-2026-07",
                "expectedVersion": 1,
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["rowVersion"] == 2
        assert updated.json()["criteriaVersion"] == "protocol-2026-07"

        stale = self.client.put(
            "/v1/projects/project-a/screening-decisions/a-source",
            json={"decision": "exclude", "expectedVersion": 1},
        )
        assert stale.status_code == 409

    def test_source_scope_and_pdf_kind_fail_closed(self) -> None:
        cross_project = self.client.put(
            "/v1/projects/project-a/screening-decisions/b-source",
            json={"decision": "include", "expectedVersion": 0},
        )
        missing = self.client.put(
            "/v1/projects/project-a/screening-decisions/missing",
            json={"decision": "include", "expectedVersion": 0},
        )
        dataset = self.client.put(
            "/v1/projects/project-a/screening-decisions/a-dataset",
            json={"decision": "include", "expectedVersion": 0},
        )
        assert cross_project.status_code == 404
        assert missing.status_code == 404
        assert dataset.status_code == 422
        for source_id in ("a-pending", "a-processing", "a-failed"):
            response = self.client.put(
                f"/v1/projects/project-a/screening-decisions/{source_id}",
                json={"decision": "include", "expectedVersion": 0},
            )
            assert response.status_code == 422, (source_id, response.text)

    def test_strict_payload_limits(self) -> None:
        for payload in (
            {"decision": "maybe", "expectedVersion": 0},
            {"decision": "include", "reason": "x" * 2001, "expectedVersion": 0},
            {"decision": "include", "criteriaVersion": " ", "expectedVersion": 0},
            {"decision": "include", "expectedVersion": "0"},
            {"decision": "include", "expectedVersion": 0, "unknown": True},
        ):
            response = self.client.put(
                "/v1/projects/project-a/screening-decisions/a-source", json=payload
            )
            assert response.status_code == 422, (payload, response.text)

    def test_list_is_project_scoped_and_deterministically_ordered(self) -> None:
        with Session(self.engine) as session:
            session.add_all(
                [
                    _source("z-source", "project-a"),
                    _source("m-source", "project-a"),
                ]
            )
            session.flush()
            timestamp = datetime(2026, 7, 22, tzinfo=timezone.utc)
            session.add_all(
                [
                    ScreeningDecisionRecord(
                        id="decision-z",
                        project_id="project-a",
                        source_id="z-source",
                        decision="include",
                        criteria_version="screening-v1",
                        row_version=1,
                        created_at=timestamp,
                        updated_at=timestamp,
                    ),
                    ScreeningDecisionRecord(
                        id="decision-m",
                        project_id="project-a",
                        source_id="m-source",
                        decision="exclude",
                        criteria_version="screening-v1",
                        row_version=1,
                        created_at=timestamp,
                        updated_at=timestamp,
                    ),
                ]
            )
            session.commit()
        response = self.client.get("/v1/projects/project-a/screening-decisions")
        assert response.status_code == 200
        assert [item["sourceId"] for item in response.json()] == ["m-source", "z-source"]
        assert self.client.get("/v1/projects/project-b/screening-decisions").json() == []

    def test_source_and_project_deletion_cascade(self) -> None:
        for source_id in ("a-source", "b-source"):
            project_id = "project-a" if source_id == "a-source" else "project-b"
            response = self.client.put(
                f"/v1/projects/{project_id}/screening-decisions/{source_id}",
                json={"decision": "include", "expectedVersion": 0},
            )
            assert response.status_code == 200
        with Session(self.engine) as session:
            session.delete(session.get(SourceRecord, "a-source"))
            session.delete(session.get(ProjectRecord, "project-b"))
            session.commit()
            assert list(session.scalars(select(ScreeningDecisionRecord))) == []

    def test_database_rejects_cross_project_parent_pair(self) -> None:
        with Session(self.engine) as session:
            session.add(
                ScreeningDecisionRecord(
                    id="cross-project-decision",
                    project_id="project-a",
                    source_id="b-source",
                    decision="include",
                    criteria_version="screening-v1",
                    row_version=1,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
            else:
                raise AssertionError("Composite project/source ownership was not enforced")


def _wal_client(database_path: Path) -> tuple[TypedTestClient, Engine]:
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False, "timeout": 2.0},
    )

    def configure_connection(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]
        dbapi_connection.execute("PRAGMA journal_mode=WAL")  # type: ignore[attr-defined]
        dbapi_connection.execute("PRAGMA busy_timeout=2000")  # type: ignore[attr-defined]

    event.listen(engine, "connect", configure_connection)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(_project("project-wal"))
        session.flush()
        session.add(_source("source-wal", "project-wal"))
        session.commit()

    api = FastAPI()
    api.include_router(router)

    def session_dependency() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    api.dependency_overrides[get_session] = session_dependency
    return TypedTestClient(api), engine


def _race_put(
    client: TypedTestClient,
    barrier: Barrier,
    decision: str,
    expected_version: int,
) -> Response:
    barrier.wait()
    return client.put(
        "/v1/projects/project-wal/screening-decisions/source-wal",
        json={"decision": decision, "expectedVersion": expected_version},
    )


def test_wal_create_and_update_races_have_one_cas_winner() -> None:
    with TemporaryDirectory() as directory:
        client, engine = _wal_client(Path(directory) / "screening.sqlite3")
        for expected_version in (0, 1):
            barrier = Barrier(2)
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(_race_put, client, barrier, decision, expected_version)
                    for decision in ("include", "exclude")
                ]
                responses = [future.result(timeout=5) for future in futures]
            assert sorted(response.status_code for response in responses) == [200, 409]
            winner = next(response.json() for response in responses if response.status_code == 200)
            with Session(engine) as session:
                stored = session.scalar(select(ScreeningDecisionRecord))
                assert stored is not None
                assert stored.row_version == expected_version + 1
                assert stored.decision == winner["decision"]
        engine.dispose()


def test_wal_busy_begin_returns_stable_conflict() -> None:
    with TemporaryDirectory() as directory:
        client, engine = _wal_client(Path(directory) / "busy.sqlite3")
        with engine.connect() as blocker:
            blocker.exec_driver_sql("BEGIN IMMEDIATE")
            response = client.put(
                "/v1/projects/project-wal/screening-decisions/source-wal",
                json={"decision": "include", "expectedVersion": 0},
            )
            assert response.status_code == 409
            assert response.json()["detail"] == (
                "Screening decision is being updated; refresh and retry"
            )
            blocker.rollback()
        engine.dispose()


def test_begin_immediate_maps_only_sqlite_busy_and_locked_to_conflict() -> None:
    for code in (
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_BUSY_SNAPSHOT,
        sqlite3.SQLITE_LOCKED,
    ):
        original = _CodedSqliteOperationalError(code)
        database_error = OperationalError("BEGIN IMMEDIATE", {}, original)
        failing = _FailingSession(database_error)
        try:
            _begin_immediate(cast(Session, failing))
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError(f"SQLite lock code {code} was not mapped to 409")
        assert failing.rollback_called


def test_begin_immediate_preserves_non_lock_operational_errors() -> None:
    originals: tuple[BaseException, ...] = (
        _CodedSqliteOperationalError(sqlite3.SQLITE_IOERR),
        RuntimeError("non-SQLite database failure"),
    )
    for original in originals:
        database_error = OperationalError("BEGIN IMMEDIATE", {}, original)
        failing = _FailingSession(database_error)
        try:
            _begin_immediate(cast(Session, failing))
        except OperationalError as error:
            assert error is database_error
        else:
            raise AssertionError("Non-lock OperationalError was incorrectly mapped to 409")
        assert failing.rollback_called
