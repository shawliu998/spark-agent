"""Add persisted project-scoped extraction matrices.

Revision ID: 0010_extraction_matrix
Revises: 0009_screening_decisions
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0010_extraction_matrix"
down_revision: str | Sequence[str] | None = "0009_screening_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("evidence_spans", recreate="always") as batch:
        batch.create_unique_constraint("uq_evidence_span_source_id", ["source_id", "id"])

    op.create_table(
        "extraction_columns",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 120", name="ck_extraction_column_name"),
        sa.CheckConstraint("instructions IS NULL OR length(instructions) <= 2000", name="ck_extraction_column_instructions"),
        sa.CheckConstraint("order_index >= 0", name="ck_extraction_column_order_index"),
        sa.CheckConstraint("row_version >= 1", name="ck_extraction_column_row_version"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "id", name="uq_extraction_column_project_id"),
        sa.UniqueConstraint("project_id", "order_index", name="uq_extraction_column_order"),
    )
    op.create_index("ix_extraction_columns_project_id", "extraction_columns", ["project_id"])
    op.create_table(
        "extraction_cells",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("column_id", sa.String(length=36), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("review_status", sa.String(length=16), nullable=False, server_default=sa.text("'unreviewed'")),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(value)) BETWEEN 1 AND 20000", name="ck_extraction_cell_value"),
        sa.CheckConstraint("review_status IN ('unreviewed','confirmed')", name="ck_extraction_cell_review_status"),
        sa.CheckConstraint("row_version >= 1", name="ck_extraction_cell_row_version"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id", "source_id"], ["sources.project_id", "sources.id"], name="fk_extraction_cell_project_source", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id", "column_id"], ["extraction_columns.project_id", "extraction_columns.id"], name="fk_extraction_cell_project_column", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "id", "source_id", name="uq_extraction_cell_project_id_source"),
        sa.UniqueConstraint("project_id", "source_id", "column_id", name="uq_extraction_cell_project_source_column"),
    )
    op.create_index("ix_extraction_cells_project_id", "extraction_cells", ["project_id"])
    op.create_index("ix_extraction_cells_source_id", "extraction_cells", ["source_id"])
    op.create_index("ix_extraction_cells_column_id", "extraction_cells", ["column_id"])
    op.create_table(
        "extraction_cell_evidence",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("cell_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["project_id", "cell_id", "source_id"], ["extraction_cells.project_id", "extraction_cells.id", "extraction_cells.source_id"], name="fk_extraction_cell_evidence_cell_source", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id", "evidence_id"], ["evidence_spans.source_id", "evidence_spans.id"], name="fk_extraction_cell_evidence_source_evidence", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "cell_id", "source_id", "evidence_id"),
    )

    # Existing projects receive the same blank comparison columns new projects get;
    # no extraction values are manufactured during migration.
    for order_index, name in enumerate(("Summary", "Population", "Outcome")):
        op.execute(
            sa.text(
                "INSERT INTO extraction_columns "
                "(id, project_id, name, instructions, order_index, row_version, created_at, updated_at) "
                "SELECT lower(hex(randomblob(16))), id, :name, NULL, :order_index, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
                "FROM projects"
            ).bindparams(name=name, order_index=order_index)
        )


def downgrade() -> None:
    op.drop_table("extraction_cell_evidence")
    op.drop_index("ix_extraction_cells_column_id", table_name="extraction_cells")
    op.drop_index("ix_extraction_cells_source_id", table_name="extraction_cells")
    op.drop_index("ix_extraction_cells_project_id", table_name="extraction_cells")
    op.drop_table("extraction_cells")
    op.drop_index("ix_extraction_columns_project_id", table_name="extraction_columns")
    op.drop_table("extraction_columns")
    with op.batch_alter_table("evidence_spans", recreate="always") as batch:
        batch.drop_constraint("uq_evidence_span_source_id", type_="unique")
