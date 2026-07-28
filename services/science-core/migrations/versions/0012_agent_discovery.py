"""Add durable bounded paper discovery records.

Revision ID: 0012_agent_discovery
Revises: 0011_repair_extraction_source_parent_key
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0012_agent_discovery"
down_revision: str | Sequence[str] | None = "0011_repair_extraction_source_parent_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite accepts a composite foreign key only when the parent column set is
    # covered by an explicit unique key.  This also makes the project/workflow
    # ownership boundary available to later provenance tables.
    op.create_index(
        "uq_workflows_project_id_id",
        "workflows",
        ["project_id", "id"],
        unique=True,
    )
    op.create_table(
        "discovery_specs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("previous_spec_id", sa.String(length=36), nullable=True),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("spec_json", sa.JSON(), nullable=False),
        sa.Column("spec_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("schema_version = '1'", name="ck_discovery_spec_schema_version"),
        sa.CheckConstraint("revision >= 1", name="ck_discovery_spec_revision"),
        sa.CheckConstraint(
            "(revision = 1 AND previous_spec_id IS NULL) OR "
            "(revision > 1 AND previous_spec_id IS NOT NULL)",
            name="ck_discovery_spec_revision_lineage",
        ),
        sa.CheckConstraint(
            "status IN ('pending-approval','approved','superseded','rejected')",
            name="ck_discovery_spec_status",
        ),
        sa.CheckConstraint(
            "json_valid(spec_json) AND json_type(spec_json) = 'object'",
            name="ck_discovery_spec_json",
        ),
        sa.CheckConstraint(
            "length(spec_sha256) = 64 AND spec_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_discovery_spec_sha256",
        ),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workflow_id", "previous_spec_id"],
            ["discovery_specs.workflow_id", "discovery_specs.id"],
            name="fk_discovery_specs_previous_spec",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "id", name="uq_discovery_spec_workflow_id"),
        sa.UniqueConstraint("workflow_id", "revision", name="uq_discovery_spec_workflow_revision"),
    )
    op.create_index("ix_discovery_specs_workflow_id", "discovery_specs", ["workflow_id"])
    op.create_index("ix_discovery_specs_spec_sha256", "discovery_specs", ["spec_sha256"])
    op.create_index("ix_discovery_specs_status", "discovery_specs", ["status"])
    op.create_index(
        "uq_discovery_spec_one_approved",
        "discovery_specs",
        ["workflow_id"],
        unique=True,
        sqlite_where=sa.text("status = 'approved'"),
    )

    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("discovery_spec_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("connector_name", sa.String(length=100), nullable=False),
        sa.Column("connector_version", sa.String(length=100), nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("operation_key", sa.String(length=300), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("request_idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("returned_count", sa.Integer(), nullable=True),
        sa.Column("novel_candidate_count", sa.Integer(), nullable=True),
        sa.Column("duplicate_count", sa.Integer(), nullable=True),
        sa.Column("candidate_set_sha256", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("schema_version = '1'", name="ck_tool_invocation_schema_version"),
        sa.CheckConstraint("attempt >= 1", name="ck_tool_invocation_attempt"),
        sa.CheckConstraint(
            "status IN ('prepared','pending','succeeded','failed','outcome-unknown','cancelled')",
            name="ck_tool_invocation_status",
        ),
        sa.CheckConstraint(
            "provider IN ('arxiv','crossref','openalex','pubmed')",
            name="ck_tool_invocation_provider",
        ),
        sa.CheckConstraint(
            "length(request_payload_sha256) = 64 AND request_payload_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_tool_invocation_request_sha256",
        ),
        sa.CheckConstraint(
            "json_valid(request_json) AND json_type(request_json) = 'object'",
            name="ck_tool_invocation_request_json",
        ),
        sa.CheckConstraint(
            "output_sha256 IS NULL OR (length(output_sha256) = 64 "
            "AND output_sha256 NOT GLOB '*[^0-9a-f]*')",
            name="ck_tool_invocation_output_sha256",
        ),
        sa.CheckConstraint(
            "candidate_set_sha256 IS NULL OR (length(candidate_set_sha256) = 64 "
            "AND candidate_set_sha256 NOT GLOB '*[^0-9a-f]*')",
            name="ck_tool_invocation_candidate_set_sha256",
        ),
        sa.CheckConstraint(
            "(status IN ('prepared','pending') AND output_sha256 IS NULL "
            "AND returned_count IS NULL AND novel_candidate_count IS NULL "
            "AND duplicate_count IS NULL AND candidate_set_sha256 IS NULL "
            "AND error_code IS NULL "
            "AND error_message IS NULL AND finished_at IS NULL) OR "
            "(status = 'succeeded' AND output_sha256 IS NOT NULL "
            "AND returned_count IS NOT NULL AND novel_candidate_count IS NOT NULL "
            "AND duplicate_count IS NOT NULL AND candidate_set_sha256 IS NOT NULL "
            "AND error_code IS NULL "
            "AND error_message IS NULL AND finished_at IS NOT NULL) OR "
            "(status IN ('failed','outcome-unknown') AND error_code IS NOT NULL "
            "AND returned_count = 0 AND novel_candidate_count = 0 "
            "AND duplicate_count = 0 AND candidate_set_sha256 IS NULL "
            "AND finished_at IS NOT NULL) OR "
            "(status = 'cancelled' AND output_sha256 IS NULL "
            "AND returned_count = 0 AND novel_candidate_count = 0 "
            "AND duplicate_count = 0 AND candidate_set_sha256 IS NULL "
            "AND finished_at IS NOT NULL)",
            name="ck_tool_invocation_terminal_result",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_id", "workflow_id"],
            ["workflows.project_id", "workflows.id"],
            name="fk_tool_invocation_workflow_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "job_id"],
            ["workflow_jobs.workflow_id", "workflow_jobs.id"],
            name="fk_tool_invocation_job_workflow",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "discovery_spec_id"],
            ["discovery_specs.workflow_id", "discovery_specs.id"],
            name="fk_tool_invocation_discovery_spec",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "id", name="uq_tool_invocation_project_id"),
        sa.UniqueConstraint(
            "workflow_id",
            "operation_key",
            "attempt",
            name="uq_tool_invocation_operation_attempt",
        ),
        sa.UniqueConstraint("request_idempotency_key", name="uq_tool_invocation_idempotency_key"),
    )
    op.create_index("ix_tool_invocations_project_id", "tool_invocations", ["project_id"])
    op.create_index("ix_tool_invocations_workflow_id", "tool_invocations", ["workflow_id"])
    op.create_index(
        "ix_tool_invocations_discovery_spec_id", "tool_invocations", ["discovery_spec_id"]
    )
    op.create_index("ix_tool_invocations_job_id", "tool_invocations", ["job_id"])
    op.create_index("ix_tool_invocations_operation_key", "tool_invocations", ["operation_key"])
    op.create_index("ix_tool_invocations_output_sha256", "tool_invocations", ["output_sha256"])
    op.create_index(
        "ix_tool_invocations_candidate_set_sha256",
        "tool_invocations",
        ["candidate_set_sha256"],
    )
    op.create_index("ix_tool_invocations_status", "tool_invocations", ["status"])

    op.create_table(
        "discovery_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=300), nullable=False),
        sa.Column("normalized_identity", sa.String(length=500), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("candidate_sha256", sa.String(length=64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("schema_version = '1'", name="ck_discovery_candidate_schema_version"),
        sa.CheckConstraint(
            "provider IN ('arxiv','crossref','openalex','pubmed')",
            name="ck_discovery_candidate_provider",
        ),
        sa.CheckConstraint(
            "json_valid(metadata_json) AND json_type(metadata_json) = 'object'",
            name="ck_discovery_candidate_metadata",
        ),
        sa.CheckConstraint(
            "length(candidate_sha256) = 64 AND candidate_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_discovery_candidate_sha256",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "id", name="uq_discovery_candidate_project_id"),
        sa.UniqueConstraint(
            "project_id",
            "normalized_identity",
            "candidate_sha256",
            name="uq_discovery_candidate_identity_content",
        ),
    )
    op.create_index("ix_discovery_candidates_project_id", "discovery_candidates", ["project_id"])
    op.create_index("ix_discovery_candidates_provider", "discovery_candidates", ["provider"])
    op.create_index(
        "ix_discovery_candidates_normalized_identity",
        "discovery_candidates",
        ["normalized_identity"],
    )
    op.create_index(
        "ix_discovery_candidates_candidate_sha256",
        "discovery_candidates",
        ["candidate_sha256"],
    )

    op.create_table(
        "discovery_candidate_occurrences",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("invocation_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("raw_item_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rank >= 1", name="ck_candidate_occurrence_rank"),
        sa.CheckConstraint(
            "length(raw_item_sha256) = 64 AND raw_item_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_candidate_occurrence_raw_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "invocation_id"],
            ["tool_invocations.project_id", "tool_invocations.id"],
            name="fk_candidate_occurrence_invocation_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "candidate_id"],
            ["discovery_candidates.project_id", "discovery_candidates.id"],
            name="fk_candidate_occurrence_candidate_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", "invocation_id", "candidate_id"),
        sa.UniqueConstraint(
            "invocation_id", "candidate_id", name="uq_candidate_occurrence_invocation_candidate"
        ),
        sa.UniqueConstraint(
            "invocation_id", "rank", name="uq_candidate_occurrence_invocation_rank"
        ),
    )


def downgrade() -> None:
    # Discovery records are evidence/provenance.  This revision has no
    # lossless representation in 0011, so refuse before dropping any table or
    # index when durable discovery state exists.
    connection = op.get_bind()
    discovery_tables = (
        "discovery_specs",
        "tool_invocations",
        "discovery_candidates",
        "discovery_candidate_occurrences",
    )
    populated = [
        table
        for table in discovery_tables
        if connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None
    ]
    if populated:
        raise RuntimeError(
            "Cannot downgrade while discovery provenance exists: " + ", ".join(populated)
        )
    op.drop_table("discovery_candidate_occurrences")
    op.drop_index("ix_discovery_candidates_candidate_sha256", table_name="discovery_candidates")
    op.drop_index("ix_discovery_candidates_normalized_identity", table_name="discovery_candidates")
    op.drop_index("ix_discovery_candidates_provider", table_name="discovery_candidates")
    op.drop_index("ix_discovery_candidates_project_id", table_name="discovery_candidates")
    op.drop_table("discovery_candidates")
    op.drop_index("ix_tool_invocations_status", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_output_sha256", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_candidate_set_sha256", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_operation_key", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_job_id", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_discovery_spec_id", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_workflow_id", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_project_id", table_name="tool_invocations")
    op.drop_table("tool_invocations")
    op.drop_index("uq_discovery_spec_one_approved", table_name="discovery_specs")
    op.drop_index("ix_discovery_specs_status", table_name="discovery_specs")
    op.drop_index("ix_discovery_specs_spec_sha256", table_name="discovery_specs")
    op.drop_index("ix_discovery_specs_workflow_id", table_name="discovery_specs")
    op.drop_table("discovery_specs")
    op.drop_index("uq_workflows_project_id_id", table_name="workflows")
