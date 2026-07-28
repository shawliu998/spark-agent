"""Add project-scoped research memory and immutable decision context snapshots.

Revision ID: 0013_research_memory_context
Revises: 0012_agent_discovery
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0013_research_memory_context"
down_revision: str | Sequence[str] | None = "0012_agent_discovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_memories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("scope_workflow_id", sa.String(length=36), nullable=True),
        sa.Column("subject_key", sa.String(length=300), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("previous_id", sa.String(length=36), nullable=True),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column("artifact_refs", sa.JSON(), nullable=False),
        sa.Column("invalidation_rule", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("creation_key", sa.String(length=200), nullable=False),
        sa.Column("memory_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("schema_version = '1'", name="ck_memory_schema_version"),
        sa.CheckConstraint("revision >= 1", name="ck_memory_revision"),
        sa.CheckConstraint("type IN ('user-decision','assumption','open-question','failure-lesson','operational-fact')", name="ck_memory_type"),
        sa.CheckConstraint("status IN ('candidate','committed','rejected','superseded','invalidated')", name="ck_memory_status"),
        sa.CheckConstraint("json_valid(content_json) AND json_type(content_json) = 'object'", name="ck_memory_content"),
        sa.CheckConstraint("json_valid(source_refs) AND json_type(source_refs) = 'array'", name="ck_memory_source_refs"),
        sa.CheckConstraint("json_valid(artifact_refs) AND json_type(artifact_refs) = 'array'", name="ck_memory_artifact_refs"),
        sa.CheckConstraint("length(memory_sha256) = 64 AND memory_sha256 NOT GLOB '*[^0-9a-f]*'", name="ck_memory_sha256"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id", "scope_workflow_id"], ["workflows.project_id", "workflows.id"], name="fk_memory_workflow_project", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id", "previous_id"], ["research_memories.project_id", "research_memories.id"], name="fk_memory_previous_project", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "id", name="uq_memory_project_id_id"),
        sa.UniqueConstraint("project_id", "subject_key", "revision", name="uq_memory_subject_revision"),
        sa.UniqueConstraint("project_id", "creation_key", name="uq_memory_creation_key"),
    )
    op.create_index("ix_research_memories_project_id", "research_memories", ["project_id"])
    op.create_index("ix_research_memories_scope_workflow_id", "research_memories", ["scope_workflow_id"])
    op.create_index("ix_research_memories_status", "research_memories", ["status"])
    op.create_index("ix_research_memories_memory_sha256", "research_memories", ["memory_sha256"])
    op.create_index(
        "uq_memory_one_committed_subject",
        "research_memories",
        ["project_id", "subject_key"],
        unique=True,
        sqlite_where=sa.text("status = 'committed'"),
    )
    op.create_index(
        "uq_workflow_plan_workflow_id_id",
        "workflow_plans",
        ["workflow_id", "id"],
        unique=True,
    )
    op.create_table(
        "agent_context_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=True),
        sa.Column("observation_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("selection_version", sa.Integer(), nullable=False),
        sa.Column("max_items", sa.Integer(), nullable=False),
        sa.Column("max_bytes", sa.Integer(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("context_sha256", sa.String(length=64), nullable=False),
        sa.Column("selected_memory_refs", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("schema_version = '1'", name="ck_context_snapshot_schema_version"),
        sa.CheckConstraint("selection_version = 1", name="ck_context_snapshot_selection_version"),
        sa.CheckConstraint("max_items BETWEEN 1 AND 12", name="ck_context_snapshot_max_items"),
        sa.CheckConstraint("max_bytes BETWEEN 1 AND 12000", name="ck_context_snapshot_max_bytes"),
        sa.CheckConstraint("json_valid(context_json) AND json_type(context_json) = 'object'", name="ck_context_snapshot_json"),
        sa.CheckConstraint("json_valid(selected_memory_refs) AND json_type(selected_memory_refs) = 'array'", name="ck_context_snapshot_refs"),
        sa.CheckConstraint("length(context_sha256) = 64 AND context_sha256 NOT GLOB '*[^0-9a-f]*'", name="ck_context_snapshot_sha256"),
        sa.ForeignKeyConstraint(["project_id", "workflow_id"], ["workflows.project_id", "workflows.id"], name="fk_context_snapshot_workflow_project", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id", "observation_id"], ["step_observations.workflow_id", "step_observations.id"], name="fk_context_snapshot_observation_workflow", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id", "plan_id"], ["workflow_plans.workflow_id", "workflow_plans.id"], name="fk_context_snapshot_plan_workflow", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "observation_id", name="uq_context_snapshot_observation"),
    )
    for name, columns in (
        ("ix_agent_context_snapshots_project_id", ["project_id"]),
        ("ix_agent_context_snapshots_workflow_id", ["workflow_id"]),
        ("ix_agent_context_snapshots_plan_id", ["plan_id"]),
        ("ix_agent_context_snapshots_observation_id", ["observation_id"]),
        ("ix_agent_context_snapshots_context_sha256", ["context_sha256"]),
    ):
        op.create_index(name, "agent_context_snapshots", columns)


def downgrade() -> None:
    connection = op.get_bind()
    # Do not partially remove this revision only for the next 0012 downgrade
    # to reject durable Discovery state.  A rejected downgrade is atomic.
    for table in (
        "agent_context_snapshots",
        "research_memories",
        "discovery_specs",
        "tool_invocations",
        "discovery_candidates",
        "discovery_candidate_occurrences",
    ):
        if connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            if table.startswith("discovery_") or table == "tool_invocations":
                raise RuntimeError("Cannot downgrade while discovery provenance exists.")
            raise RuntimeError("Research memory state cannot be losslessly downgraded.")
    op.drop_table("agent_context_snapshots")
    op.drop_table("research_memories")
    op.drop_index("uq_workflow_plan_workflow_id_id", table_name="workflow_plans")
