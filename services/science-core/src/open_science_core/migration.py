from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import URL

from .config import settings

BASELINE_REVISION = "0001_legacy_baseline"

ColumnSignature = tuple[str, str, int, int, str | None]
ForeignKeySignature = tuple[str, str, str, str, str]
IndexSignature = tuple[str, str, int, tuple[str, ...], int]


class DatabaseMigrationError(RuntimeError):
    """The database could not be proven safe to migrate."""


# This is deliberately independent of models.py. It is the immutable shape
# emitted by Base.metadata.create_all before Alembic was introduced. Future
# model changes must add a new revision, never change this fingerprint.
LEGACY_COLUMNS: dict[str, tuple[ColumnSignature, ...]] = {
    "analysis_intents": (
        ("id", "VARCHAR(36)", 1, 1, None),
        ("task_id", "VARCHAR(36)", 1, 0, None),
        ("project_id", "VARCHAR(36)", 1, 0, None),
        ("dataset_source_id", "VARCHAR(36)", 1, 0, None),
        ("objective", "TEXT", 1, 0, None),
        ("code", "TEXT", 1, 0, None),
        ("payload_sha256", "VARCHAR(64)", 1, 0, None),
        ("status", "VARCHAR(64)", 1, 0, None),
        ("decision", "VARCHAR(32)", 0, 0, None),
        ("created_at", "DATETIME", 1, 0, None),
        ("updated_at", "DATETIME", 1, 0, None),
    ),
    "answers": (
        ("id", "VARCHAR(36)", 1, 1, None),
        ("project_id", "VARCHAR(36)", 1, 0, None),
        ("question", "TEXT", 1, 0, None),
        ("answer", "TEXT", 1, 0, None),
        ("unresolved_questions", "JSON", 1, 0, None),
        ("created_at", "DATETIME", 1, 0, None),
    ),
    "approvals": (
        ("id", "VARCHAR(36)", 1, 1, None),
        ("task_id", "VARCHAR(36)", 1, 0, None),
        ("intent_hash", "VARCHAR(64)", 1, 0, None),
        ("requested_action", "VARCHAR(200)", 1, 0, None),
        ("risk_level", "VARCHAR(32)", 1, 0, None),
        ("reason", "TEXT", 1, 0, None),
        ("affected_resources", "JSON", 1, 0, None),
        ("user_decision", "VARCHAR(32)", 0, 0, None),
        ("created_at", "DATETIME", 1, 0, None),
        ("decided_at", "DATETIME", 0, 0, None),
    ),
    "artifacts": (
        ("id", "VARCHAR(36)", 1, 1, None),
        ("run_id", "VARCHAR(36)", 1, 0, None),
        ("artifact_type", "VARCHAR(64)", 1, 0, None),
        ("path", "TEXT", 1, 0, None),
        ("mime_type", "VARCHAR(200)", 1, 0, None),
        ("content_hash", "VARCHAR(64)", 1, 0, None),
        ("parent_artifacts", "JSON", 1, 0, None),
        ("metadata_json", "JSON", 1, 0, None),
        ("created_at", "DATETIME", 1, 0, None),
    ),
    "claim_evidence": (
        ("claim_id", "VARCHAR(36)", 1, 1, None),
        ("evidence_id", "VARCHAR(36)", 1, 2, None),
        ("relationship_kind", "VARCHAR(32)", 1, 0, None),
    ),
    "claims": (
        ("id", "VARCHAR(36)", 1, 1, None),
        ("answer_id", "VARCHAR(36)", 1, 0, None),
        ("statement", "TEXT", 1, 0, None),
        ("claim_type", "VARCHAR(32)", 1, 0, None),
        ("confidence", "FLOAT", 1, 0, None),
        ("review_status", "VARCHAR(32)", 1, 0, None),
    ),
    "events": (
        ("id", "VARCHAR(36)", 1, 1, None),
        ("project_id", "VARCHAR(36)", 1, 0, None),
        ("event_type", "VARCHAR(100)", 1, 0, None),
        ("payload", "JSON", 1, 0, None),
        ("created_at", "DATETIME", 1, 0, None),
    ),
    "evidence_spans": (
        ("id", "VARCHAR(36)", 1, 1, None),
        ("source_id", "VARCHAR(36)", 1, 0, None),
        ("page_index", "INTEGER", 1, 0, None),
        ("page_label", "VARCHAR(32)", 0, 0, None),
        ("text", "TEXT", 1, 0, None),
        ("bbox", "JSON", 0, 0, None),
        ("coordinate_space", "VARCHAR(64)", 1, 0, None),
        ("quote_hash", "VARCHAR(64)", 1, 0, None),
        ("extraction_method", "VARCHAR(100)", 1, 0, None),
        ("confidence", "FLOAT", 1, 0, None),
        ("verified", "BOOLEAN", 1, 0, None),
    ),
    "projects": (
        ("id", "VARCHAR(36)", 1, 1, None),
        ("title", "VARCHAR(300)", 1, 0, None),
        ("description", "TEXT", 1, 0, None),
        ("project_path", "TEXT", 1, 0, None),
        ("research_domain", "VARCHAR(200)", 0, 0, None),
        ("execution_mode", "VARCHAR(32)", 1, 0, None),
        ("created_at", "DATETIME", 1, 0, None),
        ("updated_at", "DATETIME", 1, 0, None),
    ),
    "runs": (
        ("id", "VARCHAR(36)", 1, 1, None),
        ("task_id", "VARCHAR(36)", 1, 0, None),
        ("model", "VARCHAR(200)", 0, 0, None),
        ("prompt_version", "VARCHAR(100)", 0, 0, None),
        ("environment_hash", "VARCHAR(64)", 0, 0, None),
        ("input_artifacts", "JSON", 1, 0, None),
        ("output_artifacts", "JSON", 1, 0, None),
        ("logs_path", "TEXT", 0, 0, None),
        ("token_usage", "JSON", 1, 0, None),
        ("cost", "FLOAT", 1, 0, None),
        ("status", "VARCHAR(64)", 1, 0, None),
        ("created_at", "DATETIME", 1, 0, None),
        ("finished_at", "DATETIME", 0, 0, None),
    ),
    "source_pages": (
        ("id", "INTEGER", 1, 1, None),
        ("source_id", "VARCHAR(36)", 1, 0, None),
        ("page_index", "INTEGER", 1, 0, None),
        ("page_label", "VARCHAR(32)", 0, 0, None),
        ("width", "FLOAT", 1, 0, None),
        ("height", "FLOAT", 1, 0, None),
        ("text", "TEXT", 1, 0, None),
        ("words", "JSON", 1, 0, None),
    ),
    "sources": (
        ("id", "VARCHAR(36)", 1, 1, None),
        ("project_id", "VARCHAR(36)", 1, 0, None),
        ("title", "VARCHAR(500)", 1, 0, None),
        ("source_kind", "VARCHAR(32)", 1, 0, None),
        ("authors", "JSON", 1, 0, None),
        ("doi", "VARCHAR(255)", 0, 0, None),
        ("arxiv_id", "VARCHAR(100)", 0, 0, None),
        ("local_path", "TEXT", 1, 0, None),
        ("publication_date", "VARCHAR(32)", 0, 0, None),
        ("ingestion_status", "VARCHAR(32)", 1, 0, None),
        ("content_hash", "VARCHAR(64)", 1, 0, None),
        ("page_count", "INTEGER", 0, 0, None),
        ("created_at", "DATETIME", 1, 0, None),
    ),
    "tasks": (
        ("id", "VARCHAR(36)", 1, 1, None),
        ("project_id", "VARCHAR(36)", 1, 0, None),
        ("objective", "TEXT", 1, 0, None),
        ("task_type", "VARCHAR(64)", 1, 0, None),
        ("inputs", "JSON", 1, 0, None),
        ("expected_outputs", "JSON", 1, 0, None),
        ("acceptance_criteria", "JSON", 1, 0, None),
        ("permissions", "JSON", 1, 0, None),
        ("status", "VARCHAR(64)", 1, 0, None),
        ("retries", "INTEGER", 1, 0, None),
        ("timeout_seconds", "INTEGER", 1, 0, None),
        ("created_at", "DATETIME", 1, 0, None),
        ("updated_at", "DATETIME", 1, 0, None),
    ),
}

