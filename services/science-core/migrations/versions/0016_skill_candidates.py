"""Add evaluated project-local skill candidates.

Revision ID: 0016_skill_candidates
Revises: 0015_memory_context_generation
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0016_skill_candidates"
down_revision: str | Sequence[str] | None = "0015_memory_context_generation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("workflow_id", sa.String(36), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("trigger_json", sa.JSON(), nullable=False),
        sa.Column("inputs_json", sa.JSON(), nullable=False),
        sa.Column("preconditions_json", sa.JSON(), nullable=False),
        sa.Column("allowed_tools_json", sa.JSON(), nullable=False),
        sa.Column("required_permissions_json", sa.JSON(), nullable=False),
        sa.Column("procedure_json", sa.JSON(), nullable=False),
        sa.Column("postconditions_json", sa.JSON(), nullable=False),
        sa.Column("failure_policy_json", sa.JSON(), nullable=False),
        sa.Column("provenance_requirements_json", sa.JSON(), nullable=False),
        sa.Column("origin_trace_ids", sa.JSON(), nullable=False),
        sa.Column("sanitized_source_hash", sa.String(64), nullable=False),
        sa.Column("parent_skill_id", sa.String(36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("generated_skill_md", sa.Text(), nullable=False),
        sa.Column("evaluation_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_id", "workflow_id"],
            ["workflows.project_id", "workflows.id"],
            name="fk_skill_candidate_workflow_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id", "content_hash", name="uq_skill_candidate_project_hash"
        ),
        sa.CheckConstraint("schema_version = '1'", name="ck_skill_candidate_schema_version"),
        sa.CheckConstraint("scope = 'project'", name="ck_skill_candidate_scope"),
        sa.CheckConstraint("version >= 1", name="ck_skill_candidate_version"),
        sa.CheckConstraint(
            "status IN ('failed-validation','awaiting-approval')",
            name="ck_skill_candidate_status",
        ),
        sa.CheckConstraint(
            "json_valid(origin_trace_ids) AND json_type(origin_trace_ids) = 'array' "
            "AND json_array_length(origin_trace_ids) = 1",
            name="ck_skill_candidate_origin_trace",
        ),
        *[
            sa.CheckConstraint(
                f"json_valid({column}) AND json_type({column}) = '{kind}'",
                name=name,
            )
            for column, kind, name in (
                ("trigger_json", "object", "ck_skill_candidate_trigger"),
                ("inputs_json", "object", "ck_skill_candidate_inputs"),
                ("preconditions_json", "array", "ck_skill_candidate_preconditions"),
                ("allowed_tools_json", "array", "ck_skill_candidate_allowed_tools"),
                ("required_permissions_json", "array", "ck_skill_candidate_permissions"),
                ("procedure_json", "array", "ck_skill_candidate_procedure"),
                ("postconditions_json", "array", "ck_skill_candidate_postconditions"),
                ("failure_policy_json", "object", "ck_skill_candidate_failure_policy"),
                (
                    "provenance_requirements_json",
                    "array",
                    "ck_skill_candidate_provenance",
                ),
                ("evaluation_json", "object", "ck_skill_candidate_evaluation"),
            )
        ],
        sa.CheckConstraint(
            "length(sanitized_source_hash) = 64 "
            "AND sanitized_source_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_candidate_sanitized_hash",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_candidate_content_hash",
        ),
    )
    op.create_index(
        "ix_skill_candidates_project_id", "skill_candidates", ["project_id"]
    )
    op.create_index(
        "ix_skill_candidates_workflow_id", "skill_candidates", ["workflow_id"]
    )
    op.create_index(
        "ix_skill_candidates_content_hash", "skill_candidates", ["content_hash"]
    )
    op.create_index("ix_skill_candidates_status", "skill_candidates", ["status"])


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM skill_candidates LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade while skill candidates exist.")
    op.drop_table("skill_candidates")
