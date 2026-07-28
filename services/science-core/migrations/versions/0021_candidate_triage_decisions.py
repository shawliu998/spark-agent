"""Add project-scoped discovery candidate triage decisions.

Revision ID: 0021_candidate_triage
Revises: 0020_evidence_directions
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0021_candidate_triage"
down_revision: str | Sequence[str] | None = "0020_evidence_directions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidate_triage_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "criteria_version",
            sa.String(length=100),
            nullable=False,
            server_default=sa.text("'candidate-triage-v1'"),
        ),
        sa.Column(
            "row_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('keep','reject','uncertain')",
            name="ck_candidate_triage_decision_value",
        ),
        sa.CheckConstraint(
            "length(criteria_version) BETWEEN 1 AND 100",
            name="ck_candidate_triage_criteria_version",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR length(reason) <= 2000",
            name="ck_candidate_triage_reason",
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name="ck_candidate_triage_row_version",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "candidate_id"],
            ["discovery_candidates.project_id", "discovery_candidates.id"],
            name="fk_candidate_triage_project_candidate",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "candidate_id",
            name="uq_candidate_triage_project_candidate",
        ),
    )
    op.create_index(
        "ix_candidate_triage_decisions_project_id",
        "candidate_triage_decisions",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_candidate_triage_decisions_candidate_id",
        "candidate_triage_decisions",
        ["candidate_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_triage_decisions_candidate_id",
        table_name="candidate_triage_decisions",
    )
    op.drop_index(
        "ix_candidate_triage_decisions_project_id",
        table_name="candidate_triage_decisions",
    )
    op.drop_table("candidate_triage_decisions")