LEGACY_FOREIGN_KEYS: dict[str, frozenset[ForeignKeySignature]] = {
    "analysis_intents": frozenset(
        {
            ("tasks", "task_id", "id", "NO ACTION", "CASCADE"),
            ("projects", "project_id", "id", "NO ACTION", "CASCADE"),
            ("sources", "dataset_source_id", "id", "NO ACTION", "RESTRICT"),
        }
    ),
    "answers": frozenset({("projects", "project_id", "id", "NO ACTION", "CASCADE")}),
    "approvals": frozenset({("tasks", "task_id", "id", "NO ACTION", "CASCADE")}),
    "artifacts": frozenset({("runs", "run_id", "id", "NO ACTION", "CASCADE")}),
    "claim_evidence": frozenset(
        {
            ("claims", "claim_id", "id", "NO ACTION", "CASCADE"),
            ("evidence_spans", "evidence_id", "id", "NO ACTION", "CASCADE"),
        }
    ),
    "claims": frozenset({("answers", "answer_id", "id", "NO ACTION", "CASCADE")}),
    "events": frozenset({("projects", "project_id", "id", "NO ACTION", "CASCADE")}),
    "evidence_spans": frozenset({("sources", "source_id", "id", "NO ACTION", "CASCADE")}),
    "projects": frozenset(),
    "runs": frozenset({("tasks", "task_id", "id", "NO ACTION", "CASCADE")}),
    "source_pages": frozenset({("sources", "source_id", "id", "NO ACTION", "CASCADE")}),
    "sources": frozenset({("projects", "project_id", "id", "NO ACTION", "CASCADE")}),
    "tasks": frozenset({("projects", "project_id", "id", "NO ACTION", "CASCADE")}),
}

