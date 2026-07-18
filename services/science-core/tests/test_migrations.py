from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from open_science_core import migration
from open_science_core.db import Base
from open_science_core.models import (
    ApprovalRecord,
    EventRecord,
    JobRecord,
    PlanRecord,
    ProjectRecord,
    ReviewRecord,
    SourcePageRecord,
    SourceRecord,
    TaskRecord,
    WorkflowRecord,
)
from open_science_core.workflow.schemas import WorkflowCreateIn
from open_science_core.workflow.service import (
    approve_plan,
    content_sha256,
    job_input_hash_for_handler_version,
    plan_approval_hash,
    start_workflow,
    workflow_snapshot,
)
from open_science_core.workflow.worker import WorkflowWorker

LEGACY_TABLES = tuple(sorted(migration.LEGACY_COLUMNS))
CONTROL_PLANE_TABLES = {
    "analysis_specs",
    "intent_decisions",
    "interaction_requests",
    "model_invocations",
    "structured_analysis_results",
    "user_responses",
    "workflow_jobs",
    "workflow_plans",
    "workflow_reviews",
    "workflows",
}


def _legacy_workflow_create_hash(
    goal: str,
    workflow_type: str = "literature-synthesis",
) -> str:
    canonical = json.dumps(
        {"goal": goal, "workflowType": workflow_type},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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


def _data_snapshot(database_path: Path) -> dict[str, list[tuple[object, ...]]]:
    with sqlite3.connect(database_path) as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: [
                tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
            ]
            for table in tables
        }


def _create_unversioned_legacy_database(database_path: Path) -> None:
    config = migration.alembic_config(database_path)
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


def _insert_analysis_fixture(
    connection: sqlite3.Connection,
    fixture_id: str,
    *,
    run_count: int = 0,
) -> None:
    created_at = "2026-07-15 00:00:00"
    project_id = f"project-{fixture_id}"
    source_id = f"source-{fixture_id}"
    task_id = f"task-{fixture_id}"
    intent_id = f"intent-{fixture_id}"
    connection.execute(
        """
        INSERT INTO projects (
            id, title, description, project_path, research_domain,
            execution_mode, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            f"Project {fixture_id}",
            "",
            f"/tmp/{project_id}",
            None,
            "safe",
            created_at,
            created_at,
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
            source_id,
            project_id,
            f"Dataset {fixture_id}",
            "dataset",
            "[]",
            None,
            None,
            f"/tmp/{fixture_id}.csv",
            None,
            "ready",
            "a" * 64,
            None,
            created_at,
        ),
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
            task_id,
            project_id,
            "Analyze the fixture dataset",
            "python-data-analysis",
            "{}",
            "[]",
            "[]",
            "[]",
            "completed",
            0,
            120,
            created_at,
            created_at,
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
            intent_id,
            task_id,
            project_id,
            source_id,
            "Analyze the fixture dataset",
            "print('fixture')",
            "b" * 64,
            "completed",
            "approved",
            created_at,
            created_at,
        ),
    )
    for run_index in range(run_count):
        connection.execute(
            """
            INSERT INTO runs (
                id, task_id, model, prompt_version, environment_hash,
                input_artifacts, output_artifacts, logs_path, token_usage,
                cost, status, created_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"run-{fixture_id}-{run_index}",
                task_id,
                None,
                None,
                None,
                "[]",
                "[]",
                None,
                "{}",
                0.0,
                "completed",
                created_at,
                created_at,
            ),
        )
    connection.commit()


def _insert_analysis_approval(
    connection: sqlite3.Connection,
    fixture_id: str,
    payload_schema_version: str,
) -> None:
    created_at = "2026-07-15 00:00:00"
    connection.execute(
        """
        INSERT INTO approvals (
            id, task_id, subject_type, subject_id, payload_schema_version,
            intent_hash, requested_action, risk_level, reason,
            affected_resources, user_decision, created_at, decided_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"approval-{fixture_id}",
            f"task-{fixture_id}",
            "analysis-intent",
            f"intent-{fixture_id}",
            payload_schema_version,
            "c" * 64,
            "execute-analysis",
            "high",
            "Approve the immutable analysis payload",
            "[]",
            "approved",
            created_at,
            created_at,
        ),
    )
    connection.commit()


def _insert_goal_aware_analysis_fixture(
    connection: sqlite3.Connection,
    fixture_id: str,
    *,
    include_result: bool = True,
) -> None:
    _insert_analysis_fixture(connection, fixture_id)
    created_at = "2026-07-16 00:00:00"
    workflow_id = f"workflow-{fixture_id}"
    source_id = f"source-{fixture_id}"
    intent_id = f"intent-{fixture_id}"
    run_id = f"run-{fixture_id}-goal-aware"
    spec_id = f"spec-{fixture_id}"
    connection.execute(
        """
        INSERT INTO workflows (
            id, project_id, create_idempotency_key, create_payload_sha256,
            creation_mode, selected_source_ids, workflow_type,
            dataset_source_id, dataset_content_hash, goal, generation_mode,
            status, row_version, event_sequence, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workflow_id,
            f"project-{fixture_id}",
            f"goal-aware-{fixture_id}",
            "c" * 64,
            "fixed-workflow",
            "[]",
            "dataset-analysis",
            source_id,
            "a" * 64,
            "Analyze the fixture dataset",
            "local-deterministic",
            "planning",
            1,
            0,
            created_at,
            created_at,
        ),
    )
    connection.execute(
        """
        INSERT INTO analysis_specs (
                id, workflow_id, revision, previous_spec_id, schema_version,
                selector_kind, selector_reason, prompt_version, model_invocation_id,
                dataset_source_id, dataset_content_hash, dataset_profile_sha256,
                spec_json, spec_sha256, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            spec_id,
            workflow_id,
            1,
            None,
            "1",
                "local-deterministic",
                "Deterministic fixture method-selection reason.",
                None,
            None,
            source_id,
            "a" * 64,
            "d" * 64,
            '{"schemaVersion":"1"}',
            "e" * 64,
            "approved",
            created_at,
        ),
    )
    connection.execute(
        """
        UPDATE analysis_intents SET
            analysis_spec_id = ?, spec_sha256 = ?, dataset_profile_sha256 = ?,
            compiler_version = ?, code_sha256 = ?, runtime_policy_id = ?
        WHERE id = ?
        """,
        (
            spec_id,
            "e" * 64,
            "d" * 64,
            "goal-aware-compiler-v1",
            "f" * 64,
            "science-runtime-safe-v1",
            intent_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO runs (
            id, task_id, analysis_intent_id, input_artifacts,
            output_artifacts, token_usage, cost, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            f"task-{fixture_id}",
            intent_id,
            "[]",
            "[]",
            "{}",
            0.0,
            "completed",
            created_at,
        ),
    )
    if include_result:
        connection.execute(
            """
            INSERT INTO structured_analysis_results (
                id, analysis_spec_id, analysis_intent_id, run_id,
                schema_version, result_json, result_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"result-{fixture_id}",
                spec_id,
                intent_id,
                run_id,
                "1",
                '{"schemaVersion":"1"}',
                "1" * 64,
                created_at,
            ),
        )
    connection.commit()


