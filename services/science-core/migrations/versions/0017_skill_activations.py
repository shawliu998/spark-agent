"""Add exact project-local skill activation ledger.

Revision ID: 0017_skill_activations
Revises: 0016_skill_candidates
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0017_skill_activations"
down_revision: str | Sequence[str] | None = "0016_skill_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TARGET = ".opencode/skills/remember-verified-evidence/SKILL.md"


def upgrade() -> None:
    op.create_index(
        "uq_skill_candidate_owner_id",
        "skill_candidates",
        ["project_id", "workflow_id", "id"],
        unique=True,
    )
    op.create_table(
        "skill_activations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("workflow_id", sa.String(36), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("skill_name", sa.String(64), nullable=False),
        sa.Column("target_relative_path", sa.String(200), nullable=False),
        sa.Column("candidate_content_hash", sa.String(64), nullable=False),
        sa.Column("template_sha256", sa.String(64), nullable=False),
        sa.Column("evaluation_sha256", sa.String(64), nullable=False),
        sa.Column("approval_sha256", sa.String(64), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("prior_present", sa.Boolean(), nullable=False),
        sa.Column("prior_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("prior_sha256", sa.String(64), nullable=True),
        sa.Column("installed_sha256", sa.String(64), nullable=False),
        sa.Column("created_directory", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("rollback_idempotency_key", sa.String(200), nullable=True),
        sa.Column("rollback_request_sha256", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_id", "workflow_id"],
            ["workflows.project_id", "workflows.id"],
            name="fk_skill_activation_workflow_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "workflow_id", "candidate_id"],
            [
                "skill_candidates.project_id",
                "skill_candidates.workflow_id",
                "skill_candidates.id",
            ],
            name="fk_skill_activation_candidate_owner",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_skill_activation_project_idempotency",
        ),
        sa.CheckConstraint("schema_version = '1'", name="ck_skill_activation_schema_version"),
        sa.CheckConstraint(
            "skill_name = 'remember-verified-evidence'",
            name="ck_skill_activation_name",
        ),
        sa.CheckConstraint(f"target_relative_path = '{TARGET}'", name="ck_skill_activation_target"),
        sa.CheckConstraint(
            "status IN ('installing','active','rollback-pending','rolled-back','blocked')",
            name="ck_skill_activation_status",
        ),
        *[
            sa.CheckConstraint(
                f"length({column}) = 64 AND {column} NOT GLOB '*[^0-9a-f]*'",
                name=name,
            )
            for column, name in (
                ("candidate_content_hash", "ck_skill_activation_candidate_hash"),
                ("template_sha256", "ck_skill_activation_template_hash"),
                ("evaluation_sha256", "ck_skill_activation_evaluation_hash"),
                ("approval_sha256", "ck_skill_activation_approval_hash"),
                ("request_sha256", "ck_skill_activation_request_hash"),
                ("installed_sha256", "ck_skill_activation_installed_hash"),
            )
        ],
        sa.CheckConstraint(
            "(prior_present = 1 AND prior_bytes IS NOT NULL "
            "AND length(prior_sha256) = 64) OR "
            "(prior_present = 0 AND prior_bytes IS NULL AND prior_sha256 IS NULL)",
            name="ck_skill_activation_prior",
        ),
        sa.CheckConstraint(
            "(rollback_idempotency_key IS NULL AND rollback_request_sha256 IS NULL) OR "
            "(rollback_idempotency_key IS NOT NULL "
            "AND length(rollback_request_sha256) = 64)",
            name="ck_skill_activation_rollback_request",
        ),
    )
    for column in (
        "project_id",
        "workflow_id",
        "candidate_id",
        "approval_sha256",
        "status",
    ):
        op.create_index(
            f"ix_skill_activations_{column}",
            "skill_activations",
            [column],
        )
    op.create_index(
        "uq_skill_activation_active_target",
        "skill_activations",
        ["project_id", "skill_name", "target_relative_path"],
        # name is fixed today but remains explicit in the ownership key.
        # This prevents a future second Skill from inheriting this uniqueness.
        # SQLite partial unique indexes are the durable concurrency boundary.
        unique=True,
        sqlite_where=sa.text("status IN ('installing','active','rollback-pending')"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM skill_activations LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade while skill activations exist.")
    op.drop_table("skill_activations")
    op.drop_index("uq_skill_candidate_owner_id", table_name="skill_candidates")