LEGACY_EXPLICIT_INDEXES: frozenset[IndexSignature] = frozenset(
    {
        ("analysis_intents", "ix_analysis_intents_dataset_source_id", 0, ("dataset_source_id",), 0),
        ("analysis_intents", "ix_analysis_intents_payload_sha256", 0, ("payload_sha256",), 0),
        ("analysis_intents", "ix_analysis_intents_project_id", 0, ("project_id",), 0),
        ("analysis_intents", "ix_analysis_intents_status", 0, ("status",), 0),
        ("analysis_intents", "ix_analysis_intents_task_id", 1, ("task_id",), 0),
        ("answers", "ix_answers_project_id", 0, ("project_id",), 0),
        ("approvals", "ix_approvals_intent_hash", 0, ("intent_hash",), 0),
        ("approvals", "ix_approvals_task_id", 0, ("task_id",), 0),
        ("artifacts", "ix_artifacts_content_hash", 0, ("content_hash",), 0),
        ("artifacts", "ix_artifacts_run_id", 0, ("run_id",), 0),
        ("claims", "ix_claims_answer_id", 0, ("answer_id",), 0),
        ("events", "ix_events_created_at", 0, ("created_at",), 0),
        ("events", "ix_events_event_type", 0, ("event_type",), 0),
        ("events", "ix_events_project_id", 0, ("project_id",), 0),
        ("evidence_spans", "ix_evidence_spans_source_id", 0, ("source_id",), 0),
        ("runs", "ix_runs_task_id", 0, ("task_id",), 0),
        ("source_pages", "ix_source_pages_source_id", 0, ("source_id",), 0),
        ("sources", "ix_sources_content_hash", 0, ("content_hash",), 0),
        ("sources", "ix_sources_project_id", 0, ("project_id",), 0),
        ("tasks", "ix_tasks_project_id", 0, ("project_id",), 0),
    }
)

