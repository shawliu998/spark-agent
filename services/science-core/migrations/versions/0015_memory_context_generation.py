"""Version immutable Memory context snapshots by committed-memory generation.

Revision ID: 0015_memory_context_generation
Revises: 0014_csl_json_candidate_origin
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0015_memory_context_generation"
down_revision: str | Sequence[str] | None = "0014_csl_json_candidate_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_context_snapshots", recreate="always") as batch:
        batch.drop_constraint("uq_context_snapshot_observation", type_="unique")
        batch.drop_constraint("ck_context_snapshot_schema_version", type_="check")
        batch.add_column(
            sa.Column("context_generation_sha256", sa.String(length=64), nullable=True)
        )
        batch.create_check_constraint(
            "ck_context_snapshot_schema_version",
            "schema_version IN ('1','2')",
        )
        batch.create_check_constraint(
            "ck_context_snapshot_generation",
            "(schema_version = '1' AND context_generation_sha256 IS NULL) OR "
            "(schema_version = '2' "
            "AND length(context_generation_sha256) = 64 "
            "AND context_generation_sha256 NOT GLOB '*[^0-9a-f]*')",
        )
        batch.create_unique_constraint(
            "uq_context_snapshot_generation",
            ["workflow_id", "observation_id", "context_generation_sha256"],
        )
    op.create_index(
        "ix_agent_context_snapshots_context_generation_sha256",
        "agent_context_snapshots",
        ["context_generation_sha256"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    # Refuse before mutating this revision when an older revision in the same
    # downgrade chain would reject durable state. This keeps a failed downgrade
    # at the current head instead of partially removing newer schema.
    for table in (
        "agent_context_snapshots",
        "research_memories",
        "discovery_specs",
        "tool_invocations",
        "discovery_candidates",
        "discovery_candidate_occurrences",
    ):
        if connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            if table.startswith("discovery_") or table == "tool_invocations":
                raise RuntimeError("Cannot downgrade while discovery provenance exists.")
            raise RuntimeError(
                "Cannot downgrade while immutable research memory state exists."
            )
    op.drop_index(
        "ix_agent_context_snapshots_context_generation_sha256",
        table_name="agent_context_snapshots",
    )
    with op.batch_alter_table("agent_context_snapshots", recreate="always") as batch:
        batch.drop_constraint("uq_context_snapshot_generation", type_="unique")
        batch.drop_constraint("ck_context_snapshot_generation", type_="check")
        batch.drop_constraint("ck_context_snapshot_schema_version", type_="check")
        batch.drop_column("context_generation_sha256")
        batch.create_check_constraint(
            "ck_context_snapshot_schema_version",
            "schema_version = '1'",
        )
        batch.create_unique_constraint(
            "uq_context_snapshot_observation",
            ["workflow_id", "observation_id"],
        )
