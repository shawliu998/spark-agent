"""Add human-confirmed evidence direction judgments.

Revision ID: 0020_evidence_directions
Revises: 0019_report_drafts
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0020_evidence_directions"
down_revision: str | Sequence[str] | None = "0019_report_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_direction_judgments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("answer_id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column(
            "row_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["answer_id"],
            ["answers.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "source_id"],
            ["sources.project_id", "sources.id"],
            name="fk_evidence_direction_project_source",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "answer_id",
            "source_id",
            name="uq_evidence_direction_answer_source",
        ),
        sa.CheckConstraint(
            "direction IN ('supporting','mixed','insufficient')",
            name="ck_evidence_direction_value",
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name="ck_evidence_direction_row_version",
        ),
    )
    for column in ("project_id", "answer_id", "source_id", "direction"):
        op.create_index(
            f"ix_evidence_direction_judgments_{column}",
            "evidence_direction_judgments",
            [column],
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT 1 FROM evidence_direction_judgments LIMIT 1")
    ).first():
        raise RuntimeError(
            "Cannot downgrade while evidence direction judgment business data exists."
        )
    op.drop_table("evidence_direction_judgments")
