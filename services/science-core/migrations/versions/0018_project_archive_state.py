"""Add project rename and soft-archive state.

Revision ID: 0018_project_archive_state
Revises: 0017_skill_activations
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0018_project_archive_state"
down_revision: str | Sequence[str] | None = "0017_skill_activations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(
            sa.Column("row_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_check_constraint("ck_project_row_version", "row_version >= 1")


def downgrade() -> None:
    connection = op.get_bind()
    non_default_state = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM projects "
            "WHERE archived_at IS NOT NULL OR row_version != 1"
        )
    ).scalar_one()
    if non_default_state:
        raise RuntimeError(
            "Cannot downgrade project archive state while projects have non-default state."
        )
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_constraint("ck_project_row_version", type_="check")
        batch_op.drop_column("archived_at")
        batch_op.drop_column("row_version")