LEGACY_UNIQUE_CONSTRAINTS: frozenset[tuple[str, tuple[str, ...]]] = frozenset(
    {
        ("projects", ("project_path",)),
        ("source_pages", ("source_id", "page_index")),
        ("sources", ("project_id", "content_hash")),
    }
)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _application_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    return {str(row[0]) for row in rows if row[0] != "alembic_version"}


def _validate_integrity(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        raise DatabaseMigrationError("SQLite integrity_check failed; database was not changed")
    connection.execute("PRAGMA foreign_keys=ON")
    violation = connection.execute("PRAGMA foreign_key_check").fetchone()
    if violation is not None:
        raise DatabaseMigrationError("SQLite foreign_key_check failed; database was not changed")


def _legacy_schema_mismatch(connection: sqlite3.Connection) -> str | None:
    unexpected_objects = list(
        connection.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('trigger', 'view') AND name NOT LIKE 'sqlite_%'"
        )
    )
    if unexpected_objects:
        return "unexpected trigger or view exists"

    actual_tables = _application_tables(connection)
    expected_tables = set(LEGACY_COLUMNS)
    if actual_tables != expected_tables:
        missing = sorted(expected_tables - actual_tables)
        extra = sorted(actual_tables - expected_tables)
        return f"table set differs (missing={missing}, extra={extra})"

    actual_explicit_indexes: set[IndexSignature] = set()
    actual_unique_constraints: set[tuple[str, tuple[str, ...]]] = set()
    for table, expected_columns in LEGACY_COLUMNS.items():
        quoted_table = _quote_identifier(table)
        columns = tuple(
            (
                str(row[1]),
                str(row[2]).upper(),
                int(row[3]),
                int(row[5]),
                None if row[4] is None else str(row[4]),
            )
            for row in connection.execute(f"PRAGMA table_info({quoted_table})")
        )
        if columns != expected_columns:
            return f"column definition differs for {table}"

        foreign_keys = frozenset(
            (
                str(row[2]),
                str(row[3]),
                str(row[4]),
                str(row[5]).upper(),
                str(row[6]).upper(),
            )
            for row in connection.execute(f"PRAGMA foreign_key_list({quoted_table})")
        )
        if foreign_keys != LEGACY_FOREIGN_KEYS[table]:
            return f"foreign keys differ for {table}"

        for row in connection.execute(f"PRAGMA index_list({quoted_table})"):
            index_name = str(row[1])
            unique = int(row[2])
            origin = str(row[3])
            partial = int(row[4])
            quoted_index = _quote_identifier(index_name)
            index_columns = tuple(
                str(item[2]) for item in connection.execute(f"PRAGMA index_info({quoted_index})")
            )
            if origin == "c":
                actual_explicit_indexes.add((table, index_name, unique, index_columns, partial))
            elif origin == "u":
                actual_unique_constraints.add((table, index_columns))

    if actual_explicit_indexes != set(LEGACY_EXPLICIT_INDEXES):
        return "explicit index set differs"
    if actual_unique_constraints != set(LEGACY_UNIQUE_CONSTRAINTS):
        return "unique constraint set differs"
    return None


def alembic_config(database_path: Path) -> Config:
    service_root = Path(__file__).resolve().parents[2]
    ini_path = service_root / "alembic.ini"
    migrations_path = service_root / "migrations"
    if not ini_path.is_file() or not migrations_path.is_dir():
        raise DatabaseMigrationError("Alembic migration resources are missing")
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(migrations_path))
    url = URL.create("sqlite", database=str(database_path.resolve())).render_as_string(
        hide_password=False
    )
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def single_head(config: Config) -> str:
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise DatabaseMigrationError(f"Expected one Alembic head, found {len(heads)}")
    return heads[0]


