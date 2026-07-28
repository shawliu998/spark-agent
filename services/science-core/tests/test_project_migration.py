from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest
from alembic import command

from open_science_core import migration


def _upgrade_project_archive_migration(database_path: Path) -> Any:
    config = migration.alembic_config(database_path)
    command.upgrade(config, "0017_skill_activations")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO projects (
                id, title, description, project_path, research_domain,
                execution_mode, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "project-migration",
                "Migration project",
                "",
                "/tmp/project-migration",
                None,
                "safe",
                "2026-07-24 00:00:00",
                "2026-07-24 00:00:00",
            ),
        )
        connection.commit()
    command.upgrade(config, "head")
    return config


def test_project_archive_migration_owns_only_its_columns() -> None:
    with TemporaryDirectory() as directory:
        database_path = Path(directory) / "science.sqlite3"
        config = _upgrade_project_archive_migration(database_path)
        with sqlite3.connect(database_path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(projects)")}
            assert {"row_version", "archived_at"} <= columns
            assert connection.execute(
                "SELECT row_version, archived_at FROM projects WHERE id = ?",
                ("project-migration",),
            ).fetchone() == (1, None)

        command.downgrade(config, "0017_skill_activations")
        with sqlite3.connect(database_path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(projects)")}
            assert "row_version" not in columns
            assert "archived_at" not in columns
            assert connection.execute(
                "SELECT title, project_path FROM projects WHERE id = ?",
                ("project-migration",),
            ).fetchone() == ("Migration project", "/tmp/project-migration")


@pytest.mark.parametrize(
    ("column", "value"),
    [("archived_at", "2026-07-24 00:00:00"), ("row_version", 2)],
)
def test_project_archive_migration_rejects_non_default_downgrade(
    column: str, value: object
) -> None:
    with TemporaryDirectory() as directory:
        database_path = Path(directory) / "science.sqlite3"
        config = _upgrade_project_archive_migration(database_path)
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                f"UPDATE projects SET {column} = ? WHERE id = ?",
                (value, "project-migration"),
            )
            connection.commit()

        with pytest.raises(RuntimeError, match="non-default state"):
            command.downgrade(config, "0017_skill_activations")