class DatabaseMigrationTest(unittest.TestCase):
    def test_fresh_database_upgrades_to_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "fresh.sqlite3"

            migration.ensure_database(database_path)

            config = migration.alembic_config(database_path)
            expected_head = migration.single_head(config)
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

    def test_goal_aware_analysis_schema_enforces_provenance_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "goal-aware-schema.sqlite3"
            migration.ensure_database(database_path)

            with sqlite3.connect(database_path) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                intent_columns = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(analysis_intents)")
                }
                self.assertTrue(
                    {
                        "analysis_spec_id",
                        "spec_sha256",
                        "dataset_profile_sha256",
                        "compiler_version",
                        "code_sha256",
                        "runtime_policy_id",
                    }
                    <= intent_columns
                )
                spec_indexes = {
                    str(row[1]): (int(row[2]), int(row[4]))
                    for row in connection.execute("PRAGMA index_list(analysis_specs)")
                }
                self.assertEqual(spec_indexes["uq_analysis_spec_model_invocation"], (1, 1))
                self.assertIn("ix_analysis_specs_workflow_status", spec_indexes)
                model_invocation_indexes = {
                    str(row[1]): int(row[2])
                    for row in connection.execute("PRAGMA index_list(model_invocations)")
                }
                self.assertEqual(
                    model_invocation_indexes[
                        "uq_model_invocations_workflow_id_id_compat"
                    ],
                    1,
                )
                result_unique_columns = {
                    tuple(
                        str(column[2])
                        for column in connection.execute(f'PRAGMA index_info("{row[1]}")')
                    )
                    for row in connection.execute(
                        "PRAGMA index_list(structured_analysis_results)"
                    )
                    if int(row[2]) == 1
                }
                self.assertTrue(
                    {
                        ("analysis_spec_id",),
                        ("analysis_intent_id",),
                        ("run_id",),
                    }
                    <= result_unique_columns
                )

                _insert_goal_aware_analysis_fixture(connection, "schema")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE analysis_intents SET code_sha256 = NULL "
                        "WHERE id = 'intent-schema'"
                    )
                connection.rollback()

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE analysis_specs SET spec_json = '[]' "
                        "WHERE id = 'spec-schema'"
                    )
                connection.rollback()

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE structured_analysis_results SET result_sha256 = ? "
                        "WHERE id = 'result-schema'",
                        ("not-a-sha256",),
                    )
                connection.rollback()

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO structured_analysis_results (
                            id, analysis_spec_id, analysis_intent_id, run_id,
                            schema_version, result_json, result_sha256, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "duplicate-result-schema",
                            "spec-schema",
                            "intent-schema",
                            "run-schema-goal-aware",
                            "1",
                            "{}",
                            "2" * 64,
                            "2026-07-16 00:01:00",
                        ),
                    )
                connection.rollback()

    def test_goal_aware_analysis_migration_round_trip_preserves_legacy_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "goal-aware-round-trip.sqlite3"
            config = migration.alembic_config(database_path)
            prior_revision = "0006_autonomous_agent_intake"
            command.upgrade(config, prior_revision)
            with sqlite3.connect(database_path) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                _insert_analysis_fixture(connection, "goal-aware-legacy")
            before_data = _data_snapshot(database_path)

            command.upgrade(config, "head")
            with sqlite3.connect(database_path) as connection:
                provenance = connection.execute(
                    "SELECT analysis_spec_id, spec_sha256, dataset_profile_sha256, "
                    "compiler_version, code_sha256, runtime_policy_id "
                    "FROM analysis_intents WHERE id = 'intent-goal-aware-legacy'"
                ).fetchone()
                self.assertEqual(provenance, (None, None, None, None, None, None))
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM analysis_specs").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM structured_analysis_results"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

            command.downgrade(config, prior_revision)

            self.assertEqual(_revision(database_path), prior_revision)
            self.assertEqual(_data_snapshot(database_path), before_data)
            with sqlite3.connect(database_path) as connection:
                intent_columns = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(analysis_intents)")
                }
                self.assertNotIn("analysis_spec_id", intent_columns)
                self.assertNotIn("spec_sha256", intent_columns)
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_goal_aware_analysis_downgrade_refuses_new_provenance_without_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "goal-aware-downgrade.sqlite3"
            migration.ensure_database(database_path)
            config = migration.alembic_config(database_path)
            head = migration.single_head(config)
            with sqlite3.connect(database_path) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                _insert_goal_aware_analysis_fixture(connection, "downgrade")
            before_schema = _schema_snapshot(database_path)
            before_data = _data_snapshot(database_path)

            with self.assertRaisesRegex(
                RuntimeError,
                "goal-aware analysis spec provenance exists",
            ):
                command.downgrade(config, "0006_autonomous_agent_intake")

            self.assertEqual(_revision(database_path), head)
            self.assertEqual(_schema_snapshot(database_path), before_schema)
            self.assertEqual(_data_snapshot(database_path), before_data)

    def test_goal_aware_analysis_downgrade_refuses_new_events_without_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "goal-aware-event-downgrade.sqlite3"
            migration.ensure_database(database_path)
            config = migration.alembic_config(database_path)
            head = migration.single_head(config)
            with sqlite3.connect(database_path) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                _insert_analysis_fixture(connection, "event-downgrade")
                connection.execute(
                    """
                    INSERT INTO events (
                        id, project_id, workflow_id, task_id, job_id, sequence,
                        event_type, payload, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "goal-aware-event-downgrade",
                        "project-event-downgrade",
                        None,
                        None,
                        None,
                        None,
                        "analysis.unsupported",
                        "{}",
                        "2026-07-16 00:00:00",
                    ),
                )
                connection.commit()
            before_schema = _schema_snapshot(database_path)
            before_data = _data_snapshot(database_path)

            with self.assertRaisesRegex(
                RuntimeError,
                "goal-aware analysis workflow events exist",
            ):
                command.downgrade(config, "0006_autonomous_agent_intake")

            self.assertEqual(_revision(database_path), head)
            self.assertEqual(_schema_snapshot(database_path), before_schema)
            self.assertEqual(_data_snapshot(database_path), before_data)

    def test_autonomous_intake_schema_enforces_versions_hashes_and_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "autonomous-intake.sqlite3"
            migration.ensure_database(database_path)

            with sqlite3.connect(database_path) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                workflow_columns = {
                    str(row[1]): (str(row[2]), int(row[3]), row[4])
                    for row in connection.execute("PRAGMA table_info(workflows)")
                }
                self.assertEqual(workflow_columns["workflow_type"][1], 0)
                self.assertEqual(
                    workflow_columns["creation_mode"],
                    ("VARCHAR(32)", 1, "'fixed-workflow'"),
                )
                self.assertEqual(workflow_columns["selected_source_ids"][1], 1)
                self.assertIn("current_intent_decision_id", workflow_columns)

                workflow_sql = str(
                    connection.execute(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'workflows'"
                    ).fetchone()[0]
                )
                job_sql = str(
                    connection.execute(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'workflow_jobs'"
                    ).fetchone()[0]
                )
                self.assertIn("ck_workflow_creation_mode", workflow_sql)
                self.assertIn("ck_workflow_intake_state", workflow_sql)
                self.assertIn("ck_workflow_selected_source_ids", workflow_sql)
                self.assertIn("route-intent", job_sql)

                connection.execute(
                    """
                    INSERT INTO projects (
                        id, title, description, project_path, research_domain,
                        execution_mode, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "agent-project",
                        "Autonomous intake",
                        "Persistence fixture",
                        "/tmp/agent-project",
                        "quality",
                        "safe",
                        "2026-07-16 00:00:00",
                        "2026-07-16 00:00:00",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO sources (
                        id, project_id, title, source_kind, authors, local_path,
                        ingestion_status, content_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "agent-source",
                        "agent-project",
                        "Agent dataset",
                        "csv",
                        "[]",
                        "/tmp/agent-project/dataset.csv",
                        "ready",
                        "a" * 64,
                        "2026-07-16 00:00:00",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO workflows (
                        id, project_id, create_idempotency_key,
                        create_payload_sha256, creation_mode, selected_source_ids,
                        workflow_type, goal, generation_mode, status, row_version,
                        event_sequence, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "agent-workflow",
                        "agent-project",
                        "agent-create-key",
                        "b" * 64,
                        "autonomous",
                        '["agent-source"]',
                        None,
                        "Determine the appropriate research workflow",
                        "remote-model-assisted",
                        "routing",
                        1,
                        0,
                        "2026-07-16 00:00:00",
                        "2026-07-16 00:00:00",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO workflow_jobs (
                        id, workflow_id, kind, operation_key, attempt,
                        input_sha256, handler_version, status, available_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "route-job",
                        "agent-workflow",
                        "route-intent",
                        "workflow:agent-workflow:intent:1",
                        1,
                        "c" * 64,
                        "intent-router-v1",
                        "succeeded",
                        "2026-07-16 00:00:00",
                        "2026-07-16 00:00:00",
                        "2026-07-16 00:00:01",
                    ),
                )
                connection.commit()

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO workflows (
                            id, project_id, create_idempotency_key,
                            create_payload_sha256, creation_mode,
                            selected_source_ids, workflow_type, goal,
                            generation_mode, status, row_version,
                            event_sequence, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "invalid-routing-workflow",
                            "agent-project",
                            "invalid-routing-key",
                            "d" * 64,
                            "fixed-workflow",
                            "[]",
                            "literature-synthesis",
                            "A fixed workflow cannot remain in routing",
                            "local-deterministic",
                            "routing",
                            1,
                            0,
                            "2026-07-16 00:00:00",
                            "2026-07-16 00:00:00",
                        ),
                    )
                connection.rollback()

                connection.execute(
                    """
                    INSERT INTO model_invocations (
                        id, workflow_id, schema_version, operation_type,
                        operation_key, attempt, generator, model,
                        endpoint_identity, prompt_version, input_sha256,
                        output_sha256, token_usage, validation_errors,
                        request_idempotency_key, request_payload_sha256,
                        status, created_at, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "router-invocation",
                        "agent-workflow",
                        "1",
                        "intent-routing",
                        "workflow:agent-workflow:intent:1",
                        1,
                        "remote-model-assisted-v1",
                        "test-model",
                        "https://model.invalid/v1",
                        "intent-router-v1",
                        "e" * 64,
                        "f" * 64,
                        '{"inputTokens":10,"outputTokens":5}',
                        "[]",
                        "router-request-key",
                        "1" * 64,
                        "succeeded",
                        "2026-07-16 00:00:00",
                        "2026-07-16 00:00:01",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO intent_decisions (
                        id, workflow_id, revision, intent, confidence,
                        reasoning_summary, selected_source_ids, missing_inputs,
                        proposed_workflow_type, generator, used_model, model,
                        prompt_version, parse_result, model_invocation_id,
                        input_sha256, output_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "intent-decision-1",
                        "agent-workflow",
                        1,
                        "clarification-required",
                        0.61,
                        "The selected CSV could support more than one requested method.",
                        '["agent-source"]',
                        '["analysis-method"]',
                        None,
                        "remote-model-assisted-v1",
                        1,
                        "test-model",
                        "intent-router-v1",
                        "valid",
                        "router-invocation",
                        "e" * 64,
                        "2" * 64,
                        "2026-07-16 00:00:01",
                    ),
                )
                connection.execute(
                    "UPDATE workflows SET status = 'waiting-clarification', "
                    "current_intent_decision_id = 'intent-decision-1', row_version = 2 "
                    "WHERE id = 'agent-workflow'"
                )
                connection.execute(
                    """
                    INSERT INTO interaction_requests (
                        id, workflow_id, step_id, request_key, revision,
                        workflow_revision, request_type, question, options,
                        required, status, response_schema, request_sha256,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "interaction-1",
                        "agent-workflow",
                        None,
                        "intent.analysis-method",
                        1,
                        2,
                        "single-choice",
                        "Which analysis should be performed?",
                        '[{"value":"descriptive"},{"value":"two-group"}]',
                        1,
                        "pending",
                        '{"type":"string"}',
                        "3" * 64,
                        "2026-07-16 00:00:02",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO user_responses (
                        id, interaction_id, revision,
                        expected_workflow_revision, response_json,
                        response_sha256, idempotency_key,
                        request_payload_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "response-1",
                        "interaction-1",
                        1,
                        2,
                        '"two-group"',
                        "4" * 64,
                        "response-idempotency-1",
                        "5" * 64,
                        "2026-07-16 00:00:03",
                    ),
                )
                connection.execute(
                    "UPDATE interaction_requests SET status = 'answered', "
                    "answered_at = '2026-07-16 00:00:03' WHERE id = 'interaction-1'"
                )
                connection.commit()

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO user_responses (
                            id, interaction_id, revision,
                            expected_workflow_revision, response_json,
                            response_sha256, idempotency_key,
                            request_payload_sha256, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "response-idempotency-conflict",
                            "interaction-1",
                            2,
                            3,
                            '"descriptive"',
                            "6" * 64,
                            "response-idempotency-1",
                            "7" * 64,
                            "2026-07-16 00:00:04",
                        ),
                    )
                connection.rollback()

                connection.execute(
                    """
                    INSERT INTO user_responses (
                        id, interaction_id, revision,
                        expected_workflow_revision, response_json,
                        response_sha256, idempotency_key,
                        request_payload_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "response-2",
                        "interaction-1",
                        2,
                        3,
                        '"descriptive"',
                        "6" * 64,
                        "response-idempotency-2",
                        "7" * 64,
                        "2026-07-16 00:00:04",
                    ),
                )
                connection.commit()
                self.assertEqual(
                    connection.execute(
                        "SELECT revision, response_json FROM user_responses "
                        "WHERE interaction_id = 'interaction-1' ORDER BY revision"
                    ).fetchall(),
                    [(1, '"two-group"'), (2, '"descriptive"')],
                )

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO intent_decisions (
                            id, workflow_id, revision, intent, confidence,
                            reasoning_summary, selected_source_ids, missing_inputs,
                            proposed_workflow_type, generator, used_model,
                            prompt_version, parse_result, input_sha256,
                            output_sha256, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "invalid-intent-decision",
                            "agent-workflow",
                            2,
                            "dataset-analysis",
                            1.1,
                            "Invalid confidence and resolution",
                            '["agent-source"]',
                            "[]",
                            "literature-synthesis",
                            "deterministic-intent-router-v1",
                            0,
                            "intent-router-v1",
                            "deterministic-capability-guard",
                            "8" * 64,
                            "9" * 64,
                            "2026-07-16 00:00:05",
                        ),
                    )
                connection.rollback()

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE model_invocations SET status = 'pending' "
                        "WHERE id = 'router-invocation'"
                    )
                connection.rollback()

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE model_invocations SET output_sha256 = NULL "
                        "WHERE id = 'router-invocation'"
                    )
                connection.rollback()

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO intent_decisions (
                            id, workflow_id, revision, intent, confidence,
                            reasoning_summary, selected_source_ids, missing_inputs,
                            proposed_workflow_type, generator, used_model, model,
                            prompt_version, parse_result, model_invocation_id,
                            input_sha256, output_sha256, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "duplicate-invocation-decision",
                            "agent-workflow",
                            2,
                            "clarification-required",
                            0.75,
                            "A second decision cannot bind to the same invocation.",
                            '["agent-source"]',
                            '["analysis-method"]',
                            None,
                            "remote-model-assisted-v1",
                            1,
                            "test-model",
                            "intent-router-v1",
                            "valid",
                            "router-invocation",
                            "e" * 64,
                            "3" * 64,
                            "2026-07-16 00:00:06",
                        ),
                    )
                connection.rollback()

                connection.execute(
                    """
                    INSERT INTO workflows (
                        id, project_id, create_idempotency_key,
                        create_payload_sha256, creation_mode, selected_source_ids,
                        workflow_type, goal, generation_mode, status, row_version,
                        event_sequence, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "agent-workflow-2",
                        "agent-project",
                        "agent-create-key-2",
                        "a" * 64,
                        "autonomous",
                        '["agent-source"]',
                        None,
                        "Route a second autonomous workflow",
                        "remote-model-assisted",
                        "routing",
                        1,
                        0,
                        "2026-07-16 00:00:07",
                        "2026-07-16 00:00:07",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO model_invocations (
                        id, workflow_id, schema_version, operation_type,
                        operation_key, attempt, generator, model,
                        endpoint_identity, prompt_version, input_sha256,
                        output_sha256, token_usage, validation_errors,
                        request_idempotency_key, request_payload_sha256,
                        status, created_at, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "cross-workflow-invocation",
                        "agent-workflow",
                        "1",
                        "intent-routing",
                        "workflow:agent-workflow:intent:2",
                        1,
                        "remote-model-assisted-v1",
                        "test-model",
                        "https://model.invalid/v1",
                        "intent-router-v1",
                        "4" * 64,
                        "5" * 64,
                        "{}",
                        "[]",
                        "cross-workflow-request-key",
                        "6" * 64,
                        "succeeded",
                        "2026-07-16 00:00:07",
                        "2026-07-16 00:00:08",
                    ),
                )
                connection.commit()

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO model_invocations (
                            id, workflow_id, schema_version, operation_type,
                            operation_key, attempt, generator, model,
                            endpoint_identity, prompt_version, input_sha256,
                            output_sha256, token_usage, validation_errors,
                            request_idempotency_key, request_payload_sha256,
                            status, error_message, created_at, finished_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "invalid-succeeded-invocation",
                            "agent-workflow-2",
                            "1",
                            "intent-routing",
                            "workflow:agent-workflow-2:intent:1",
                            1,
                            "remote-model-assisted-v1",
                            "test-model",
                            "https://model.invalid/v1",
                            "intent-router-v1",
                            "7" * 64,
                            "8" * 64,
                            "{}",
                            "[]",
                            "invalid-succeeded-request-key",
                            "9" * 64,
                            "succeeded",
                            "A succeeded invocation cannot retain an error.",
                            "2026-07-16 00:00:08",
                            "2026-07-16 00:00:09",
                        ),
                    )
                connection.rollback()

                connection.execute(
                    """
                    INSERT INTO intent_decisions (
                        id, workflow_id, revision, intent, confidence,
                        reasoning_summary, selected_source_ids, missing_inputs,
                        proposed_workflow_type, generator, used_model, model,
                        prompt_version, parse_result, model_invocation_id,
                        input_sha256, output_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "cross-workflow-decision",
                        "agent-workflow-2",
                        1,
                        "clarification-required",
                        0.7,
                        "A decision cannot bind an invocation from another workflow.",
                        '["agent-source"]',
                        '["analysis-method"]',
                        None,
                        "remote-model-assisted-v1",
                        1,
                        "test-model",
                        "intent-router-v1",
                        "valid",
                        "cross-workflow-invocation",
                        "4" * 64,
                        "a" * 64,
                        "2026-07-16 00:00:09",
                    ),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.commit()
                connection.rollback()
                self.assertIsNone(
                    connection.execute(
                        "SELECT id FROM intent_decisions "
                        "WHERE id = 'cross-workflow-decision'"
                    ).fetchone()
                )

                connection.execute(
                    "UPDATE workflows SET current_intent_decision_id = ? WHERE id = ?",
                    ("intent-decision-1", "agent-workflow-2"),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.commit()
                connection.rollback()
                self.assertIsNone(
                    connection.execute(
                        "SELECT current_intent_decision_id FROM workflows "
                        "WHERE id = 'agent-workflow-2'"
                    ).fetchone()[0]
                )

                connection.execute(
                    "DELETE FROM model_invocations WHERE id = 'router-invocation'"
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.commit()
                connection.rollback()
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM model_invocations "
                        "WHERE id = 'router-invocation'"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM intent_decisions "
                        "WHERE id = 'intent-decision-1'"
                    ).fetchone()[0],
                    1,
                )

                connection.execute(
                    "DELETE FROM intent_decisions WHERE id = 'intent-decision-1'"
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.commit()
                connection.rollback()
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM intent_decisions "
                        "WHERE id = 'intent-decision-1'"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT current_intent_decision_id FROM workflows "
                        "WHERE id = 'agent-workflow'"
                    ).fetchone()[0],
                    "intent-decision-1",
                )

                connection.execute("DELETE FROM workflows WHERE id = 'agent-workflow'")
                connection.commit()
                for table, record_id in (
                    ("workflows", "agent-workflow"),
                    ("workflow_jobs", "route-job"),
                    ("model_invocations", "router-invocation"),
                    ("model_invocations", "cross-workflow-invocation"),
                    ("intent_decisions", "intent-decision-1"),
                    ("interaction_requests", "interaction-1"),
                    ("user_responses", "response-1"),
                    ("user_responses", "response-2"),
                ):
                    self.assertEqual(
                        connection.execute(
                            f'SELECT COUNT(*) FROM "{table}" WHERE id = ?',
                            (record_id,),
                        ).fetchone()[0],
                        0,
                    )
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_orm_metadata_declares_autonomous_composite_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "autonomous-metadata.sqlite3"
            engine = create_engine(f"sqlite:///{database_path}")
            Base.metadata.create_all(engine)
            engine.dispose()

            with sqlite3.connect(database_path) as connection:
                def foreign_keys(
                    table: str,
                ) -> set[tuple[str, tuple[tuple[str, str], ...]]]:
                    grouped: dict[int, tuple[str, list[tuple[int, str, str]]]] = {}
                    for row in connection.execute(f'PRAGMA foreign_key_list("{table}")'):
                        constraint_id = int(row[0])
                        target_table = str(row[2])
                        grouped.setdefault(constraint_id, (target_table, []))[1].append(
                            (int(row[1]), str(row[3]), str(row[4]))
                        )
                    return {
                        (
                            target_table,
                            tuple(
                                (source_column, target_column)
                                for _, source_column, target_column in sorted(columns)
                            ),
                        )
                        for target_table, columns in grouped.values()
                    }

                self.assertIn(
                    (
                        "model_invocations",
                        (
                            ("workflow_id", "workflow_id"),
                            ("model_invocation_id", "id"),
                        ),
                    ),
                    foreign_keys("intent_decisions"),
                )
                self.assertIn(
                    (
                        "intent_decisions",
                        (
                            ("id", "workflow_id"),
                            ("current_intent_decision_id", "id"),
                        ),
                    ),
                    foreign_keys("workflows"),
                )

    def test_autonomous_workflow_orm_keeps_unresolved_workflow_type_null(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "autonomous-orm.sqlite3"
            migration.ensure_database(database_path)
            engine = create_engine(f"sqlite:///{database_path}")
            with Session(engine) as session:
                project = ProjectRecord(
                    id="autonomous-orm-project",
                    title="Autonomous ORM",
                    description="",
                    project_path="/tmp/autonomous-orm-project",
                    execution_mode="safe",
                )
                session.add(project)
                session.flush()
                workflow = WorkflowRecord(
                    id="autonomous-orm-workflow",
                    project_id=project.id,
                    create_idempotency_key="autonomous-orm-key",
                    create_payload_sha256="a" * 64,
                    creation_mode="autonomous",
                    selected_source_ids=[],
                    workflow_type=None,
                    goal="Route this research goal",
                    generation_mode="local-deterministic",
                    status="routing",
                    row_version=1,
                    event_sequence=0,
                )
                session.add(workflow)
                session.commit()
                self.assertIsNone(workflow.workflow_type)
            engine.dispose()

    def test_dataset_analysis_schema_enforces_lineage_and_active_uniqueness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "dataset-schema.sqlite3"
            migration.ensure_database(database_path)

            with sqlite3.connect(database_path) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                workflow_columns = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(workflows)")
                }
                self.assertTrue({"dataset_source_id", "dataset_content_hash"} <= workflow_columns)
                intent_columns = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(analysis_intents)")
                }
                self.assertTrue(
                    {
                        "workflow_id",
                        "plan_step_id",
                        "previous_intent_id",
                        "dataset_content_hash",
                        "expected_outputs",
                        "timeout_seconds",
                        "risk_level",
                        "repair_attempt",
                        "error_summary",
                        "code_diff",
                    }
                    <= intent_columns
                )
                run_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(runs)")}
                self.assertIn("analysis_intent_id", run_columns)
                job_columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(workflow_jobs)")
                }
                self.assertIn("request_payload_sha256", job_columns)

                intent_indexes = {
                    str(row[1]): (int(row[2]), int(row[4]))
                    for row in connection.execute("PRAGMA index_list(analysis_intents)")
                }
                self.assertEqual(intent_indexes["ix_analysis_intents_task_id"], (0, 0))
                self.assertEqual(intent_indexes["uq_analysis_intent_active_task"], (1, 1))
                self.assertIn("ix_analysis_intents_workflow_step", intent_indexes)
                run_indexes = {
                    str(row[1]): (int(row[2]), int(row[4]))
                    for row in connection.execute("PRAGMA index_list(runs)")
                }
                self.assertEqual(run_indexes["uq_run_analysis_intent"], (1, 1))

                workflow_sql = str(
                    connection.execute(
                        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'workflows'"
                    ).fetchone()[0]
                )
                intent_sql = str(
                    connection.execute(
                        "SELECT sql FROM sqlite_master WHERE type = 'table' "
                        "AND name = 'analysis_intents'"
                    ).fetchone()[0]
                )
                review_sql = str(
                    connection.execute(
                        "SELECT sql FROM sqlite_master WHERE type = 'table' "
                        "AND name = 'workflow_reviews'"
                    ).fetchone()[0]
                )
                self.assertIn("ck_workflow_dataset_identity", workflow_sql)
                self.assertIn("ck_workflow_type", workflow_sql)
                self.assertIn("ck_workflow_dataset_content_hash", workflow_sql)
                self.assertIn("ck_analysis_intent_repair_attempt", intent_sql)
                self.assertIn("ck_analysis_intent_risk_level", intent_sql)
                self.assertIn("ck_analysis_intent_dataset_content_hash", intent_sql)
                self.assertIn("ck_analysis_intent_timeout_seconds", intent_sql)
                self.assertIn("ck_analysis_intent_workflow_binding", intent_sql)
                self.assertIn("passed-with-warnings", review_sql)

                _insert_analysis_fixture(connection, "partial")
                connection.execute(
                    """
                    INSERT INTO workflows (
                        id, project_id, create_idempotency_key,
                        create_payload_sha256, workflow_type,
                        dataset_source_id, dataset_content_hash, goal,
                        generation_mode, status, row_version, event_sequence,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "workflow-partial",
                        "project-partial",
                        "dataset-partial-key",
                        "f" * 64,
                        "dataset-analysis",
                        "source-partial",
                        "a" * 64,
                        "Analyze the immutable fixture dataset",
                        "local-deterministic",
                        "running",
                        2,
                        0,
                        "2026-07-15 00:00:00",
                        "2026-07-15 00:00:00",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO tasks (
                        id, project_id, workflow_id, step_key, order_index,
                        objective, task_type, inputs, expected_outputs,
                        acceptance_criteria, permissions, status, retries,
                        timeout_seconds, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "task-workflow-partial",
                        "project-partial",
                        "workflow-partial",
                        "execute-analysis",
                        2,
                        "Execute the approved analysis",
                        "python-data-analysis",
                        "{}",
                        "[]",
                        "[]",
                        "[]",
                        "waiting-approval",
                        0,
                        600,
                        "2026-07-15 00:00:00",
                        "2026-07-15 00:00:00",
                    ),
                )
                connection.commit()

                workflow_intent_columns = (
                    "id",
                    "task_id",
                    "project_id",
                    "workflow_id",
                    "plan_step_id",
                    "dataset_source_id",
                    "dataset_content_hash",
                    "objective",
                    "code",
                    "expected_outputs",
                    "timeout_seconds",
                    "risk_level",
                    "repair_attempt",
                    "payload_sha256",
                    "status",
                    "decision",
                    "created_at",
                    "updated_at",
                )
                valid_workflow_intent: dict[str, object] = {
                    "id": "intent-workflow-partial",
                    "task_id": "task-workflow-partial",
                    "project_id": "project-partial",
                    "workflow_id": "workflow-partial",
                    "plan_step_id": "execute-analysis",
                    "dataset_source_id": "source-partial",
                    "dataset_content_hash": "a" * 64,
                    "objective": "Execute the approved analysis",
                    "code": "print('approved')",
                    "expected_outputs": json.dumps(
                        [
                            "executed-notebook",
                            "analysis-log",
                            "environment-manifest",
                        ]
                    ),
                    "timeout_seconds": 600,
                    "risk_level": "high",
                    "repair_attempt": 0,
                    "payload_sha256": "e" * 64,
                    "status": "waiting-approval",
                    "decision": None,
                    "created_at": "2026-07-15 00:00:00",
                    "updated_at": "2026-07-15 00:00:00",
                }
                insert_workflow_intent = (
                    f"INSERT INTO analysis_intents ({', '.join(workflow_intent_columns)}) "
                    f"VALUES ({', '.join('?' for _ in workflow_intent_columns)})"
                )
                for missing_field in ("plan_step_id", "risk_level"):
                    invalid = {**valid_workflow_intent, missing_field: None}
                    invalid["id"] = f"intent-missing-{missing_field}"
                    with (
                        self.subTest(missing_field=missing_field),
                        self.assertRaises(sqlite3.IntegrityError),
                    ):
                        connection.execute(
                            insert_workflow_intent,
                            tuple(invalid[column] for column in workflow_intent_columns),
                        )
                    connection.rollback()

                connection.execute(
                    insert_workflow_intent,
                    tuple(valid_workflow_intent[column] for column in workflow_intent_columns),
                )
                connection.commit()

                incomplete_repair = {
                    **valid_workflow_intent,
                    "id": "intent-workflow-repair",
                    "previous_intent_id": "intent-workflow-partial",
                    "repair_attempt": 1,
                    "error_summary": "{}",
                    "code_diff": None,
                    "status": "failed",
                    "decision": "approved",
                }
                repair_columns = workflow_intent_columns + (
                    "previous_intent_id",
                    "error_summary",
                    "code_diff",
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        f"INSERT INTO analysis_intents ({', '.join(repair_columns)}) "
                        f"VALUES ({', '.join('?' for _ in repair_columns)})",
                        tuple(incomplete_repair[column] for column in repair_columns),
                    )
                connection.rollback()

                connection.execute(
                    "UPDATE analysis_intents SET status = 'failed', decision = 'approved', "
                    "error_summary = '{}' WHERE id = 'intent-workflow-partial'"
                )
                complete_repair = {
                    **incomplete_repair,
                    "status": "waiting-approval",
                    "decision": None,
                    "code_diff": "-print('approved')\n+print('repaired')",
                    "payload_sha256": "d" * 64,
                }
                connection.execute(
                    f"INSERT INTO analysis_intents ({', '.join(repair_columns)}) "
                    f"VALUES ({', '.join('?' for _ in repair_columns)})",
                    tuple(complete_repair[column] for column in repair_columns),
                )
                connection.commit()

                connection.execute(
                    "DELETE FROM tasks WHERE id = 'task-workflow-partial'"
                )
                connection.commit()
                remaining_workflow_intents = connection.execute(
                    "SELECT id FROM analysis_intents WHERE id IN (?, ?) ORDER BY id",
                    ("intent-workflow-partial", "intent-workflow-repair"),
                ).fetchall()
                self.assertEqual(remaining_workflow_intents, [])
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(), []
                )

                connection.execute(
                    """
                    INSERT INTO analysis_intents (
                        id, task_id, project_id, dataset_source_id, objective, code,
                        payload_sha256, status, decision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "intent-partial-history-2",
                        "task-partial",
                        "project-partial",
                        "source-partial",
                        "Historical repair",
                        "print('history')",
                        "c" * 64,
                        "failed",
                        None,
                        "2026-07-15 00:01:00",
                        "2026-07-15 00:01:00",
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
                        "intent-partial-active",
                        "task-partial",
                        "project-partial",
                        "source-partial",
                        "Active repair",
                        "print('active')",
                        "d" * 64,
                        "waiting-approval",
                        None,
                        "2026-07-15 00:02:00",
                        "2026-07-15 00:02:00",
                    ),
                )
                connection.commit()
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO analysis_intents (
                            id, task_id, project_id, dataset_source_id, objective, code,
                            payload_sha256, status, decision, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "intent-partial-active-2",
                            "task-partial",
                            "project-partial",
                            "source-partial",
                            "Conflicting active repair",
                            "print('conflict')",
                            "e" * 64,
                            "approved",
                            None,
                            "2026-07-15 00:03:00",
                            "2026-07-15 00:03:00",
                        ),
                    )
                connection.rollback()

                connection.execute(
                    """
                    INSERT INTO runs (
                        id, task_id, analysis_intent_id, input_artifacts,
                        output_artifacts, token_usage, cost, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "run-partial-1",
                        "task-partial",
                        "intent-partial-active",
                        "[]",
                        "[]",
                        "{}",
                        0.0,
                        "completed",
                        "2026-07-15 00:04:00",
                    ),
                )
                connection.commit()
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO runs (
                            id, task_id, analysis_intent_id, input_artifacts,
                            output_artifacts, token_usage, cost, status, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "run-partial-2",
                            "task-partial",
                            "intent-partial-active",
                            "[]",
                            "[]",
                            "{}",
                            0.0,
                            "completed",
                            "2026-07-15 00:05:00",
                        ),
                    )
                connection.rollback()
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_repeated_head_upgrades_preserve_schema_version_schema_and_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "idempotent.sqlite3"
            migration.ensure_database(database_path)
            config = migration.alembic_config(database_path)
            expected_head = migration.single_head(config)
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    """
                    INSERT INTO projects (
                        id, title, description, project_path, research_domain,
                        execution_mode, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "idempotency-project",
                        "Preserve across repeated upgrades",
                        "Migration idempotency fixture",
                        "/tmp/idempotency-project",
                        "quality",
                        "safe",
                        "2026-07-14 00:00:00",
                        "2026-07-14 00:00:00",
                    ),
                )
                connection.commit()
            before_schema = _schema_snapshot(database_path)
            before_data = _data_snapshot(database_path)

            command.upgrade(config, "head")

            self.assertEqual(_revision(database_path), expected_head)
            self.assertEqual(_schema_snapshot(database_path), before_schema)
            self.assertEqual(_data_snapshot(database_path), before_data)

            migration.ensure_database(database_path)

            self.assertEqual(_revision(database_path), expected_head)
            self.assertEqual(_schema_snapshot(database_path), before_schema)
            self.assertEqual(_data_snapshot(database_path), before_data)

    def test_autonomous_intake_migration_round_trip_preserves_fixed_workflow_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "autonomous-round-trip.sqlite3"
            config = migration.alembic_config(database_path)
            prior_revision = "0005_workflow_mutation_replay"
            command.upgrade(config, prior_revision)
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
                        "round-trip-agent-project",
                        "Fixed workflow",
                        "Autonomous migration round-trip fixture",
                        "/tmp/round-trip-agent-project",
                        None,
                        "safe",
                        "2026-07-16 00:00:00",
                        "2026-07-16 00:00:00",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO workflows (
                        id, project_id, create_idempotency_key,
                        create_payload_sha256, workflow_type, goal,
                        generation_mode, status, row_version, event_sequence,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "round-trip-fixed-workflow",
                        "round-trip-agent-project",
                        "round-trip-fixed-key",
                        "a" * 64,
                        "literature-synthesis",
                        "Preserve the fixed workflow",
                        "local-deterministic",
                        "planning",
                        1,
                        0,
                        "2026-07-16 00:00:00",
                        "2026-07-16 00:00:00",
                    ),
                )
                connection.commit()
            before_data = _data_snapshot(database_path)

            command.upgrade(config, "head")
            with sqlite3.connect(database_path) as connection:
                workflow = connection.execute(
                    "SELECT creation_mode, selected_source_ids, "
                    "current_intent_decision_id FROM workflows "
                    "WHERE id = 'round-trip-fixed-workflow'"
                ).fetchone()
                self.assertEqual(workflow, ("fixed-workflow", "[]", None))
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

            command.downgrade(config, prior_revision)

            self.assertEqual(_revision(database_path), prior_revision)
            self.assertEqual(_data_snapshot(database_path), before_data)
            with sqlite3.connect(database_path) as connection:
                workflow_columns = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(workflows)")
                }
                self.assertNotIn("creation_mode", workflow_columns)
                self.assertNotIn("selected_source_ids", workflow_columns)
                self.assertNotIn("current_intent_decision_id", workflow_columns)
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_autonomous_intake_downgrade_refuses_new_provenance_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "autonomous-downgrade.sqlite3"
            migration.ensure_database(database_path)
            config = migration.alembic_config(database_path)
            head = migration.single_head(config)
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
                        "autonomous-downgrade-project",
                        "Autonomous workflow",
                        "Downgrade refusal fixture",
                        "/tmp/autonomous-downgrade-project",
                        None,
                        "safe",
                        "2026-07-16 00:00:00",
                        "2026-07-16 00:00:00",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO workflows (
                        id, project_id, create_idempotency_key,
                        create_payload_sha256, creation_mode, selected_source_ids,
                        workflow_type, goal, generation_mode, status, row_version,
                        event_sequence, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "autonomous-downgrade-workflow",
                        "autonomous-downgrade-project",
                        "autonomous-downgrade-key",
                        "b" * 64,
                        "autonomous",
                        "[]",
                        None,
                        "Keep autonomous intake provenance",
                        "local-deterministic",
                        "routing",
                        1,
                        0,
                        "2026-07-16 00:00:00",
                        "2026-07-16 00:00:00",
                    ),
                )
                connection.commit()
            before_schema = _schema_snapshot(database_path)
            before_data = _data_snapshot(database_path)

            with self.assertRaisesRegex(
                RuntimeError,
                "autonomous workflow intake provenance exists",
            ):
                command.downgrade(config, "0005_workflow_mutation_replay")

            self.assertEqual(_revision(database_path), head)
            self.assertEqual(_schema_snapshot(database_path), before_schema)
            self.assertEqual(_data_snapshot(database_path), before_data)

    def test_dataset_upgrade_rejects_ambiguous_legacy_run_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "ambiguous-runs.sqlite3"
            config = migration.alembic_config(database_path)
            prior_revision = "0003_model_assisted_workflows"
            command.upgrade(config, prior_revision)
            with sqlite3.connect(database_path) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                _insert_analysis_fixture(connection, "ambiguous", run_count=2)
            before_schema = _schema_snapshot(database_path)
            before_data = _data_snapshot(database_path)

            with self.assertRaisesRegex(
                RuntimeError,
                "multiple runs share one analysis intent",
            ):
                command.upgrade(config, "head")

            self.assertEqual(_revision(database_path), prior_revision)
            self.assertEqual(_schema_snapshot(database_path), before_schema)
            self.assertEqual(_data_snapshot(database_path), before_data)

    def test_dataset_migration_round_trip_preserves_legacy_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "round-trip.sqlite3"
            config = migration.alembic_config(database_path)
            prior_revision = "0003_model_assisted_workflows"
            command.upgrade(config, prior_revision)
            with sqlite3.connect(database_path) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                _insert_analysis_fixture(connection, "roundtrip", run_count=1)
            before_data = _data_snapshot(database_path)

            command.upgrade(config, "head")
            with sqlite3.connect(database_path) as connection:
                run_intent = connection.execute(
                    "SELECT analysis_intent_id FROM runs WHERE id = 'run-roundtrip-0'"
                ).fetchone()
                self.assertEqual(run_intent, ("intent-roundtrip",))

            command.downgrade(config, prior_revision)

            self.assertEqual(_revision(database_path), prior_revision)
            self.assertEqual(_data_snapshot(database_path), before_data)
            with sqlite3.connect(database_path) as connection:
                run_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(runs)")}
                self.assertNotIn("analysis_intent_id", run_columns)
                intent_indexes = {
                    str(row[1]): int(row[2])
                    for row in connection.execute("PRAGMA index_list(analysis_intents)")
                }
                self.assertEqual(intent_indexes["ix_analysis_intents_task_id"], 1)
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_dataset_downgrade_refuses_new_provenance_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "dataset-downgrade.sqlite3"
            migration.ensure_database(database_path)
            config = migration.alembic_config(database_path)
            head = migration.single_head(config)
            with sqlite3.connect(database_path) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                _insert_analysis_fixture(connection, "downgrade")
                connection.execute(
                    """
                    INSERT INTO workflows (
                        id, project_id, create_idempotency_key,
                        create_payload_sha256, workflow_type,
                        dataset_source_id, dataset_content_hash, goal,
                        generation_mode, status, row_version, event_sequence,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "workflow-downgrade",
                        "project-downgrade",
                        "dataset-key",
                        "f" * 64,
                        "dataset-analysis",
                        "source-downgrade",
                        "a" * 64,
                        "Analyze the immutable fixture dataset",
                        "local-deterministic",
                        "planning",
                        1,
                        0,
                        "2026-07-15 00:00:00",
                        "2026-07-15 00:00:00",
                    ),
                )
                connection.commit()
            before_schema = _schema_snapshot(database_path)
            before_data = _data_snapshot(database_path)

            with self.assertRaisesRegex(
                RuntimeError,
                "dataset-analysis workflow provenance exists",
            ):
                command.downgrade(config, "0003_model_assisted_workflows")

            self.assertEqual(_revision(database_path), head)
            self.assertEqual(_schema_snapshot(database_path), before_schema)
            self.assertEqual(_data_snapshot(database_path), before_data)

    def test_dataset_downgrade_refuses_multiple_intents_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "intent-lineage-downgrade.sqlite3"
            migration.ensure_database(database_path)
            config = migration.alembic_config(database_path)
            head = migration.single_head(config)
            with sqlite3.connect(database_path) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                _insert_analysis_fixture(connection, "lineage")
                connection.execute(
                    """
                    INSERT INTO analysis_intents (
                        id, task_id, project_id, dataset_source_id, objective, code,
                        payload_sha256, status, decision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "intent-lineage-2",
                        "task-lineage",
                        "project-lineage",
                        "source-lineage",
                        "Historical repaired analysis",
                        "print('repaired')",
                        "e" * 64,
                        "failed",
                        None,
                        "2026-07-15 00:01:00",
                        "2026-07-15 00:01:00",
                    ),
                )
                connection.commit()
            before_schema = _schema_snapshot(database_path)
            before_data = _data_snapshot(database_path)

            with self.assertRaisesRegex(
                RuntimeError,
                "multiple analysis intents share a task",
            ):
                command.downgrade(config, "0003_model_assisted_workflows")

            self.assertEqual(_revision(database_path), head)
            self.assertEqual(_schema_snapshot(database_path), before_schema)
            self.assertEqual(_data_snapshot(database_path), before_data)

    def test_dataset_downgrade_refuses_v2_and_v3_approvals_without_mutation(self) -> None:
        targets = (
            "0004_dataset_analysis_workflows",
            "0005_workflow_mutation_replay",
        )
        schema_versions = ("analysis-intent-v2", "analysis-intent-v3")
        for target_revision in targets:
            for payload_schema_version in schema_versions:
                with self.subTest(
                    target_revision=target_revision,
                    payload_schema_version=payload_schema_version,
                ):
                    with tempfile.TemporaryDirectory() as directory:
                        database_path = Path(directory) / "approval-downgrade.sqlite3"
                        config = migration.alembic_config(database_path)
                        command.upgrade(config, target_revision)
                        fixture_id = f"{target_revision[:4]}-{payload_schema_version[-2:]}"
                        with sqlite3.connect(database_path) as connection:
                            connection.execute("PRAGMA foreign_keys=ON")
                            _insert_analysis_fixture(connection, fixture_id)
                            _insert_analysis_approval(
                                connection,
                                fixture_id,
                                payload_schema_version,
                            )
                        before_schema = _schema_snapshot(database_path)
                        before_data = _data_snapshot(database_path)

                        with self.assertRaisesRegex(
                            RuntimeError,
                            rf"{payload_schema_version} approvals",
                        ):
                            command.downgrade(config, "0003_model_assisted_workflows")

                        self.assertEqual(_revision(database_path), target_revision)
                        self.assertEqual(_schema_snapshot(database_path), before_schema)
                        self.assertEqual(_data_snapshot(database_path), before_data)

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
                migration.single_head(migration.alembic_config(database_path)),
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
                answer_provenance = connection.execute(
                    """
                    SELECT generator, model, prompt_version, metadata_json
                    FROM answers WHERE id = 'answer-1'
                    """
                ).fetchone()
                self.assertEqual(
                    answer_provenance,
                    ("legacy-unknown", None, None, "{}"),
                )
                intent_provenance = connection.execute(
                    """
                    SELECT workflow_id, plan_step_id, previous_intent_id,
                           dataset_content_hash, expected_outputs, timeout_seconds,
                           risk_level, repair_attempt, error_summary, code_diff
                    FROM analysis_intents WHERE id = 'intent-1'
                    """
                ).fetchone()
                self.assertEqual(intent_provenance, (None,) * 10)
                run_intent = connection.execute(
                    "SELECT analysis_intent_id FROM runs WHERE id = 'run-1'"
                ).fetchone()
                self.assertEqual(run_intent, ("intent-1",))
                workflow_columns = {
                    str(row[1]): str(row[2])
                    for row in connection.execute("PRAGMA table_info(workflows)")
                }
                self.assertEqual(workflow_columns["generation_mode"], "VARCHAR(32)")
            backups = list((root / "backups").glob("science-core-legacy-v0-to-*.sqlite3"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(_table_counts(backups[0], LEGACY_TABLES), before)

    def test_control_plane_upgrade_preserves_rows_and_backfills_model_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "control-plane.sqlite3"
            config = migration.alembic_config(database_path)
            command.upgrade(config, "0002_workflow_control_plane")
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
                        "project-v2",
                        "Control-plane project",
                        "",
                        "/tmp/control-plane-project",
                        None,
                        "safe",
                        "2026-07-14 00:00:00",
                        "2026-07-14 00:00:00",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO workflows (
                        id, project_id, create_idempotency_key,
                        create_payload_sha256, workflow_type, goal, status,
                        row_version, event_sequence, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "workflow-v2",
                        "project-v2",
                        "legacy-control-plane-key",
                        _legacy_workflow_create_hash("Preserve this goal"),
                        "literature-synthesis",
                        "Preserve this goal",
                        "completed",
                        3,
                        7,
                        "2026-07-14 00:00:00",
                        "2026-07-14 00:01:00",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO answers (
                        id, project_id, workflow_id, task_id, question,
                        answer, unresolved_questions, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "answer-v2",
                        "project-v2",
                        "workflow-v2",
                        None,
                        "Preserve this question",
                        "Preserve this answer",
                        "[]",
                        "2026-07-14 00:01:00",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO workflow_jobs (
                        id, workflow_id, kind, operation_key, attempt,
                        input_sha256, handler_version, status, available_at,
                        request_idempotency_key, created_at, updated_at, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "legacy-idempotency-job",
                        "workflow-v2",
                        "generate-plan",
                        "workflow:workflow-v2:plan:1",
                        1,
                        "a" * 64,
                        "template-plan-v1",
                        "succeeded",
                        "2026-07-14 00:00:00",
                        "legacy-mutation-key",
                        "2026-07-14 00:00:00",
                        "2026-07-14 00:01:00",
                        "2026-07-14 00:01:00",
                    ),
                )
                connection.commit()

            migration.ensure_database(database_path)

            engine = create_engine(f"sqlite:///{database_path}")
            with Session(engine) as session:
                project = session.get(ProjectRecord, "project-v2")
                assert project is not None
                replayed = start_workflow(
                    session,
                    project,
                    WorkflowCreateIn(goal="Preserve this goal"),
                    "legacy-control-plane-key",
                )
                self.assertEqual(replayed.id, "workflow-v2")
                self.assertEqual(
                    len(list(session.scalars(select(WorkflowRecord)))),
                    1,
                )
            engine.dispose()

            with sqlite3.connect(database_path) as connection:
                workflow = connection.execute(
                    "SELECT goal, generation_mode FROM workflows WHERE id = 'workflow-v2'"
                ).fetchone()
                self.assertEqual(
                    workflow,
                    ("Preserve this goal", "local-deterministic"),
                )
                legacy_request_binding = connection.execute(
                    "SELECT request_idempotency_key, request_payload_sha256 "
                    "FROM workflow_jobs WHERE id = 'legacy-idempotency-job'"
                ).fetchone()
                self.assertEqual(
                    legacy_request_binding,
                    ("legacy-mutation-key", None),
                )
                answer = connection.execute(
                    """
                    SELECT answer, generator, model, prompt_version, metadata_json
                    FROM answers WHERE id = 'answer-v2'
                    """
                ).fetchone()
                self.assertEqual(
                    answer,
                    ("Preserve this answer", "local-extractive-v1", None, None, "{}"),
                )
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_failed_upgrade_keeps_prior_revision_and_does_not_claim_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "tampered-control-plane.sqlite3"
            config = migration.alembic_config(database_path)
            prior_revision = "0002_workflow_control_plane"
            expected_head = migration.single_head(config)
            self.assertNotEqual(prior_revision, expected_head)
            command.upgrade(config, prior_revision)
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    """
                    INSERT INTO projects (
                        id, title, description, project_path, research_domain,
                        execution_mode, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "tampered-project",
                        "Tampered project",
                        "",
                        "/tmp/tampered-project",
                        None,
                        "safe",
                        "2026-07-14 00:00:00",
                        "2026-07-14 00:00:00",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO workflows (
                        id, project_id, create_idempotency_key,
                        create_payload_sha256, workflow_type, goal, status,
                        row_version, event_sequence, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "tampered-workflow",
                        "tampered-project",
                        "tampered-key",
                        "0" * 64,
                        "literature-synthesis",
                        "Preserve the original goal",
                        "completed",
                        1,
                        0,
                        "2026-07-14 00:00:00",
                        "2026-07-14 00:00:00",
                    ),
                )
                connection.commit()
            before_schema = _schema_snapshot(database_path)
            before_data = _data_snapshot(database_path)

            with self.assertRaisesRegex(
                RuntimeError,
                "existing v1 payload hash does not match",
            ):
                migration.ensure_database(database_path)

            self.assertEqual(_revision(database_path), prior_revision)
            self.assertNotEqual(_revision(database_path), expected_head)
            self.assertEqual(_schema_snapshot(database_path), before_schema)
            self.assertEqual(_data_snapshot(database_path), before_data)
            with sqlite3.connect(database_path) as connection:
                row = connection.execute(
                    "SELECT create_payload_sha256, goal FROM workflows "
                    "WHERE id = 'tampered-workflow'"
                ).fetchone()
                self.assertEqual(row, ("0" * 64, "Preserve the original goal"))

    def test_legacy_review_job_survives_0002_upgrade_and_completes_with_v1_reviewer(self) -> None:
        passage = (
            "Brain computer interfaces improve communication for people with severe motor "
            "impairments using verified neural signals."
        )
        goal = "How do brain computer interfaces improve communication?"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "reviewing.sqlite3"
            migration.ensure_database(database_path)
            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )
            session_factory = sessionmaker(bind=engine, expire_on_commit=False)
            source_path = root / "paper.pdf"
            source_path.write_bytes(b"%PDF-legacy-review-migration")
            words = [
                {
                    "text": word,
                    "x0": float(index * 10),
                    "y0": 0.0,
                    "x1": float(index * 10 + 8),
                    "y1": 10.0,
                    "block": 0,
                    "line": 0,
                    "word": index,
                }
                for index, word in enumerate(passage.split())
            ]
            with session_factory() as session:
                project = ProjectRecord(
                    id="review-project",
                    title="Review migration",
                    description="",
                    project_path=str(root),
                    execution_mode="safe",
                )
                session.add(project)
                session.add(
                    SourceRecord(
                        id="review-source",
                        project_id=project.id,
                        title="Review source",
                        source_kind="pdf",
                        authors=[],
                        local_path=str(source_path),
                        ingestion_status="ready",
                        content_hash=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                        page_count=1,
                    )
                )
                session.add(
                    SourcePageRecord(
                        source_id="review-source",
                        page_index=0,
                        page_label="1",
                        width=500.0,
                        height=700.0,
                        text=passage,
                        words=words,
                    )
                )
                session.commit()
                workflow = start_workflow(
                    session,
                    project,
                    WorkflowCreateIn(goal=goal),
                    "legacy-review-migration-key",
                )
                workflow_id = workflow.id

            worker = WorkflowWorker(session_factory, poll_interval_seconds=0.01)
            self.assertTrue(asyncio.run(worker.run_once()))
            with session_factory() as session:
                workflow = session.get(WorkflowRecord, workflow_id)
                plan = session.scalar(
                    select(PlanRecord).where(PlanRecord.workflow_id == workflow_id)
                )
                approval = session.scalar(
                    select(ApprovalRecord).where(ApprovalRecord.workflow_id == workflow_id)
                )
                assert workflow is not None
                assert plan is not None
                assert approval is not None
                approve_plan(
                    session,
                    workflow,
                    approval_id=approval.id,
                    plan_id=plan.id,
                    plan_version=plan.version,
                    plan_sha256=plan.spec_sha256,
                    expected_revision=workflow.row_version,
                )
            self.assertTrue(asyncio.run(worker.run_once()))
            self.assertTrue(asyncio.run(worker.run_once()))
            with session_factory() as session:
                workflow = session.get(WorkflowRecord, workflow_id)
                synthesis_job = session.scalar(
                    select(JobRecord).where(
                        JobRecord.workflow_id == workflow_id,
                        JobRecord.kind == "execute-task",
                        JobRecord.status == "queued",
                    )
                )
                assert workflow is not None
                assert synthesis_job is not None
                assert synthesis_job.task_id is not None
                synthesis_task = session.get(TaskRecord, synthesis_job.task_id)
                assert synthesis_task is not None
                self.assertEqual(
                    synthesis_task.task_type,
                    "synthesize-extractive-claims",
                )
                synthesis_job.handler_version = "local-literature-v1"
                synthesis_job.input_sha256 = job_input_hash_for_handler_version(
                    session,
                    workflow,
                    kind="execute-task",
                    task=synthesis_task,
                    handler_version="local-literature-v1",
                )
                session.commit()
            self.assertTrue(asyncio.run(worker.run_once()))
            with session_factory() as session:
                workflow = session.get(WorkflowRecord, workflow_id)
                review_job = session.scalar(
                    select(JobRecord).where(
                        JobRecord.workflow_id == workflow_id,
                        JobRecord.kind == "review-workflow",
                        JobRecord.status == "queued",
                    )
                )
                assert workflow is not None
                assert review_job is not None
                self.assertEqual(workflow.status, "reviewing")
                self.assertEqual(review_job.handler_version, "deterministic-claims-v1")
                plan = session.scalar(
                    select(PlanRecord).where(PlanRecord.workflow_id == workflow_id)
                )
                approval = session.scalar(
                    select(ApprovalRecord).where(ApprovalRecord.workflow_id == workflow_id)
                )
                assert plan is not None
                assert approval is not None
                legacy_spec = json.loads(json.dumps(plan.spec_json))
                legacy_inspect_inputs = legacy_spec["steps"][0]["inputs"]
                legacy_inspect_inputs.pop("sourceIds", None)
                legacy_inspect_inputs.pop("frozenSources", None)
                plan.spec_json = legacy_spec
                plan.spec_sha256 = content_sha256(legacy_spec)
                approval.payload_schema_version = "workflow-plan-approval-v1"
                approval.intent_hash = plan_approval_hash(
                    plan,
                    approval.affected_resources,
                )
                for task in session.scalars(
                    select(TaskRecord).where(TaskRecord.workflow_id == workflow_id)
                ):
                    if task.task_type == "inspect-sources":
                        task.outputs = {
                            key: value
                            for key, value in task.outputs.items()
                            if key
                            not in {
                                "sourceDescriptors",
                                "sourcePageManifestHashes",
                            }
                        }
                    elif task.task_type == "extract-local-evidence":
                        task.outputs = {
                            key: value
                            for key, value in task.outputs.items()
                            if key != "evidenceFingerprints"
                        }
                for event_record in session.scalars(
                    select(EventRecord).where(EventRecord.workflow_id == workflow_id)
                ):
                    if event_record.event_type == "workflow.created":
                        event_record.payload = {
                            key: value
                            for key, value in event_record.payload.items()
                            if key != "generationMode"
                        }
                    elif event_record.event_type in {
                        "plan.generated",
                        "plan.approved",
                    }:
                        event_record.payload = {
                            **event_record.payload,
                            "planSha256": plan.spec_sha256,
                        }
                    elif event_record.event_type == "approval.requested":
                        event_record.payload = {
                            key: value
                            for key, value in event_record.payload.items()
                            if key
                            not in {
                                "riskLevel",
                                "reason",
                                "affectedResources",
                                "approvalSchemaVersion",
                            }
                        } | {
                            "payloadSha256": approval.intent_hash,
                        }
                legacy_versions = {
                    "generate-plan": "template-plan-v1",
                    "execute-task": "local-literature-v1",
                    "review-workflow": "deterministic-claims-v1",
                }
                for job in session.scalars(
                    select(JobRecord).where(JobRecord.workflow_id == workflow_id)
                ):
                    task = session.get(TaskRecord, job.task_id) if job.task_id else None
                    job.handler_version = legacy_versions[job.kind]
                    job.input_sha256 = job_input_hash_for_handler_version(
                        session,
                        workflow,
                        kind=job.kind,
                        task=task,
                        handler_version=job.handler_version,
                    )
                session.commit()
            engine.dispose()

            config = migration.alembic_config(database_path)
            command.downgrade(config, "0002_workflow_control_plane")
            self.assertEqual(_revision(database_path), "0002_workflow_control_plane")
            migration.ensure_database(database_path)

            upgraded_engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )
            upgraded_factory = sessionmaker(
                bind=upgraded_engine,
                expire_on_commit=False,
            )
            upgraded_worker = WorkflowWorker(
                upgraded_factory,
                poll_interval_seconds=0.01,
            )
            self.assertTrue(asyncio.run(upgraded_worker.run_once()))
            with upgraded_factory() as session:
                workflow = session.get(WorkflowRecord, workflow_id)
                review = session.scalar(
                    select(ReviewRecord).where(ReviewRecord.workflow_id == workflow_id)
                )
                assert workflow is not None
                assert review is not None
                self.assertEqual(workflow.status, "completed")
                self.assertEqual(review.review_type, "deterministic-claims-v1")
                self.assertEqual(review.verdict, "passed")
                snapshot = workflow_snapshot(session, workflow)
                assert snapshot.result is not None
                assert snapshot.latest_review is not None
                self.assertEqual(snapshot.result.integrity_status, "unfrozen")
                self.assertEqual(
                    snapshot.latest_review.result.schema_version,
                    "1",
                )
            upgraded_engine.dispose()

    def test_v2_approval_event_downgrade_fails_without_mutation(self) -> None:
        goal = "Preserve approval event consent metadata"
        create_payload = WorkflowCreateIn(goal=goal)
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "approval-event.sqlite3"
            migration.ensure_database(database_path)
            config = migration.alembic_config(database_path)
            head = migration.single_head(config)
            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )
            with Session(engine) as session:
                project = ProjectRecord(
                    id="approval-event-project",
                    title="Approval event project",
                    description="",
                    project_path="/tmp/approval-event-project",
                    execution_mode="safe",
                )
                workflow = WorkflowRecord(
                    id="approval-event-workflow",
                    project_id=project.id,
                    create_idempotency_key="approval-event-key",
                    create_payload_sha256=content_sha256(
                        create_payload.model_dump(
                            mode="json",
                            by_alias=True,
                            exclude_none=True,
                        )
                    ),
                    workflow_type=create_payload.workflow_type,
                    goal=goal,
                    generation_mode="local-deterministic",
                    status="waiting-plan-approval",
                    row_version=1,
                    event_sequence=1,
                )
                session.add_all([project, workflow])
                session.flush()
                session.add(
                    EventRecord(
                        id="approval-event",
                        project_id=project.id,
                        workflow_id=workflow.id,
                        sequence=1,
                        event_type="approval.requested",
                        payload={
                            "approvalId": "approval-id",
                            "planId": "plan-id",
                            "payloadSha256": "a" * 64,
                            "riskLevel": "low",
                            "reason": "Review the deterministic local research plan.",
                            "affectedResources": [f"project:{project.id}"],
                            "approvalSchemaVersion": "workflow-plan-approval-v2",
                        },
                    )
                )
                session.commit()
            engine.dispose()
            before = _schema_snapshot(database_path)

            with self.assertRaisesRegex(
                RuntimeError,
                "Cannot downgrade while v2 workflow approval events exist",
            ):
                command.downgrade(config, "0002_workflow_control_plane")

            self.assertEqual(_revision(database_path), head)
            self.assertEqual(_schema_snapshot(database_path), before)
            with sqlite3.connect(database_path) as connection:
                workflow_row = connection.execute(
                    "SELECT generation_mode, create_payload_sha256 FROM workflows "
                    "WHERE id = 'approval-event-workflow'"
                ).fetchone()
                event_payload = json.loads(
                    connection.execute(
                        "SELECT payload FROM events WHERE id = 'approval-event'"
                    ).fetchone()[0]
                )
                self.assertEqual(workflow_row[0], "local-deterministic")
                self.assertEqual(
                    workflow_row[1],
                    content_sha256(
                        create_payload.model_dump(
                            mode="json",
                            by_alias=True,
                            exclude_none=True,
                        )
                    ),
                )
                self.assertEqual(
                    event_payload["approvalSchemaVersion"],
                    "workflow-plan-approval-v2",
                )

    def test_remote_workflow_downgrade_fails_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "remote.sqlite3"
            migration.ensure_database(database_path)
            config = migration.alembic_config(database_path)
            head = migration.single_head(config)
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
                        "remote-project",
                        "Remote project",
                        "",
                        "/tmp/remote-project",
                        None,
                        "safe",
                        "2026-07-14 00:00:00",
                        "2026-07-14 00:00:00",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO workflows (
                        id, project_id, create_idempotency_key,
                        create_payload_sha256, workflow_type, goal,
                        generation_mode, status, row_version, event_sequence,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "remote-workflow",
                        "remote-project",
                        "remote-key",
                        "a" * 64,
                        "literature-synthesis",
                        "Preserve remote approval semantics",
                        "remote-model-assisted",
                        "completed",
                        1,
                        0,
                        "2026-07-14 00:00:00",
                        "2026-07-14 00:01:00",
                    ),
                )
                connection.commit()
            before = _schema_snapshot(database_path)

            with self.assertRaisesRegex(
                RuntimeError,
                "Cannot downgrade while remote-model-assisted workflows exist",
            ):
                command.downgrade(config, "0002_workflow_control_plane")

            self.assertEqual(_revision(database_path), head)
            self.assertEqual(_schema_snapshot(database_path), before)
            with sqlite3.connect(database_path) as connection:
                row = connection.execute(
                    "SELECT generation_mode, create_payload_sha256 FROM workflows "
                    "WHERE id = 'remote-workflow'"
                ).fetchone()
                self.assertEqual(row, ("remote-model-assisted", "a" * 64))

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
            config = migration.alembic_config(database_path)
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
