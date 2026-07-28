"""Repair the source parent key required by persisted extraction cells.

Some development databases were stamped through 0010 while the earlier 0009
batch rebuild had not materialized its composite parent key. SQLite accepts the
child table DDL but later rejects every foreign-key check with a mismatch. Keep
the repair forward-only: 0010 itself requires this key, so downgrading must not
remove it.

Revision ID: 0011_repair_extraction_source_parent_key
Revises: 0010_extraction_matrix
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0011_repair_extraction_source_parent_key"
down_revision: str | Sequence[str] | None = "0010_extraction_matrix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PARENT_KEY = ("project_id", "id")


def _has_source_parent_key() -> bool:
    constraints = sa.inspect(op.get_bind()).get_unique_constraints("sources")
    return any(tuple(item.get("column_names") or ()) == _PARENT_KEY for item in constraints)


def upgrade() -> None:
    if _has_source_parent_key():
        return
    with op.batch_alter_table("sources", recreate="always") as batch:
        batch.create_unique_constraint("uq_source_project_id", list(_PARENT_KEY))


def downgrade() -> None:
    # The composite key is a parent requirement of 0010's extraction_cells.
    # Retaining it makes a downgrade to 0010 valid for both repaired and clean
    # histories, and avoids reintroducing SQLite's foreign-key mismatch.
    return None
