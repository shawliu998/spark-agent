"""Add project-scoped screening decisions.

Revision ID: 0009_screening_decisions
Revises: 0008_agent_observe_replan
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0009_screening_decisions"
down_revision: str | Sequence[str] | None = "0008_agent_observe_replan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sources", recreate="always") as batch:
        batch.create_unique_constraint("uq_source_project_id", ["project_id", "id"])

    op.create_table(
        "screening_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "criteria_version",
            sa.String(length=100),
            nullable=False,
            server_default=sa.text("'screening-v1'"),
        ),
        sa.Column(
            "row_version", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('include','exclude')", name="ck_screening_decision_value"
        ),
        sa.CheckConstraint(
            "length(criteria_version) BETWEEN 1 AND 100",
            name="ck_screening_decision_criteria_version",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR length(reason) <= 2000",
            name="ck_screening_decision_reason",
        ),
        sa.CheckConstraint(
            "row_version >= 1", name="ck_screening_decision_row_version"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "source_id"],
            ["sources.project_id", "sources.id"],
            name="fk_screening_decision_project_source",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "source_id", name="uq_screening_decision_project_source"
        ),
    )
    op.create_index(
        "ix_screening_decisions_project_id",
        "screening_decisions",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_screening_decisions_source_id",
        "screening_decisions",
        ["source_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_screening_decisions_source_id", table_name="screening_decisions"
    )
    op.drop_index(
        "ix_screening_decisions_project_id", table_name="screening_decisions"
    )
    op.drop_table("screening_decisions")
    with op.batch_alter_table("sources", recreate="always") as batch:
        batch.drop_constraint("uq_source_project_id", type_="unique")
