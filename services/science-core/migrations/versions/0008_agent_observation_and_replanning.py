"""Add durable agent observations, decisions, and bounded retry lineage.

Revision ID: 0008_agent_observe_replan
Revises: 0007_goal_aware_dataset_analysis
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib import import_module

from alembic import context, op
import sqlalchemy as sa


revision: str = "0008_agent_observe_replan"
down_revision: str | Sequence[str] | None = "0007_goal_aware_dataset_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_prior_revision = import_module("migrations.versions.0007_goal_aware_dataset_analysis")

_AGENT_LOOP_JOB_KINDS = (
    "observe-step",
    "decide-next-action",
    "apply-agent-decision",
)
_AGENT_LOOP_EVENT_TYPES = (
    "agent.observation-created",
    "agent.decision-proposed",
    "agent.decision-approved",
    "agent.decision-rejected",
    "agent.decision-applied",
    "agent.step-retry-requested",
    "agent.analysis-spec-revision-proposed",
    "agent.analysis-spec-revision-approved",
    "agent.loop-limit-reached",
    "agent.stopped",
)

_ANALYSIS_INTENT_WORKFLOW_BINDING_V7 = (
    "workflow_id IS NULL OR ("
    "plan_step_id IS NOT NULL "
    "AND plan_step_id = 'execute-analysis' "
    "AND dataset_content_hash IS NOT NULL "
    "AND expected_outputs IS NOT NULL "
    "AND json_valid(expected_outputs) "
    "AND json_type(expected_outputs) = 'array' "
    "AND timeout_seconds IS NOT NULL "
    "AND risk_level IS NOT NULL "
    "AND risk_level = 'high' "
    "AND repair_attempt IS NOT NULL "
    "AND ((repair_attempt = 0 "
    "AND previous_intent_id IS NULL "
    "AND code_diff IS NULL) "
    "OR (repair_attempt IN (1,2) "
    "AND previous_intent_id IS NOT NULL "
    "AND error_summary IS NOT NULL "
    "AND json_valid(error_summary) "
    "AND json_type(error_summary) = 'object' "
    "AND code_diff IS NOT NULL "
    "AND length(trim(code_diff)) > 0)))"
)
_ANALYSIS_INTENT_WORKFLOW_BINDING_V8 = (
    "workflow_id IS NULL OR ("
    "plan_step_id IS NOT NULL "
    "AND plan_step_id = 'execute-analysis' "
    "AND dataset_content_hash IS NOT NULL "
    "AND expected_outputs IS NOT NULL "
    "AND json_valid(expected_outputs) "
    "AND json_type(expected_outputs) = 'array' "
    "AND timeout_seconds IS NOT NULL "
    "AND risk_level IS NOT NULL "
    "AND risk_level = 'high' "
    "AND repair_attempt IS NOT NULL "
    "AND ((repair_attempt = 0 "
    "AND code_diff IS NULL "
    "AND previous_intent_id IS NULL) "
    "OR (repair_attempt IN (1,2) "
    "AND previous_intent_id IS NOT NULL "
    "AND error_summary IS NOT NULL "
    "AND json_valid(error_summary) "
    "AND json_type(error_summary) = 'object' "
    "AND code_diff IS NOT NULL "
    "AND length(trim(code_diff)) > 0)))"
)


def upgrade() -> None:
    with op.batch_alter_table("analysis_intents", recreate="always") as batch:
        batch.drop_constraint("ck_analysis_intent_workflow_binding", type_="check")
        batch.create_check_constraint(
            "ck_analysis_intent_workflow_binding",
            _ANALYSIS_INTENT_WORKFLOW_BINDING_V8,
        )

    op.drop_index("uq_run_analysis_intent", table_name="runs")
    with op.batch_alter_table("runs", recreate="always") as batch:
        batch.add_column(
            sa.Column(
                "attempt",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch.add_column(
            sa.Column("previous_run_id", sa.String(length=36), nullable=True)
        )
        batch.create_foreign_key(
            "fk_runs_previous_run_id",
            "runs",
            ["previous_run_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_unique_constraint(
            "uq_run_analysis_intent_attempt",
            ["analysis_intent_id", "attempt"],
        )
        batch.create_check_constraint("ck_run_attempt", "attempt >= 1")
        batch.create_check_constraint(
            "ck_run_attempt_lineage",
            "(attempt = 1 AND previous_run_id IS NULL) OR "
            "(attempt > 1 AND previous_run_id IS NOT NULL)",
        )
    op.create_index(
        "ix_runs_previous_run_id", "runs", ["previous_run_id"], unique=False
    )

    with op.batch_alter_table("workflow_jobs", recreate="always") as batch:
        batch.drop_constraint("ck_workflow_job_kind", type_="check")
        batch.create_unique_constraint(
            "uq_workflow_job_workflow_id", ["workflow_id", "id"]
        )
        batch.create_check_constraint(
            "ck_workflow_job_kind",
            "kind IN ('route-intent','generate-plan','execute-task','review-workflow',"
            "'observe-step','decide-next-action','apply-agent-decision')",
        )

    op.create_table(
        "step_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("source_job_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("review_id", sa.String(length=36), nullable=True),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("observation_type", sa.String(length=32), nullable=False),
        sa.Column("step_key", sa.String(length=100), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("facts_json", sa.JSON(), nullable=False),
        sa.Column("warnings_json", sa.JSON(), nullable=False),
        sa.Column("unresolved_questions_json", sa.JSON(), nullable=False),
        sa.Column("artifact_ids_json", sa.JSON(), nullable=False),
        sa.Column("failure_category", sa.String(length=32), nullable=False),
        sa.Column("recommended_actions_json", sa.JSON(), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("output_sha256", sa.String(length=64), nullable=False),
        sa.Column("generator", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("model_invocation_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "schema_version = '1'", name="ck_step_observation_schema_version"
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_step_observation_attempt"),
        sa.CheckConstraint(
            "observation_type IN ('pre-plan','step-output','analysis-execution','review')",
            name="ck_step_observation_type",
        ),
        sa.CheckConstraint(
            "status IN ('succeeded','failed','blocked','needs-review')",
            name="ck_step_observation_status",
        ),
        sa.CheckConstraint(
            "failure_category IN "
            "('none','input','method','runtime','artifact','review','unsupported','unknown')",
            name="ck_step_observation_failure_category",
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded','needs-review') AND failure_category = 'none') OR "
            "(status IN ('failed','blocked') AND failure_category != 'none')",
            name="ck_step_observation_failure_status",
        ),
        sa.CheckConstraint(
            "task_id IS NOT NULL OR observation_type IN ('pre-plan','review')",
            name="ck_step_observation_task_scope",
        ),
        sa.CheckConstraint(
            "plan_id IS NOT NULL OR observation_type = 'pre-plan'",
            name="ck_step_observation_plan_scope",
        ),
        sa.CheckConstraint(
            "json_valid(facts_json) AND json_type(facts_json) = 'array' "
            "AND json_array_length(facts_json) >= 1",
            name="ck_step_observation_facts",
        ),
        sa.CheckConstraint(
            "json_valid(warnings_json) AND json_type(warnings_json) = 'array'",
            name="ck_step_observation_warnings",
        ),
        sa.CheckConstraint(
            "json_valid(unresolved_questions_json) "
            "AND json_type(unresolved_questions_json) = 'array'",
            name="ck_step_observation_questions",
        ),
        sa.CheckConstraint(
            "json_valid(artifact_ids_json) "
            "AND json_type(artifact_ids_json) = 'array'",
            name="ck_step_observation_artifact_ids",
        ),
        sa.CheckConstraint(
            "json_valid(recommended_actions_json) "
            "AND json_type(recommended_actions_json) = 'array' "
            "AND json_array_length(recommended_actions_json) >= 1",
            name="ck_step_observation_recommended_actions",
        ),
        sa.CheckConstraint(
            "length(input_sha256) = 64 "
            "AND input_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_step_observation_input_sha256",
        ),
        sa.CheckConstraint(
            "length(output_sha256) = 64 "
            "AND output_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_step_observation_output_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["workflow_plans.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["review_id"], ["workflow_reviews.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "source_job_id"],
            ["workflow_jobs.workflow_id", "workflow_jobs.id"],
            name="fk_step_observations_source_job_workflow",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "model_invocation_id"],
            ["model_invocations.workflow_id", "model_invocations.id"],
            name="fk_step_observations_model_invocation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_id", "id", name="uq_step_observation_workflow_id"
        ),
        sa.UniqueConstraint(
            "source_job_id",
            "observation_type",
            name="uq_step_observation_source_job_type",
        ),
    )
    for name, columns in (
        ("ix_step_observations_workflow_id", ["workflow_id"]),
        ("ix_step_observations_plan_id", ["plan_id"]),
        ("ix_step_observations_task_id", ["task_id"]),
        ("ix_step_observations_source_job_id", ["source_job_id"]),
        ("ix_step_observations_run_id", ["run_id"]),
        ("ix_step_observations_review_id", ["review_id"]),
        ("ix_step_observations_model_invocation_id", ["model_invocation_id"]),
        ("ix_step_observations_input_sha256", ["input_sha256"]),
        ("ix_step_observations_output_sha256", ["output_sha256"]),
    ):
        op.create_index(name, "step_observations", columns, unique=False)

    op.create_table(
        "agent_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("observation_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("decision_revision", sa.Integer(), nullable=False),
        sa.Column("expected_workflow_revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("target_step_key", sa.String(length=100), nullable=True),
        sa.Column(
            "proposed_analysis_spec_json",
            sa.JSON(none_as_null=True),
            nullable=True,
        ),
        sa.Column("proposed_analysis_spec_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "analysis_spec_diff_json", sa.JSON(none_as_null=True), nullable=True
        ),
        sa.Column("clarification_requests_json", sa.JSON(), nullable=False),
        sa.Column("requires_user_confirmation", sa.Boolean(), nullable=False),
        sa.Column("generator", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("model_invocation_id", sa.String(length=36), nullable=True),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("output_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "schema_version = '1'", name="ck_agent_decision_schema_version"
        ),
        sa.CheckConstraint(
            "decision_revision >= 1", name="ck_agent_decision_revision"
        ),
        sa.CheckConstraint(
            "expected_workflow_revision >= 1",
            name="ck_agent_decision_workflow_revision",
        ),
        sa.CheckConstraint(
            "action IN ('continue','request-clarification','revise-analysis-spec',"
            "'retry-step','complete','stop')",
            name="ck_agent_decision_action",
        ),
        sa.CheckConstraint(
            "status IN ('proposed','waiting-user-confirmation','applied',"
            "'superseded','rejected','failed')",
            name="ck_agent_decision_status",
        ),
        sa.CheckConstraint(
            "json_valid(clarification_requests_json) "
            "AND json_type(clarification_requests_json) = 'array'",
            name="ck_agent_decision_clarifications",
        ),
        sa.CheckConstraint(
            "proposed_analysis_spec_json IS NULL OR "
            "(json_valid(proposed_analysis_spec_json) "
            "AND json_type(proposed_analysis_spec_json) = 'object')",
            name="ck_agent_decision_proposed_spec_json",
        ),
        sa.CheckConstraint(
            "analysis_spec_diff_json IS NULL OR "
            "(json_valid(analysis_spec_diff_json) "
            "AND json_type(analysis_spec_diff_json) = 'object')",
            name="ck_agent_decision_spec_diff_json",
        ),
        sa.CheckConstraint(
            "(proposed_analysis_spec_json IS NULL "
            "AND proposed_analysis_spec_sha256 IS NULL) OR "
            "(proposed_analysis_spec_json IS NOT NULL "
            "AND length(proposed_analysis_spec_sha256) = 64 "
            "AND proposed_analysis_spec_sha256 NOT GLOB '*[^0-9a-f]*')",
            name="ck_agent_decision_proposed_spec_sha256",
        ),
        sa.CheckConstraint(
            "(action IN ('continue','retry-step') "
            "AND target_step_key IS NOT NULL "
            "AND proposed_analysis_spec_json IS NULL "
            "AND analysis_spec_diff_json IS NULL "
            "AND json_array_length(clarification_requests_json) = 0 "
            "AND requires_user_confirmation = 0) OR "
            "(action = 'request-clarification' "
            "AND target_step_key IS NULL "
            "AND proposed_analysis_spec_json IS NULL "
            "AND analysis_spec_diff_json IS NULL "
            "AND json_array_length(clarification_requests_json) >= 1 "
            "AND requires_user_confirmation = 0) OR "
            "(action = 'revise-analysis-spec' "
            "AND target_step_key IS NULL "
            "AND proposed_analysis_spec_json IS NOT NULL "
            "AND analysis_spec_diff_json IS NOT NULL "
            "AND json_array_length(clarification_requests_json) = 0 "
            "AND requires_user_confirmation = 1) OR "
            "(action IN ('complete','stop') "
            "AND target_step_key IS NULL "
            "AND proposed_analysis_spec_json IS NULL "
            "AND analysis_spec_diff_json IS NULL "
            "AND json_array_length(clarification_requests_json) = 0 "
            "AND requires_user_confirmation = 0)",
            name="ck_agent_decision_action_shape",
        ),
        sa.CheckConstraint(
            "(status = 'applied' AND applied_at IS NOT NULL) OR "
            "(status != 'applied' AND applied_at IS NULL)",
            name="ck_agent_decision_applied_at",
        ),
        sa.CheckConstraint(
            "length(input_sha256) = 64 "
            "AND input_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_agent_decision_input_sha256",
        ),
        sa.CheckConstraint(
            "length(output_sha256) = 64 "
            "AND output_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_agent_decision_output_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "observation_id"],
            ["step_observations.workflow_id", "step_observations.id"],
            name="fk_agent_decisions_observation_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "model_invocation_id"],
            ["model_invocations.workflow_id", "model_invocations.id"],
            name="fk_agent_decisions_model_invocation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_id", "id", name="uq_agent_decision_workflow_id"
        ),
        sa.UniqueConstraint(
            "observation_id",
            "decision_revision",
            name="uq_agent_decision_observation_revision",
        ),
    )
    for name, columns in (
        ("ix_agent_decisions_workflow_id", ["workflow_id"]),
        ("ix_agent_decisions_observation_id", ["observation_id"]),
        ("ix_agent_decisions_model_invocation_id", ["model_invocation_id"]),
        ("ix_agent_decisions_input_sha256", ["input_sha256"]),
        ("ix_agent_decisions_output_sha256", ["output_sha256"]),
        ("ix_agent_decisions_status", ["status"]),
    ):
        op.create_index(name, "agent_decisions", columns, unique=False)
    op.create_index(
        "uq_agent_decision_live_observation",
        "agent_decisions",
        ["observation_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('proposed','waiting-user-confirmation')"),
    )
    op.create_index(
        "uq_agent_decision_applied_observation",
        "agent_decisions",
        ["observation_id"],
        unique=True,
        sqlite_where=sa.text("status = 'applied'"),
    )

    with op.batch_alter_table("interaction_requests", recreate="always") as batch:
        batch.add_column(
            sa.Column("agent_decision_id", sa.String(length=36), nullable=True)
        )
        batch.create_foreign_key(
            "fk_interaction_requests_agent_decision",
            "agent_decisions",
            ["workflow_id", "agent_decision_id"],
            ["workflow_id", "id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "ix_interaction_requests_agent_decision_id",
        "interaction_requests",
        ["agent_decision_id"],
        unique=False,
    )

    with op.batch_alter_table("analysis_specs", recreate="always") as batch:
        batch.add_column(
            sa.Column("proposed_by_decision_id", sa.String(length=36), nullable=True)
        )
        batch.add_column(sa.Column("revision_reason", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_analysis_specs_proposed_by_decision",
            "agent_decisions",
            ["workflow_id", "proposed_by_decision_id"],
            ["workflow_id", "id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_analysis_spec_decision_reason",
            "(proposed_by_decision_id IS NULL AND revision_reason IS NULL) OR "
            "(proposed_by_decision_id IS NOT NULL "
            "AND length(trim(revision_reason)) BETWEEN 1 AND 2000)",
        )
    op.create_index(
        "ix_analysis_specs_proposed_by_decision_id",
        "analysis_specs",
        ["proposed_by_decision_id"],
        unique=False,
    )


def _raise_if_agent_loop_provenance_exists(connection: sa.Connection) -> None:
    checks = (
        (
            "SELECT id FROM step_observations LIMIT 1",
            "Cannot downgrade while agent observation provenance exists.",
        ),
        (
            "SELECT id FROM agent_decisions LIMIT 1",
            "Cannot downgrade while agent decision provenance exists.",
        ),
        (
            "SELECT id FROM interaction_requests "
            "WHERE agent_decision_id IS NOT NULL LIMIT 1",
            "Cannot downgrade while interaction decision lineage exists.",
        ),
        (
            "SELECT id FROM analysis_specs "
            "WHERE proposed_by_decision_id IS NOT NULL OR revision_reason IS NOT NULL LIMIT 1",
            "Cannot downgrade while analysis spec decision lineage exists.",
        ),
        (
            "SELECT id FROM runs WHERE attempt != 1 OR previous_run_id IS NOT NULL LIMIT 1",
            "Cannot downgrade while repeated analysis run lineage exists.",
        ),
    )
    for query, message in checks:
        if connection.execute(sa.text(query)).first() is not None:
            raise RuntimeError(message)
    duplicate_run = connection.execute(
        sa.text(
            "SELECT analysis_intent_id FROM runs "
            "WHERE analysis_intent_id IS NOT NULL "
            "GROUP BY analysis_intent_id HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate_run is not None:
        raise RuntimeError(
            "Cannot downgrade while multiple runs share an analysis intent."
        )
    job_placeholders = ",".join(
        f":job_kind_{index}" for index in range(len(_AGENT_LOOP_JOB_KINDS))
    )
    if connection.execute(
        sa.text(
            "SELECT id FROM workflow_jobs WHERE kind IN "
            f"({job_placeholders}) LIMIT 1"
        ),
        {
            f"job_kind_{index}": kind
            for index, kind in enumerate(_AGENT_LOOP_JOB_KINDS)
        },
    ).first() is not None:
        raise RuntimeError("Cannot downgrade while agent loop jobs exist.")
    event_placeholders = ",".join(
        f":event_type_{index}" for index in range(len(_AGENT_LOOP_EVENT_TYPES))
    )
    if connection.execute(
        sa.text(
            "SELECT id FROM events WHERE event_type IN "
            f"({event_placeholders}) LIMIT 1"
        ),
        {
            f"event_type_{index}": event_type
            for index, event_type in enumerate(_AGENT_LOOP_EVENT_TYPES)
        },
    ).first() is not None:
        raise RuntimeError("Cannot downgrade while agent loop events exist.")


def downgrade() -> None:
    connection = op.get_bind()
    _raise_if_agent_loop_provenance_exists(connection)
    target_revision = context.get_revision_argument()
    if target_revision not in {down_revision, "-1"}:
        _prior_revision._raise_if_new_provenance_exists(connection)
        if target_revision not in {_prior_revision.down_revision, "-1"}:
            _prior_revision._prior_revision._preflight_autonomous_downgrade(
                connection
            )
            _prior_revision._prior_revision._downstream_downgrade_preflight(
                connection
            )

    op.drop_index(
        "ix_analysis_specs_proposed_by_decision_id", table_name="analysis_specs"
    )
    with op.batch_alter_table("analysis_specs", recreate="always") as batch:
        batch.drop_constraint("ck_analysis_spec_decision_reason", type_="check")
        batch.drop_constraint(
            "fk_analysis_specs_proposed_by_decision", type_="foreignkey"
        )
        batch.drop_column("revision_reason")
        batch.drop_column("proposed_by_decision_id")

    op.drop_index(
        "ix_interaction_requests_agent_decision_id",
        table_name="interaction_requests",
    )
    with op.batch_alter_table("interaction_requests", recreate="always") as batch:
        batch.drop_constraint(
            "fk_interaction_requests_agent_decision", type_="foreignkey"
        )
        batch.drop_column("agent_decision_id")

    op.drop_table("agent_decisions")
    op.drop_table("step_observations")

    with op.batch_alter_table("workflow_jobs", recreate="always") as batch:
        batch.drop_constraint("ck_workflow_job_kind", type_="check")
        batch.drop_constraint("uq_workflow_job_workflow_id", type_="unique")
        batch.create_check_constraint(
            "ck_workflow_job_kind",
            "kind IN ('route-intent','generate-plan','execute-task','review-workflow')",
        )

    op.drop_index("ix_runs_previous_run_id", table_name="runs")
    with op.batch_alter_table("runs", recreate="always") as batch:
        batch.drop_constraint("ck_run_attempt_lineage", type_="check")
        batch.drop_constraint("ck_run_attempt", type_="check")
        batch.drop_constraint("uq_run_analysis_intent_attempt", type_="unique")
        batch.drop_constraint("fk_runs_previous_run_id", type_="foreignkey")
        batch.drop_column("previous_run_id")
        batch.drop_column("attempt")
    op.create_index(
        "uq_run_analysis_intent",
        "runs",
        ["analysis_intent_id"],
        unique=True,
        sqlite_where=sa.text("analysis_intent_id IS NOT NULL"),
    )

    with op.batch_alter_table("analysis_intents", recreate="always") as batch:
        batch.drop_constraint("ck_analysis_intent_workflow_binding", type_="check")
        batch.create_check_constraint(
            "ck_analysis_intent_workflow_binding",
            _ANALYSIS_INTENT_WORKFLOW_BINDING_V7,
        )
