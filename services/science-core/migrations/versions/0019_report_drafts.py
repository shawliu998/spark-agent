"""Add project-bound persistent report drafts.

Revision ID: 0019_report_drafts
Revises: 0018_project_archive_state
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0019_report_drafts"
down_revision: str | Sequence[str] | None = "0018_project_archive_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sha256_check(column: str) -> str:
    return f"length({column}) = 64 AND {column} NOT GLOB '*[^0-9a-f]*'"


def upgrade() -> None:
    op.create_table(
        "report_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("workflow_id", sa.String(36), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("base_workflow_sha256", sa.String(64), nullable=False),
        sa.Column("base_result_sha256", sa.String(64), nullable=False),
        sa.Column("base_evidence_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("create_idempotency_key", sa.String(200), nullable=False),
        sa.Column("create_payload_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_id", "workflow_id"],
            ["workflows.project_id", "workflows.id"],
            name="fk_report_draft_workflow_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workflow_id", name="uq_report_draft_workflow"),
        sa.CheckConstraint("schema_version = '1'", name="ck_report_draft_schema_version"),
        sa.CheckConstraint("revision >= 1", name="ck_report_draft_revision"),
        sa.CheckConstraint(
            "status IN ('draft','needs-review','reviewed')",
            name="ck_report_draft_status",
        ),
        sa.CheckConstraint(
            "length(content_markdown) BETWEEN 1 AND 500000",
            name="ck_report_draft_content",
        ),
        sa.CheckConstraint(
            _sha256_check("content_sha256"),
            name="ck_report_draft_content_sha256",
        ),
        sa.CheckConstraint(
            _sha256_check("base_workflow_sha256"),
            name="ck_report_draft_workflow_sha256",
        ),
        sa.CheckConstraint(
            _sha256_check("base_result_sha256"),
            name="ck_report_draft_result_sha256",
        ),
        sa.CheckConstraint(
            _sha256_check("base_evidence_sha256"),
            name="ck_report_draft_evidence_sha256",
        ),
        sa.CheckConstraint(
            _sha256_check("create_payload_sha256"),
            name="ck_report_draft_create_payload_sha256",
        ),
    )
    for column in ("project_id", "workflow_id", "content_sha256", "status"):
        op.create_index(f"ix_report_drafts_{column}", "report_drafts", [column])
    op.create_table(
        "report_draft_mutations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("draft_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("postcondition_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["report_drafts.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "draft_id",
            "idempotency_key",
            name="uq_report_draft_mutation_key",
        ),
        sa.CheckConstraint(
            "operation IN ('create','save','review')",
            name="ck_report_draft_mutation_operation",
        ),
        sa.CheckConstraint(
            _sha256_check("payload_sha256"),
            name="ck_report_draft_mutation_payload_sha256",
        ),
        sa.CheckConstraint(
            _sha256_check("postcondition_sha256"),
            name="ck_report_draft_mutation_postcondition_sha256",
        ),
    )
    op.create_index(
        "ix_report_draft_mutations_draft_id",
        "report_draft_mutations",
        ["draft_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT 1 FROM report_draft_mutations LIMIT 1")
    ).first() or connection.execute(sa.text("SELECT 1 FROM report_drafts LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade while report draft business data exists.")
    op.drop_table("report_draft_mutations")
    op.drop_table("report_drafts")
