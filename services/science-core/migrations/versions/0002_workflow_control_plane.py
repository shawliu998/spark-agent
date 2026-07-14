"""Add the durable research workflow control plane.

Revision ID: 0002_workflow_control_plane
Revises: 0001_legacy_baseline
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_workflow_control_plane"
down_revision: str | Sequence[str] | None = "0001_legacy_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("create_idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("create_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "workflow_type",
            sa.String(length=64),
            nullable=False,
            server_default="literature-synthesis",
        ),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="planning"),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("event_sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocking_code", sa.String(length=100), nullable=True),
        sa.Column("blocking_message", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('planning','waiting-plan-approval','running','reviewing',"
            "'completed','blocked','failed','cancelled')",
            name="ck_workflow_status",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "create_idempotency_key", name="uq_workflow_project_create_key"
        ),
    )
    op.create_index("ix_workflows_project_id", "workflows", ["project_id"], unique=False)
    op.create_index("ix_workflows_status", "workflows", ["status"], unique=False)

    op.create_table(
        "workflow_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spec_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("spec_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending-approval",
        ),
        sa.Column(
            "generator", sa.String(length=100), nullable=False, server_default="template-v1"
        ),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending-approval','approved','rejected','superseded')",
            name="ck_workflow_plan_status",
        ),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "version", name="uq_workflow_plan_version"),
    )
    op.create_index(
        "ix_workflow_plans_spec_sha256", "workflow_plans", ["spec_sha256"], unique=False
    )
    op.create_index(
        "ix_workflow_plans_status", "workflow_plans", ["status"], unique=False
    )
    op.create_index(
        "ix_workflow_plans_workflow_id", "workflow_plans", ["workflow_id"], unique=False
    )
    op.create_index(
        "uq_workflow_one_approved_plan",
        "workflow_plans",
        ["workflow_id"],
        unique=True,
        sqlite_where=sa.text("status = 'approved'"),
    )

    with op.batch_alter_table("tasks", recreate="always") as batch:
        batch.add_column(sa.Column("workflow_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("plan_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("step_key", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("order_index", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("outputs", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch.add_column(sa.Column("risk_level", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("input_sha256", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1"))
        )
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_tasks_workflow_id", "workflows", ["workflow_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_foreign_key(
            "fk_tasks_plan_id", "workflow_plans", ["plan_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_unique_constraint("uq_task_plan_step_key", ["plan_id", "step_key"])
        batch.create_unique_constraint("uq_task_plan_order", ["plan_id", "order_index"])
    op.create_index("ix_tasks_workflow_id", "tasks", ["workflow_id"], unique=False)
    op.create_index("ix_tasks_plan_id", "tasks", ["plan_id"], unique=False)
    op.create_index("ix_tasks_input_sha256", "tasks", ["input_sha256"], unique=False)

    op.create_table(
        "workflow_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("operation_key", sa.String(length=300), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("handler_version", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("previous_job_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('generate-plan','execute-task','review-workflow')",
            name="ck_workflow_job_kind",
        ),
        sa.CheckConstraint(
            "status IN ('queued','leased','succeeded','failed','cancelled')",
            name="ck_workflow_job_status",
        ),
        sa.ForeignKeyConstraint(["previous_job_id"], ["workflow_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_key", "attempt", name="uq_workflow_job_attempt"),
        sa.UniqueConstraint("request_idempotency_key"),
    )
    op.create_index(
        "ix_workflow_job_claim", "workflow_jobs", ["status", "available_at"], unique=False
    )
    op.create_index(
        "ix_workflow_jobs_lease_token", "workflow_jobs", ["lease_token"], unique=False
    )
    op.create_index(
        "ix_workflow_jobs_operation_key", "workflow_jobs", ["operation_key"], unique=False
    )
    op.create_index("ix_workflow_jobs_status", "workflow_jobs", ["status"], unique=False)
    op.create_index("ix_workflow_jobs_task_id", "workflow_jobs", ["task_id"], unique=False)
    op.create_index(
        "ix_workflow_jobs_workflow_id", "workflow_jobs", ["workflow_id"], unique=False
    )
    op.create_index(
        "uq_workflow_job_active_operation",
        "workflow_jobs",
        ["operation_key"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued','leased')"),
    )

    op.create_table(
        "workflow_reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("review_type", sa.String(length=100), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "verdict IN ('passed','revision-required','blocked','failed')",
            name="ck_workflow_review_verdict",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["workflow_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_id", "review_type", "input_sha256", name="uq_workflow_review_input"
        ),
    )
    op.create_index(
        "ix_workflow_reviews_input_sha256", "workflow_reviews", ["input_sha256"], unique=False
    )
    op.create_index(
        "ix_workflow_reviews_plan_id", "workflow_reviews", ["plan_id"], unique=False
    )
    op.create_index(
        "ix_workflow_reviews_workflow_id", "workflow_reviews", ["workflow_id"], unique=False
    )

    with op.batch_alter_table("answers", recreate="always") as batch:
        batch.add_column(sa.Column("workflow_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("task_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_answers_workflow_id",
            "workflows",
            ["workflow_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_answers_task_id", "tasks", ["task_id"], ["id"], ondelete="CASCADE"
        )
    op.create_index("ix_answers_workflow_id", "answers", ["workflow_id"], unique=False)
    op.create_index("ix_answers_task_id", "answers", ["task_id"], unique=False)
    op.create_index(
        "uq_workflow_answer_task",
        "answers",
        ["task_id"],
        unique=True,
        sqlite_where=sa.text("task_id IS NOT NULL"),
    )

    with op.batch_alter_table("approvals", recreate="always") as batch:
        batch.alter_column(
            "task_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )
        batch.add_column(sa.Column("workflow_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("plan_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("subject_type", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("subject_id", sa.String(length=100), nullable=True))
        batch.add_column(
            sa.Column("payload_schema_version", sa.String(length=100), nullable=True)
        )
        batch.add_column(
            sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1"))
        )
        batch.create_foreign_key(
            "fk_approvals_workflow_id",
            "workflows",
            ["workflow_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_approvals_plan_id",
            "workflow_plans",
            ["plan_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint(
            "uq_approval_subject_payload",
            ["subject_type", "subject_id", "requested_action", "intent_hash"],
        )
    op.execute(
        sa.text(
            """
            UPDATE approvals
            SET subject_type = 'analysis-intent',
                subject_id = (
                    SELECT analysis_intents.id
                    FROM analysis_intents
                    WHERE analysis_intents.task_id = approvals.task_id
                ),
                payload_schema_version = 'analysis-intent-v1'
            WHERE task_id IS NOT NULL
            """
        )
    )
    op.create_index("ix_approvals_workflow_id", "approvals", ["workflow_id"], unique=False)
    op.create_index("ix_approvals_plan_id", "approvals", ["plan_id"], unique=False)

    with op.batch_alter_table("events", recreate="always") as batch:
        batch.add_column(sa.Column("workflow_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("task_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("job_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("sequence", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_events_workflow_id",
            "workflows",
            ["workflow_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_events_task_id", "tasks", ["task_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_foreign_key(
            "fk_events_job_id",
            "workflow_jobs",
            ["job_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_workflow_event_sequence", ["workflow_id", "sequence"]
        )
    op.create_index("ix_events_workflow_id", "events", ["workflow_id"], unique=False)
    op.create_index("ix_events_task_id", "events", ["task_id"], unique=False)
    op.create_index("ix_events_job_id", "events", ["job_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_events_job_id", table_name="events")
    op.drop_index("ix_events_task_id", table_name="events")
    op.drop_index("ix_events_workflow_id", table_name="events")
    with op.batch_alter_table("events", recreate="always") as batch:
        batch.drop_constraint("uq_workflow_event_sequence", type_="unique")
        batch.drop_constraint("fk_events_job_id", type_="foreignkey")
        batch.drop_constraint("fk_events_task_id", type_="foreignkey")
        batch.drop_constraint("fk_events_workflow_id", type_="foreignkey")
        batch.drop_column("sequence")
        batch.drop_column("job_id")
        batch.drop_column("task_id")
        batch.drop_column("workflow_id")

    op.drop_index("ix_approvals_plan_id", table_name="approvals")
    op.drop_index("ix_approvals_workflow_id", table_name="approvals")
    with op.batch_alter_table("approvals", recreate="always") as batch:
        batch.drop_constraint("uq_approval_subject_payload", type_="unique")
        batch.drop_constraint("fk_approvals_plan_id", type_="foreignkey")
        batch.drop_constraint("fk_approvals_workflow_id", type_="foreignkey")
        batch.drop_column("row_version")
        batch.drop_column("payload_schema_version")
        batch.drop_column("subject_id")
        batch.drop_column("subject_type")
        batch.drop_column("plan_id")
        batch.drop_column("workflow_id")
        batch.alter_column(
            "task_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )

    op.drop_index("uq_workflow_answer_task", table_name="answers")
    op.drop_index("ix_answers_task_id", table_name="answers")
    op.drop_index("ix_answers_workflow_id", table_name="answers")
    with op.batch_alter_table("answers", recreate="always") as batch:
        batch.drop_constraint("fk_answers_task_id", type_="foreignkey")
        batch.drop_constraint("fk_answers_workflow_id", type_="foreignkey")
        batch.drop_column("task_id")
        batch.drop_column("workflow_id")

    op.drop_index("ix_workflow_reviews_workflow_id", table_name="workflow_reviews")
    op.drop_index("ix_workflow_reviews_plan_id", table_name="workflow_reviews")
    op.drop_index("ix_workflow_reviews_input_sha256", table_name="workflow_reviews")
    op.drop_table("workflow_reviews")

    op.drop_index("uq_workflow_job_active_operation", table_name="workflow_jobs")
    op.drop_index("ix_workflow_jobs_workflow_id", table_name="workflow_jobs")
    op.drop_index("ix_workflow_jobs_task_id", table_name="workflow_jobs")
    op.drop_index("ix_workflow_jobs_status", table_name="workflow_jobs")
    op.drop_index("ix_workflow_jobs_operation_key", table_name="workflow_jobs")
    op.drop_index("ix_workflow_jobs_lease_token", table_name="workflow_jobs")
    op.drop_index("ix_workflow_job_claim", table_name="workflow_jobs")
    op.drop_table("workflow_jobs")

    op.drop_index("ix_tasks_input_sha256", table_name="tasks")
    op.drop_index("ix_tasks_plan_id", table_name="tasks")
    op.drop_index("ix_tasks_workflow_id", table_name="tasks")
    with op.batch_alter_table("tasks", recreate="always") as batch:
        batch.drop_constraint("uq_task_plan_order", type_="unique")
        batch.drop_constraint("uq_task_plan_step_key", type_="unique")
        batch.drop_constraint("fk_tasks_plan_id", type_="foreignkey")
        batch.drop_constraint("fk_tasks_workflow_id", type_="foreignkey")
        batch.drop_column("finished_at")
        batch.drop_column("started_at")
        batch.drop_column("row_version")
        batch.drop_column("input_sha256")
        batch.drop_column("risk_level")
        batch.drop_column("outputs")
        batch.drop_column("order_index")
        batch.drop_column("step_key")
        batch.drop_column("plan_id")
        batch.drop_column("workflow_id")

    op.drop_index("uq_workflow_one_approved_plan", table_name="workflow_plans")
    op.drop_index("ix_workflow_plans_workflow_id", table_name="workflow_plans")
    op.drop_index("ix_workflow_plans_status", table_name="workflow_plans")
    op.drop_index("ix_workflow_plans_spec_sha256", table_name="workflow_plans")
    op.drop_table("workflow_plans")
    op.drop_index("ix_workflows_status", table_name="workflows")
    op.drop_index("ix_workflows_project_id", table_name="workflows")
    op.drop_table("workflows")
