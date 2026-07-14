from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from alembic import command

from open_science_core import migration


LEGACY_TABLES = tuple(sorted(migration.LEGACY_COLUMNS))
CONTROL_PLANE_TABLES = {
    "workflow_jobs",
    "workflow_plans",
    "workflow_reviews",
    "workflows",
}


def _revision(database_path: Path) -> str | None:
    with sqlite3.connect(database_path) as connection:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
        ).fetchone()
        if table_exists is None:
            return None
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        return None if row is None else str(row[0])


def _table_counts(database_path: Path, tables: tuple[str, ...]) -> dict[str, int]:
    with sqlite3.connect(database_path) as connection:
        return {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables
        }


def _schema_snapshot(database_path: Path) -> list[tuple[str, str, str | None]]:
    with sqlite3.connect(database_path) as connection:
        return [
            (str(row[0]), str(row[1]), None if row[2] is None else str(row[2]))
            for row in connection.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        ]


def _create_unversioned_legacy_database(database_path: Path) -> None:
    config = migration._alembic_config(database_path)
    command.upgrade(config, migration.BASELINE_REVISION)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO projects (
                id, title, description, project_path, research_domain,
                execution_mode, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "project-1",
                "Legacy project",
                "Legacy migration fixture",
                "/tmp/legacy-project",
                "neuroscience",
                "safe",
                "2026-07-14 00:00:00",
                "2026-07-14 00:00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO sources (
                id, project_id, title, source_kind, authors, doi, arxiv_id,
                local_path, publication_date, ingestion_status, content_hash,
                page_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "source-1",
                "project-1",
                "Legacy paper",
                "pdf",
                '["Ada Lovelace"]',
                "10.0000/example",
                None,
                "/tmp/legacy-project/paper.pdf",
                "2026-01-01",
                "ready",
                "a" * 64,
                1,
                "2026-07-14 00:00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO source_pages (
                id, source_id, page_index, page_label, width, height, text, words
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, "source-1", 0, "1", 612.0, 792.0, "Legacy evidence text.", "[]"),
        )
        connection.execute(
            """
            INSERT INTO evidence_spans (
                id, source_id, page_index, page_label, text, bbox,
                coordinate_space, quote_hash, extraction_method, confidence, verified
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evidence-1",
                "source-1",
                0,
                "1",
                "Legacy evidence text.",
                "[0, 0, 100, 20]",
                "pdf-points",
                "b" * 64,
                "pymupdf",
                0.99,
                1,
            ),
        )
        connection.execute(
            """
            INSERT INTO answers (
                id, project_id, question, answer, unresolved_questions, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "answer-1",
                "project-1",
                "What did the legacy paper find?",
                "It contains legacy evidence.",
                "[]",
                "2026-07-14 00:00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO claims (
                id, answer_id, statement, claim_type, confidence, review_status
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("claim-1", "answer-1", "Legacy claim.", "finding", 0.9, "supported"),
        )
        connection.execute(
            """
            INSERT INTO claim_evidence (claim_id, evidence_id, relationship_kind)
            VALUES (?, ?, ?)
            """,
            ("claim-1", "evidence-1", "supports"),
        )
        connection.execute(
            """
            INSERT INTO tasks (
                id, project_id, objective, task_type, inputs, expected_outputs,
                acceptance_criteria, permissions, status, retries, timeout_seconds,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task-1",
                "project-1",
                "Run a legacy analysis",
                "analysis",
                "{}",
                "{}",
                "[]",
                "[]",
                "completed",
                0,
                120,
                "2026-07-14 00:00:00",
                "2026-07-14 00:00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO analysis_intents (
                id, task_id, project_id, dataset_source_id, objective, code,
                payload_sha256, status, decision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "intent-1",
                "task-1",
                "project-1",
                "source-1",
                "Analyze the legacy paper",
                "print('legacy')",
                "c" * 64,
                "approved",
                "approved",
                "2026-07-14 00:00:00",
                "2026-07-14 00:00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO approvals (
                id, task_id, intent_hash, requested_action, risk_level, reason,
                affected_resources, user_decision, created_at, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "approval-1",
                "task-1",
                "d" * 64,
                "execute-analysis",
                "medium",
                "Legacy execution approval",
                '["source-1"]',
                "approved",
                "2026-07-14 00:00:00",
                "2026-07-14 00:01:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO runs (
                id, task_id, model, prompt_version, environment_hash,
                input_artifacts, output_artifacts, logs_path, token_usage,
                cost, status, created_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                "task-1",
                "local-model",
                "v1",
                "e" * 64,
                "[]",
                "[]",
                "/tmp/legacy-project/run.log",
                "{}",
                0.0,
                "completed",
                "2026-07-14 00:00:00",
                "2026-07-14 00:01:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO artifacts (
                id, run_id, artifact_type, path, mime_type, content_hash,
                parent_artifacts, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "artifact-1",
                "run-1",
                "report",
                "/tmp/legacy-project/report.md",
                "text/markdown",
                "f" * 64,
                "[]",
                "{}",
                "2026-07-14 00:01:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO events (id, project_id, event_type, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "event-1",
                "project-1",
                "legacy.completed",
                "{}",
                "2026-07-14 00:01:00",
            ),
        )
        # Pre-Alembic installations have no version table at all. Leaving an
        # empty version table correctly represents a corrupt database instead.
        connection.execute("DROP TABLE alembic_version")
        connection.commit()


class DatabaseMigrationTest(unittest.TestCase):
    def test_fresh_database_upgrades_to_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "fresh.sqlite3"

            migration.ensure_database(database_path)

            config = migration._alembic_config(database_path)
            expected_head = migration._single_head(config)
            self.assertEqual(_revision(database_path), expected_head)
            with sqlite3.connect(database_path) as connection:
                application_tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                        "AND name != 'alembic_version'"
                    )
                }
                self.assertEqual(
                    application_tables,
                    set(LEGACY_TABLES) | CONTROL_PLANE_TABLES,
                )
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_full_legacy_fixture_preserves_every_table_and_backfills_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "legacy.sqlite3"
            _create_unversioned_legacy_database(database_path)
            before = _table_counts(database_path, LEGACY_TABLES)
            self.assertEqual(before, dict.fromkeys(LEGACY_TABLES, 1))

            migration.ensure_database(database_path)

            after = _table_counts(database_path, LEGACY_TABLES)
            self.assertEqual(after, before)
            self.assertEqual(
                _revision(database_path),
                migration._single_head(migration._alembic_config(database_path)),
            )
            with sqlite3.connect(database_path) as connection:
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                approval = connection.execute(
                    """
                    SELECT task_id, workflow_id, plan_id, subject_type, subject_id,
                           payload_schema_version, row_version
                    FROM approvals WHERE id = 'approval-1'
                    """
                ).fetchone()
                self.assertEqual(
                    approval,
                    (
                        "task-1",
                        None,
                        None,
                        "analysis-intent",
                        "intent-1",
                        "analysis-intent-v1",
                        1,
                    ),
                )
            backups = list((root / "backups").glob("science-core-legacy-v0-to-*.sqlite3"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(_table_counts(backups[0], LEGACY_TABLES), before)

    def test_unknown_unversioned_schema_fails_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "unknown.sqlite3"
            with sqlite3.connect(database_path) as connection:
                connection.execute("CREATE TABLE alien_state (id INTEGER PRIMARY KEY, value TEXT)")
                connection.execute("INSERT INTO alien_state (value) VALUES ('keep-me')")
                connection.commit()
            before = _schema_snapshot(database_path)

            with self.assertRaisesRegex(
                migration.DatabaseMigrationError,
                "Unversioned database is not the frozen legacy schema",
            ):
                migration.ensure_database(database_path)

            self.assertEqual(_schema_snapshot(database_path), before)
            with sqlite3.connect(database_path) as connection:
                self.assertEqual(
                    connection.execute("SELECT value FROM alien_state").fetchone()[0],
                    "keep-me",
                )
            self.assertFalse((Path(directory) / "backups").exists())

    def test_unknown_revision_fails_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "unknown-revision.sqlite3"
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO alembic_version (version_num) VALUES ('future_revision')"
                )
                connection.commit()
            before = _schema_snapshot(database_path)

            with self.assertRaisesRegex(
                migration.DatabaseMigrationError,
                "unknown or is not an ancestor",
            ):
                migration.ensure_database(database_path)

            self.assertEqual(_schema_snapshot(database_path), before)
            self.assertEqual(_revision(database_path), "future_revision")
            self.assertFalse((Path(directory) / "backups").exists())

    def test_incompatible_versioned_baseline_fails_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "incompatible.sqlite3"
            config = migration._alembic_config(database_path)
            command.upgrade(config, migration.BASELINE_REVISION)
            with sqlite3.connect(database_path) as connection:
                connection.execute("CREATE TABLE unexpected_extension (id INTEGER PRIMARY KEY)")
                connection.commit()
            before = _schema_snapshot(database_path)

            with self.assertRaisesRegex(
                migration.DatabaseMigrationError,
                "Versioned baseline database does not match revision",
            ):
                migration.ensure_database(database_path)

            self.assertEqual(_schema_snapshot(database_path), before)
            self.assertEqual(_revision(database_path), migration.BASELINE_REVISION)
            self.assertFalse((Path(directory) / "backups").exists())


if __name__ == "__main__":
    unittest.main()
