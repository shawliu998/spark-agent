from __future__ import annotations

from collections.abc import Generator
from typing import Protocol

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


class _Cursor(Protocol):
    def execute(self, statement: str) -> object: ...

    def close(self) -> None: ...


class _DbapiConnection(Protocol):
    def cursor(self) -> _Cursor: ...


settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.runtime_exchange_dir.mkdir(parents=True, exist_ok=True)
engine: Engine = create_engine(
    f"sqlite:///{settings.database_path}",
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def configure_sqlite(dbapi_connection: _DbapiConnection, _connection_record: object) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def initialize_database() -> None:
    # Alembic owns every schema change. This also recognizes and safely stamps
    # the pre-Alembic MVP schema before applying any later revisions.
    from .migration import ensure_database

    ensure_database(settings.database_path)


def database_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