def _current_revision(connection: sqlite3.Connection) -> str | None:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "alembic_version" not in tables:
        return None
    revisions = [
        str(row[0]) for row in connection.execute("SELECT version_num FROM alembic_version")
    ]
    if len(revisions) != 1:
        raise DatabaseMigrationError("Database must contain exactly one Alembic revision")
    return revisions[0]


def _assert_revision_can_upgrade(config: Config, current: str, head: str) -> None:
    script = ScriptDirectory.from_config(config)
    try:
        script.get_revision(current)
        if current != head:
            list(script.iterate_revisions(head, current))
    except Exception as error:
        raise DatabaseMigrationError(
            f"Database revision {current!r} is unknown or is not an ancestor of {head!r}"
        ) from error


def _backup_database(database_path: Path, current: str, head: str) -> Path:
    backup_dir = database_path.parent / "backups"
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    safe_current = current.replace("/", "-")
    safe_head = head.replace("/", "-")
    backup_path = backup_dir / f"science-core-{safe_current}-to-{safe_head}-{timestamp}.sqlite3"
    with sqlite3.connect(database_path, timeout=5) as source:
        with sqlite3.connect(backup_path) as destination:
            source.backup(destination)
            _validate_integrity(destination)
    try:
        os.chmod(backup_path, 0o600)
    except OSError:
        pass
    return backup_path


def _post_migration_check(database_path: Path, expected_revision: str) -> None:
    with sqlite3.connect(database_path, timeout=5) as connection:
        _validate_integrity(connection)
        current = _current_revision(connection)
        if expected_revision == BASELINE_REVISION:
            mismatch = _legacy_schema_mismatch(connection)
            if mismatch is not None:
                raise DatabaseMigrationError(
                    f"Baseline schema verification failed after migration: {mismatch}"
                )
    if current != expected_revision:
        raise DatabaseMigrationError(
            f"Database revision is {current!r}, expected {expected_revision!r}"
        )
    try:
        os.chmod(database_path, 0o600)
    except OSError:
        pass


def ensure_database(database_path: Path | None = None) -> None:
    """Upgrade an empty, versioned, or exact legacy-v0 SQLite database.

    Unknown and partially-created schemas fail closed. Every existing database
    is backed up with SQLite's online backup API before stamp/upgrade, so WAL
    contents are included in the consistent snapshot.
    """

    path = (database_path or settings.database_path).expanduser().resolve()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    config = alembic_config(path)
    head = single_head(config)

    with sqlite3.connect(path, timeout=5) as connection:
        _validate_integrity(connection)
        application_tables = _application_tables(connection)
        current = _current_revision(connection)

        if not application_tables and current is None:
            mode = "empty"
        elif current is None:
            mismatch = _legacy_schema_mismatch(connection)
            if mismatch is not None:
                raise DatabaseMigrationError(
                    f"Unversioned database is not the frozen legacy schema: {mismatch}"
                )
            mode = "legacy"
        else:
            _assert_revision_can_upgrade(config, current, head)
            if current == BASELINE_REVISION:
                mismatch = _legacy_schema_mismatch(connection)
                if mismatch is not None:
                    raise DatabaseMigrationError(
                        f"Versioned baseline database does not match revision: {mismatch}"
                    )
            mode = "versioned"

    if mode == "empty":
        command.upgrade(config, "head")
    elif mode == "legacy":
        _backup_database(path, "legacy-v0", head)
        command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
    else:
        assert current is not None
        if current != head:
            _backup_database(path, current, head)
            command.upgrade(config, "head")

    _post_migration_check(path, head)


if __name__ == "__main__":
    ensure_database()
