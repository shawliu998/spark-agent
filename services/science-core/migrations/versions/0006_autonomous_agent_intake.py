"""Add autonomous intent routing and durable interaction provenance.

Revision ID: 0006_autonomous_agent_intake
Revises: 0005_workflow_mutation_replay
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json

from alembic import context, op
import sqlalchemy as sa


revision: str = "0006_autonomous_agent_intake"
down_revision: str | Sequence[str] | None = "0005_workflow_mutation_replay"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_invocations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False, server_default="1"),
        sa.Column("operation_type", sa.String(length=100), nullable=False),
        sa.Column("operation_key", sa.String(length=300), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("generator", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("endpoint_identity", sa.String(length=500), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "token_usage", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "validation_errors", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("request_idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "schema_version = '1'", name="ck_model_invocation_schema_version"
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_model_invocation_attempt"),
        sa.CheckConstraint(
            "status IN ('pending','succeeded','failed')",
            name="ck_model_invocation_status",
        ),
        sa.CheckConstraint(
            "length(input_sha256) = 64 "
            "AND input_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_model_invocation_input_sha256",
        ),
        sa.CheckConstraint(
            "output_sha256 IS NULL OR "
            "(length(output_sha256) = 64 "
            "AND output_sha256 NOT GLOB '*[^0-9a-f]*')",
            name="ck_model_invocation_output_sha256",
        ),
        sa.CheckConstraint(
            "length(request_payload_sha256) = 64 "
            "AND request_payload_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_model_invocation_request_payload_sha256",
        ),
        sa.CheckConstraint(
            "json_valid(token_usage) AND json_type(token_usage) = 'object'",
            name="ck_model_invocation_token_usage",
        ),
        sa.CheckConstraint(
            "json_valid(validation_errors) "
            "AND json_type(validation_errors) = 'array'",
            name="ck_model_invocation_validation_errors",
        ),
        sa.CheckConstraint(
            "(status = 'pending' "
            "AND output_sha256 IS NULL "
            "AND error_code IS NULL "
            "AND error_message IS NULL "
            "AND finished_at IS NULL) OR "
            "(status = 'succeeded' "
            "AND output_sha256 IS NOT NULL "
            "AND error_code IS NULL "
            "AND error_message IS NULL "
            "AND finished_at IS NOT NULL) OR "
            "(status = 'failed' "
            "AND error_code IS NOT NULL "
            "AND finished_at IS NOT NULL)",
            name="ck_model_invocation_terminal_result",
        ),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_id",
            "operation_key",
            "attempt",
            name="uq_model_invocation_operation_attempt",
        ),
        sa.UniqueConstraint(
            "workflow_id", "id", name="uq_model_invocation_workflow_id"
        ),
        sa.UniqueConstraint(
            "request_idempotency_key",
            name="uq_model_invocation_idempotency_key",
        ),
    )
    op.create_index(
        "ix_model_invocations_workflow_id",
        "model_invocations",
        ["workflow_id"],
        unique=False,
    )
    op.create_index(
        "ix_model_invocations_operation_type",
        "model_invocations",
        ["operation_type"],
        unique=False,
    )
    op.create_index(
        "ix_model_invocations_input_sha256",
        "model_invocations",
        ["input_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_model_invocations_output_sha256",
        "model_invocations",
        ["output_sha256"],
        unique=False,
    )

    op.create_table(
        "intent_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("intent", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reasoning_summary", sa.Text(), nullable=False),
        sa.Column(
            "selected_source_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "missing_inputs", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("proposed_workflow_type", sa.String(length=64), nullable=True),
        sa.Column("generator", sa.String(length=100), nullable=False),
        sa.Column("used_model", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("parse_result", sa.String(length=64), nullable=False),
        sa.Column("model_invocation_id", sa.String(length=36), nullable=True),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("output_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_intent_decision_revision"),
        sa.CheckConstraint(
            "intent IN ('literature-synthesis','dataset-analysis','mixed-research',"
            "'clarification-required','unsupported')",
            name="ck_intent_decision_intent",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_intent_decision_confidence",
        ),
        sa.CheckConstraint(
            "proposed_workflow_type IS NULL OR proposed_workflow_type IN "
            "('literature-synthesis','dataset-analysis','mixed-research')",
            name="ck_intent_decision_proposed_workflow_type",
        ),
        sa.CheckConstraint(
            "((intent IN ('literature-synthesis','dataset-analysis','mixed-research')) "
            "AND proposed_workflow_type = intent) OR "
            "(intent IN ('clarification-required','unsupported') "
            "AND proposed_workflow_type IS NULL)",
            name="ck_intent_decision_resolution",
        ),
        sa.CheckConstraint(
            "json_valid(selected_source_ids) "
            "AND json_type(selected_source_ids) = 'array'",
            name="ck_intent_decision_selected_source_ids",
        ),
        sa.CheckConstraint(
            "json_valid(missing_inputs) AND json_type(missing_inputs) = 'array'",
            name="ck_intent_decision_missing_inputs",
        ),
        sa.CheckConstraint(
            "parse_result IN ('valid','model-not-configured','model-request-failed',"
            "'model-request-outcome-unknown','model-output-invalid',"
            "'deterministic-capability-guard')",
            name="ck_intent_decision_parse_result",
        ),
        sa.CheckConstraint(
            "length(input_sha256) = 64 "
            "AND input_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_intent_decision_input_sha256",
        ),
        sa.CheckConstraint(
            "length(output_sha256) = 64 "
            "AND output_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_intent_decision_output_sha256",
        ),
        sa.CheckConstraint(
            "(used_model = 1 "
            "AND model_invocation_id IS NOT NULL "
            "AND model IS NOT NULL) OR "
            "(used_model = 0 "
            "AND model_invocation_id IS NULL "
            "AND model IS NULL)",
            name="ck_intent_decision_model_binding",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "model_invocation_id"],
            ["model_invocations.workflow_id", "model_invocations.id"],
            name="fk_intent_decision_model_invocation",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_id", "revision", name="uq_intent_decision_workflow_revision"
        ),
        sa.UniqueConstraint(
            "workflow_id", "id", name="uq_intent_decision_workflow_id"
        ),
        sa.UniqueConstraint(
            "model_invocation_id", name="uq_intent_decision_model_invocation"
        ),
    )
    op.create_index(
        "ix_intent_decisions_workflow_id",
        "intent_decisions",
        ["workflow_id"],
        unique=False,
    )
    op.create_index(
        "ix_intent_decisions_intent", "intent_decisions", ["intent"], unique=False
    )
    op.create_index(
        "ix_intent_decisions_model_invocation_id",
        "intent_decisions",
        ["model_invocation_id"],
        unique=False,
    )
    op.create_index(
        "ix_intent_decisions_input_sha256",
        "intent_decisions",
        ["input_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_intent_decisions_output_sha256",
        "intent_decisions",
        ["output_sha256"],
        unique=False,
    )

    op.create_table(
        "interaction_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("step_id", sa.String(length=100), nullable=True),
        sa.Column("request_key", sa.String(length=200), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("workflow_revision", sa.Integer(), nullable=False),
        sa.Column("request_type", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column(
            "response_schema", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("revision >= 1", name="ck_interaction_request_revision"),
        sa.CheckConstraint(
            "workflow_revision >= 1", name="ck_interaction_request_workflow_revision"
        ),
        sa.CheckConstraint(
            "request_type IN ('single-choice','multi-choice','text','number','boolean',"
            "'column-selection','method-confirmation','assumption-confirmation')",
            name="ck_interaction_request_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','answered','superseded','cancelled')",
            name="ck_interaction_request_status",
        ),
        sa.CheckConstraint(
            "json_valid(options) AND json_type(options) = 'array'",
            name="ck_interaction_request_options",
        ),
        sa.CheckConstraint(
            "json_valid(response_schema) AND json_type(response_schema) = 'object'",
            name="ck_interaction_request_response_schema",
        ),
        sa.CheckConstraint(
            "length(request_sha256) = 64 "
            "AND request_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_interaction_request_sha256",
        ),
        sa.CheckConstraint(
            "(status = 'answered' AND answered_at IS NOT NULL) OR "
            "(status != 'answered')",
            name="ck_interaction_request_answered_at",
        ),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_id",
            "request_key",
            "revision",
            name="uq_interaction_request_workflow_key_revision",
        ),
    )
    op.create_index(
        "ix_interaction_requests_workflow_id",
        "interaction_requests",
        ["workflow_id"],
        unique=False,
    )
    op.create_index(
        "ix_interaction_requests_status",
        "interaction_requests",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_interaction_requests_request_sha256",
        "interaction_requests",
        ["request_sha256"],
        unique=False,
    )

    op.create_table(
        "user_responses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("interaction_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("expected_workflow_revision", sa.Integer(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("response_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_user_response_revision"),
        sa.CheckConstraint(
            "expected_workflow_revision >= 1",
            name="ck_user_response_expected_workflow_revision",
        ),
        sa.CheckConstraint("json_valid(response_json)", name="ck_user_response_json"),
        sa.CheckConstraint(
            "length(response_sha256) = 64 "
            "AND response_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_user_response_sha256",
        ),
        sa.CheckConstraint(
            "length(request_payload_sha256) = 64 "
            "AND request_payload_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_user_response_request_payload_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["interaction_id"], ["interaction_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "interaction_id", "revision", name="uq_user_response_interaction_revision"
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_user_response_idempotency_key"
        ),
    )
    op.create_index(
        "ix_user_responses_interaction_id",
        "user_responses",
        ["interaction_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_responses_response_sha256",
        "user_responses",
        ["response_sha256"],
        unique=False,
    )

    with op.batch_alter_table("workflows", recreate="always") as batch:
        batch.drop_constraint("ck_workflow_dataset_identity", type_="check")
        batch.drop_constraint("ck_workflow_type", type_="check")
        batch.drop_constraint("ck_workflow_status", type_="check")
        batch.add_column(
            sa.Column(
                "creation_mode",
                sa.String(length=32),
                nullable=False,
                server_default="fixed-workflow",
            )
        )
        batch.add_column(
            sa.Column(
                "selected_source_ids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(
            sa.Column("current_intent_decision_id", sa.String(length=36), nullable=True)
        )
        batch.alter_column(
            "workflow_type",
            existing_type=sa.String(length=64),
            nullable=True,
            existing_server_default="literature-synthesis",
        )
        batch.create_foreign_key(
            "fk_workflows_current_intent_decision",
            "intent_decisions",
            ["id", "current_intent_decision_id"],
            ["workflow_id", "id"],
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        )
        batch.create_check_constraint(
            "ck_workflow_status",
            "status IN ('routing','waiting-clarification','planning',"
            "'waiting-plan-approval','running','reviewing','completed','unsupported',"
            "'blocked','failed','cancelled')",
        )
        batch.create_check_constraint(
            "ck_workflow_creation_mode",
            "creation_mode IN ('fixed-workflow','autonomous')",
        )
        batch.create_check_constraint(
            "ck_workflow_type",
            "workflow_type IS NULL OR "
            "workflow_type IN ('literature-synthesis','dataset-analysis')",
        )
        batch.create_check_constraint(
            "ck_workflow_intake_state",
            "(workflow_type IS NULL "
            "AND creation_mode = 'autonomous' "
            "AND status IN ('routing','waiting-clarification','unsupported',"
            "'blocked','failed','cancelled')) OR "
            "(workflow_type IS NOT NULL "
            "AND status NOT IN ('routing','waiting-clarification','unsupported'))",
        )
        batch.create_check_constraint(
            "ck_workflow_selected_source_ids",
            "json_valid(selected_source_ids) "
            "AND json_type(selected_source_ids) = 'array'",
        )
        batch.create_check_constraint(
            "ck_workflow_dataset_identity",
            "(workflow_type = 'dataset-analysis' "
            "AND dataset_source_id IS NOT NULL "
            "AND dataset_content_hash IS NOT NULL) OR "
            "((workflow_type IS NULL OR workflow_type != 'dataset-analysis') "
            "AND dataset_source_id IS NULL "
            "AND dataset_content_hash IS NULL)",
        )
    op.create_index(
        "ix_workflows_current_intent_decision_id",
        "workflows",
        ["current_intent_decision_id"],
        unique=False,
    )

    with op.batch_alter_table("workflow_jobs", recreate="always") as batch:
        batch.drop_constraint("ck_workflow_job_kind", type_="check")
        batch.create_check_constraint(
            "ck_workflow_job_kind",
            "kind IN ('route-intent','generate-plan','execute-task','review-workflow')",
        )


def _raise_if_row(connection: sa.Connection, query: str, message: str) -> None:
    if connection.execute(sa.text(query)).first() is not None:
        raise RuntimeError(message)


def _preflight_autonomous_downgrade(connection: sa.Connection) -> None:
    _raise_if_row(
        connection,
        "SELECT id FROM model_invocations LIMIT 1",
        "Cannot downgrade while model invocation provenance exists; revision 0005 "
        "cannot preserve it.",
    )
    _raise_if_row(
        connection,
        "SELECT id FROM intent_decisions LIMIT 1",
        "Cannot downgrade while intent decision provenance exists; revision 0005 "
        "cannot preserve it.",
    )
    _raise_if_row(
        connection,
        "SELECT id FROM interaction_requests LIMIT 1",
        "Cannot downgrade while interaction request provenance exists; revision 0005 "
        "cannot preserve it.",
    )
    _raise_if_row(
        connection,
        "SELECT id FROM user_responses LIMIT 1",
        "Cannot downgrade while user response provenance exists; revision 0005 "
        "cannot preserve it.",
    )
    _raise_if_row(
        connection,
        "SELECT id FROM workflows "
        "WHERE creation_mode != 'fixed-workflow' "
        "OR current_intent_decision_id IS NOT NULL "
        "OR workflow_type IS NULL "
        "OR status IN ('routing','waiting-clarification','unsupported') "
        "OR json_array_length(selected_source_ids) != 0 LIMIT 1",
        "Cannot downgrade while autonomous workflow intake provenance exists; revision "
        "0005 cannot preserve it.",
    )
    _raise_if_row(
        connection,
        "SELECT id FROM workflow_jobs WHERE kind = 'route-intent' LIMIT 1",
        "Cannot downgrade while intent routing job history exists; revision 0005 cannot "
        "preserve it.",
    )


def _preflight_mutation_binding_downgrade(connection: sa.Connection) -> None:
    _raise_if_row(
        connection,
        "SELECT id FROM workflow_jobs "
        "WHERE request_payload_sha256 IS NOT NULL LIMIT 1",
        "Cannot downgrade while workflow mutation idempotency bindings exist; "
        "the prior schema cannot preserve exact retry or resume replay semantics.",
    )


def _preflight_dataset_downgrade(connection: sa.Connection) -> None:
    _raise_if_row(
        connection,
        "SELECT id FROM workflows WHERE workflow_type = 'dataset-analysis' "
        "OR dataset_source_id IS NOT NULL OR dataset_content_hash IS NOT NULL LIMIT 1",
        "Cannot downgrade while dataset-analysis workflow provenance exists; revision "
        "0003 cannot preserve it.",
    )
    _raise_if_row(
        connection,
        """
        SELECT id
        FROM analysis_intents
        WHERE workflow_id IS NOT NULL
           OR plan_step_id IS NOT NULL
           OR previous_intent_id IS NOT NULL
           OR dataset_content_hash IS NOT NULL
           OR expected_outputs IS NOT NULL
           OR timeout_seconds IS NOT NULL
           OR risk_level IS NOT NULL
           OR repair_attempt IS NOT NULL
           OR error_summary IS NOT NULL
           OR code_diff IS NOT NULL
        LIMIT 1
        """,
        "Cannot downgrade while dataset-analysis intent provenance or repair lineage "
        "exists; revision 0003 cannot preserve it.",
    )
    _raise_if_row(
        connection,
        "SELECT task_id FROM analysis_intents "
        "GROUP BY task_id HAVING COUNT(*) > 1 LIMIT 1",
        "Cannot downgrade while multiple analysis intents share a task; revision 0003 "
        "requires one intent per task.",
    )
    _raise_if_row(
        connection,
        "SELECT id FROM workflow_reviews "
        "WHERE verdict = 'passed-with-warnings' LIMIT 1",
        "Cannot downgrade while passed-with-warnings reviews exist; revision 0003 "
        "cannot represent that verdict.",
    )
    _raise_if_row(
        connection,
        "SELECT id FROM approvals WHERE payload_schema_version IN "
        "('analysis-intent-v2', 'analysis-intent-v3') LIMIT 1",
        "Cannot downgrade while analysis-intent-v2 approvals or analysis-intent-v3 "
        "approvals exist; revision 0003 cannot preserve their immutable approval payload.",
    )
    _raise_if_row(
        connection,
        """
        SELECT runs.id
        FROM runs
        LEFT JOIN analysis_intents
          ON analysis_intents.id = runs.analysis_intent_id
        WHERE runs.analysis_intent_id IS NOT NULL
          AND (
              analysis_intents.id IS NULL
              OR analysis_intents.task_id != runs.task_id
              OR (
                  SELECT COUNT(*)
                  FROM analysis_intents AS task_intents
                  WHERE task_intents.task_id = runs.task_id
              ) != 1
          )
        LIMIT 1
        """,
        "Cannot downgrade a run whose analysis intent cannot be derived uniquely from "
        "its legacy task relationship.",
    )


def _v2_create_payload_sha256(goal: str, workflow_type: str) -> str:
    canonical = json.dumps(
        {
            "generationMode": "local-deterministic",
            "goal": goal,
            "remoteDataApproved": False,
            "workflowType": workflow_type,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _preflight_model_assisted_downgrade(connection: sa.Connection) -> None:
    _raise_if_row(
        connection,
        "SELECT id FROM workflows "
        "WHERE generation_mode = 'remote-model-assisted' LIMIT 1",
        "Cannot downgrade while remote-model-assisted workflows exist; revision 0002 "
        "cannot preserve their approval and provenance semantics.",
    )
    _raise_if_row(
        connection,
        """
        SELECT id
        FROM workflow_jobs
        WHERE handler_version IN (
              'research-plan-v2',
              'literature-synthesis-v2',
              'deterministic-claims-v2'
          )
        LIMIT 1
        """,
        "Cannot downgrade while v2 workflow job history exists; revision 0002 cannot "
        "preserve its event, retry, review, and result contracts.",
    )
    _raise_if_row(
        connection,
        "SELECT id FROM workflow_reviews "
        "WHERE review_type = 'deterministic-claims-v2' "
        "OR json_extract(result_json, '$.schemaVersion') = '2' LIMIT 1",
        "Cannot downgrade while v2 workflow reviews exist; revision 0002 cannot "
        "preserve their frozen result contracts.",
    )
    _raise_if_row(
        connection,
        "SELECT id FROM approvals WHERE payload_schema_version = "
        "'workflow-plan-approval-v2' LIMIT 1",
        "Cannot downgrade while v2 workflow approvals exist; revision 0002 cannot "
        "recompute their provenance-bound intent hashes.",
    )
    _raise_if_row(
        connection,
        "SELECT id FROM events WHERE event_type = 'workflow.created' "
        "AND json_type(payload, '$.generationMode') IS NOT NULL LIMIT 1",
        "Cannot downgrade while v2 workflow creation events exist; revision 0002 "
        "cannot parse their generation-mode provenance.",
    )
    _raise_if_row(
        connection,
        "SELECT id FROM events WHERE event_type = 'approval.requested' AND ("
        "json_type(payload, '$.riskLevel') IS NOT NULL OR "
        "json_type(payload, '$.reason') IS NOT NULL OR "
        "json_type(payload, '$.affectedResources') IS NOT NULL OR "
        "json_type(payload, '$.approvalSchemaVersion') IS NOT NULL"
        ") LIMIT 1",
        "Cannot downgrade while v2 workflow approval events exist; revision 0002 "
        "cannot parse their consent metadata.",
    )
    _raise_if_row(
        connection,
        "SELECT id FROM workflow_plans "
        "WHERE json_type(spec_json, '$.steps[0].inputs.frozenSources') IS NOT NULL "
        "OR json_type(spec_json, '$.steps[0].inputs.sourceIds') IS NOT NULL LIMIT 1",
        "Cannot downgrade while v2 workflow plans exist; revision 0002 cannot parse "
        "their frozen source descriptors.",
    )
    workflows = connection.execute(
        sa.text("SELECT id, goal, workflow_type, create_payload_sha256 FROM workflows")
    ).mappings()
    for workflow in workflows:
        expected_hash = _v2_create_payload_sha256(
            str(workflow["goal"]), str(workflow["workflow_type"])
        )
        if workflow["create_payload_sha256"] != expected_hash:
            raise RuntimeError(
                "Cannot downgrade workflow create hashes because an existing v2 payload "
                "hash does not match its canonical local workflow payload."
            )


def _downstream_downgrade_preflight(connection: sa.Connection) -> None:
    target_revision = context.get_revision_argument()
    if target_revision not in {"0005_workflow_mutation_replay", "-1"}:
        _preflight_mutation_binding_downgrade(connection)
    if target_revision not in {
        "0005_workflow_mutation_replay",
        "0004_dataset_analysis_workflows",
        "-1",
    }:
        _preflight_dataset_downgrade(connection)
    if target_revision not in {
        "0005_workflow_mutation_replay",
        "0004_dataset_analysis_workflows",
        "0003_model_assisted_workflows",
        "-1",
    }:
        _preflight_model_assisted_downgrade(connection)


def downgrade() -> None:
    connection = op.get_bind()
    _preflight_autonomous_downgrade(connection)
    _downstream_downgrade_preflight(connection)

    with op.batch_alter_table("workflow_jobs", recreate="always") as batch:
        batch.drop_constraint("ck_workflow_job_kind", type_="check")
        batch.create_check_constraint(
            "ck_workflow_job_kind",
            "kind IN ('generate-plan','execute-task','review-workflow')",
        )

    op.drop_index("ix_workflows_current_intent_decision_id", table_name="workflows")
    with op.batch_alter_table("workflows", recreate="always") as batch:
        batch.drop_constraint("ck_workflow_dataset_identity", type_="check")
        batch.drop_constraint("ck_workflow_selected_source_ids", type_="check")
        batch.drop_constraint("ck_workflow_intake_state", type_="check")
        batch.drop_constraint("ck_workflow_type", type_="check")
        batch.drop_constraint("ck_workflow_creation_mode", type_="check")
        batch.drop_constraint("ck_workflow_status", type_="check")
        batch.drop_constraint(
            "fk_workflows_current_intent_decision", type_="foreignkey"
        )
        batch.alter_column(
            "workflow_type",
            existing_type=sa.String(length=64),
            nullable=False,
            existing_server_default="literature-synthesis",
        )
        batch.drop_column("current_intent_decision_id")
        batch.drop_column("selected_source_ids")
        batch.drop_column("creation_mode")
        batch.create_check_constraint(
            "ck_workflow_status",
            "status IN ('planning','waiting-plan-approval','running','reviewing',"
            "'completed','blocked','failed','cancelled')",
        )
        batch.create_check_constraint(
            "ck_workflow_type",
            "workflow_type IN ('literature-synthesis','dataset-analysis')",
        )
        batch.create_check_constraint(
            "ck_workflow_dataset_identity",
            "(workflow_type = 'dataset-analysis' "
            "AND dataset_source_id IS NOT NULL "
            "AND dataset_content_hash IS NOT NULL) OR "
            "(workflow_type != 'dataset-analysis' "
            "AND dataset_source_id IS NULL "
            "AND dataset_content_hash IS NULL)",
        )

    op.drop_index("ix_user_responses_response_sha256", table_name="user_responses")
    op.drop_index("ix_user_responses_interaction_id", table_name="user_responses")
    op.drop_table("user_responses")

    op.drop_index(
        "ix_interaction_requests_request_sha256", table_name="interaction_requests"
    )
    op.drop_index("ix_interaction_requests_status", table_name="interaction_requests")
    op.drop_index(
        "ix_interaction_requests_workflow_id", table_name="interaction_requests"
    )
    op.drop_table("interaction_requests")

    op.drop_index("ix_intent_decisions_output_sha256", table_name="intent_decisions")
    op.drop_index("ix_intent_decisions_input_sha256", table_name="intent_decisions")
    op.drop_index(
        "ix_intent_decisions_model_invocation_id", table_name="intent_decisions"
    )
    op.drop_index("ix_intent_decisions_intent", table_name="intent_decisions")
    op.drop_index("ix_intent_decisions_workflow_id", table_name="intent_decisions")
    op.drop_table("intent_decisions")

    op.drop_index(
        "ix_model_invocations_output_sha256", table_name="model_invocations"
    )
    op.drop_index("ix_model_invocations_input_sha256", table_name="model_invocations")
    op.drop_index("ix_model_invocations_operation_type", table_name="model_invocations")
    op.drop_index("ix_model_invocations_workflow_id", table_name="model_invocations")
    op.drop_table("model_invocations")
