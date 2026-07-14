"""Freeze the pre-Alembic internal MVP schema.

Revision ID: 0001_legacy_baseline
Revises:
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_legacy_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("project_path", sa.Text(), nullable=False),
        sa.Column("research_domain", sa.String(length=200), nullable=True),
        sa.Column("execution_mode", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_path"),
    )

    op.create_table(
        "sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("arxiv_id", sa.String(length=100), nullable=True),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("publication_date", sa.String(length=32), nullable=True),
        sa.Column("ingestion_status", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "content_hash", name="uq_source_project_hash"),
    )
    op.create_index("ix_sources_content_hash", "sources", ["content_hash"], unique=False)
    op.create_index("ix_sources_project_id", "sources", ["project_id"], unique=False)

    op.create_table(
        "source_pages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.Column("page_label", sa.String(length=32), nullable=True),
        sa.Column("width", sa.Float(), nullable=False),
        sa.Column("height", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("words", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "page_index", name="uq_source_page"),
    )
    op.create_index("ix_source_pages_source_id", "source_pages", ["source_id"], unique=False)

    op.create_table(
        "answers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("unresolved_questions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_answers_project_id", "answers", ["project_id"], unique=False)

    op.create_table(
        "claims",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("answer_id", sa.String(length=36), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["answer_id"], ["answers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_claims_answer_id", "claims", ["answer_id"], unique=False)

    op.create_table(
        "evidence_spans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.Column("page_label", sa.String(length=32), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("bbox", sa.JSON(), nullable=True),
        sa.Column("coordinate_space", sa.String(length=64), nullable=False),
        sa.Column("quote_hash", sa.String(length=64), nullable=False),
        sa.Column("extraction_method", sa.String(length=100), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_spans_source_id", "evidence_spans", ["source_id"], unique=False)

    op.create_table(
        "claim_evidence",
        sa.Column("claim_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.Column("relationship_kind", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence_spans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("claim_id", "evidence_id"),
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("expected_outputs", sa.JSON(), nullable=False),
        sa.Column("acceptance_criteria", sa.JSON(), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"], unique=False)

    op.create_table(
        "analysis_intents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_source_id", sa.String(length=36), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_intents_dataset_source_id",
        "analysis_intents",
        ["dataset_source_id"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_intents_payload_sha256",
        "analysis_intents",
        ["payload_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_intents_project_id",
        "analysis_intents",
        ["project_id"],
        unique=False,
    )
    op.create_index("ix_analysis_intents_status", "analysis_intents", ["status"], unique=False)
    op.create_index("ix_analysis_intents_task_id", "analysis_intents", ["task_id"], unique=True)

    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("environment_hash", sa.String(length=64), nullable=True),
        sa.Column("input_artifacts", sa.JSON(), nullable=False),
        sa.Column("output_artifacts", sa.JSON(), nullable=False),
        sa.Column("logs_path", sa.Text(), nullable=True),
        sa.Column("token_usage", sa.JSON(), nullable=False),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_task_id", "runs", ["task_id"], unique=False)

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=200), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("parent_artifacts", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifacts_content_hash", "artifacts", ["content_hash"], unique=False)
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"], unique=False)

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("intent_hash", sa.String(length=64), nullable=False),
        sa.Column("requested_action", sa.String(length=200), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("affected_resources", sa.JSON(), nullable=False),
        sa.Column("user_decision", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approvals_intent_hash", "approvals", ["intent_hash"], unique=False)
    op.create_index("ix_approvals_task_id", "approvals", ["task_id"], unique=False)

    op.create_table(
        "events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_created_at", "events", ["created_at"], unique=False)
    op.create_index("ix_events_event_type", "events", ["event_type"], unique=False)
    op.create_index("ix_events_project_id", "events", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_events_project_id", table_name="events")
    op.drop_index("ix_events_event_type", table_name="events")
    op.drop_index("ix_events_created_at", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_approvals_task_id", table_name="approvals")
    op.drop_index("ix_approvals_intent_hash", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
    op.drop_index("ix_artifacts_content_hash", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_runs_task_id", table_name="runs")
    op.drop_table("runs")
    op.drop_index("ix_analysis_intents_task_id", table_name="analysis_intents")
    op.drop_index("ix_analysis_intents_status", table_name="analysis_intents")
    op.drop_index("ix_analysis_intents_project_id", table_name="analysis_intents")
    op.drop_index("ix_analysis_intents_payload_sha256", table_name="analysis_intents")
    op.drop_index("ix_analysis_intents_dataset_source_id", table_name="analysis_intents")
    op.drop_table("analysis_intents")
    op.drop_index("ix_tasks_project_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("claim_evidence")
    op.drop_index("ix_evidence_spans_source_id", table_name="evidence_spans")
    op.drop_table("evidence_spans")
    op.drop_index("ix_claims_answer_id", table_name="claims")
    op.drop_table("claims")
    op.drop_index("ix_answers_project_id", table_name="answers")
    op.drop_table("answers")
    op.drop_index("ix_source_pages_source_id", table_name="source_pages")
    op.drop_table("source_pages")
    op.drop_index("ix_sources_project_id", table_name="sources")
    op.drop_index("ix_sources_content_hash", table_name="sources")
    op.drop_table("sources")
    op.drop_table("projects")
