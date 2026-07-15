"""Add dataset-analysis workflow provenance and repair lineage.

Revision ID: 0004_dataset_analysis_workflows
Revises: 0003_model_assisted_workflows
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json

from alembic import context, op
import sqlalchemy as sa


revision: str = "0004_dataset_analysis_workflows"
down_revision: str | Sequence[str] | None = "0003_model_assisted_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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


def _upgrade_preflight(connection: sa.Connection) -> None:
    unknown_workflow = connection.execute(
        sa.text(
            "SELECT id FROM workflows "
            "WHERE workflow_type NOT IN ('literature-synthesis','dataset-analysis') LIMIT 1"
        )
    ).first()
    if unknown_workflow is not None:
        raise RuntimeError(
            "Cannot upgrade a workflow with an unknown workflow_type; revision 0004 "
            "requires an explicitly registered workflow contract."
        )

    unsupported_workflow = connection.execute(
        sa.text("SELECT id FROM workflows WHERE workflow_type = 'dataset-analysis' LIMIT 1")
    ).first()
    if unsupported_workflow is not None:
        raise RuntimeError(
            "Cannot upgrade a pre-0004 dataset-analysis workflow because its immutable "
            "dataset identity cannot be inferred safely."
        )

    ambiguous_run = connection.execute(
        sa.text(
            """
            SELECT runs.task_id
            FROM runs
            JOIN analysis_intents ON analysis_intents.task_id = runs.task_id
            GROUP BY runs.task_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if ambiguous_run is not None:
        raise RuntimeError(
            "Cannot upgrade legacy runs when multiple runs share one analysis intent; "
            "their intent provenance cannot be inferred safely."
        )


def upgrade() -> None:
    connection = op.get_bind()
    _upgrade_preflight(connection)

    with op.batch_alter_table("workflows", recreate="always") as batch:
        batch.add_column(sa.Column("dataset_source_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("dataset_content_hash", sa.String(length=64), nullable=True))
        batch.create_foreign_key(
            "fk_workflows_dataset_source_id",
            "sources",
            ["dataset_source_id"],
            ["id"],
            ondelete="RESTRICT",
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
        batch.create_check_constraint(
            "ck_workflow_type",
            "workflow_type IN ('literature-synthesis','dataset-analysis')",
        )
        batch.create_check_constraint(
            "ck_workflow_dataset_content_hash",
            "dataset_content_hash IS NULL OR "
            "(length(dataset_content_hash) = 64 "
            "AND dataset_content_hash NOT GLOB '*[^0-9a-f]*')",
        )
    op.create_index(
        "ix_workflows_dataset_source_id",
        "workflows",
        ["dataset_source_id"],
        unique=False,
    )

    op.drop_index("ix_analysis_intents_task_id", table_name="analysis_intents")
    with op.batch_alter_table("analysis_intents", recreate="always") as batch:
        batch.add_column(sa.Column("workflow_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("plan_step_id", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("previous_intent_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("dataset_content_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("expected_outputs", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("timeout_seconds", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("risk_level", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("repair_attempt", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("error_summary", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("code_diff", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_analysis_intents_workflow_id",
            "workflows",
            ["workflow_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_analysis_intents_previous_intent_id",
            "analysis_intents",
            ["previous_intent_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_check_constraint(
            "ck_analysis_intent_repair_attempt",
            "repair_attempt IS NULL OR repair_attempt BETWEEN 0 AND 2",
        )
        batch.create_check_constraint(
            "ck_analysis_intent_risk_level",
            "risk_level IS NULL OR risk_level IN ('low','medium','high')",
        )
        batch.create_check_constraint(
            "ck_analysis_intent_dataset_content_hash",
            "dataset_content_hash IS NULL OR "
            "(length(dataset_content_hash) = 64 "
            "AND dataset_content_hash NOT GLOB '*[^0-9a-f]*')",
        )
        batch.create_check_constraint(
            "ck_analysis_intent_timeout_seconds",
            "timeout_seconds IS NULL OR timeout_seconds BETWEEN 1 AND 3600",
        )
        batch.create_check_constraint(
            "ck_analysis_intent_workflow_binding",
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
            "AND length(trim(code_diff)) > 0)))",
        )
    op.create_index(
        "ix_analysis_intents_task_id",
        "analysis_intents",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_intents_previous_intent_id",
        "analysis_intents",
        ["previous_intent_id"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_intents_workflow_step",
        "analysis_intents",
        ["workflow_id", "plan_step_id"],
        unique=False,
    )
    op.create_index(
        "uq_analysis_intent_active_task",
        "analysis_intents",
        ["task_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('waiting-approval','approved','executing')"),
    )

    with op.batch_alter_table("runs", recreate="always") as batch:
        batch.add_column(sa.Column("analysis_intent_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_runs_analysis_intent_id",
            "analysis_intents",
            ["analysis_intent_id"],
            ["id"],
            ondelete="CASCADE",
        )
    connection.execute(
        sa.text(
            """
            UPDATE runs
            SET analysis_intent_id = (
                SELECT analysis_intents.id
                FROM analysis_intents
                WHERE analysis_intents.task_id = runs.task_id
            )
            WHERE EXISTS (
                SELECT 1
                FROM analysis_intents
                WHERE analysis_intents.task_id = runs.task_id
            )
            """
        )
    )
    op.create_index(
        "uq_run_analysis_intent",
        "runs",
        ["analysis_intent_id"],
        unique=True,
        sqlite_where=sa.text("analysis_intent_id IS NOT NULL"),
    )

    with op.batch_alter_table("workflow_reviews", recreate="always") as batch:
        batch.drop_constraint("ck_workflow_review_verdict", type_="check")
        batch.create_check_constraint(
            "ck_workflow_review_verdict",
            "verdict IN ('passed','passed-with-warnings','revision-required','blocked','failed')",
        )


def _downgrade_preflight(connection: sa.Connection) -> None:
    dataset_workflow = connection.execute(
        sa.text(
            "SELECT id FROM workflows WHERE workflow_type = 'dataset-analysis' "
            "OR dataset_source_id IS NOT NULL OR dataset_content_hash IS NOT NULL LIMIT 1"
        )
    ).first()
    if dataset_workflow is not None:
        raise RuntimeError(
            "Cannot downgrade while dataset-analysis workflow provenance exists; "
            "revision 0003 cannot preserve it."
        )

    dataset_intent = connection.execute(
        sa.text(
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
            """
        )
    ).first()
    if dataset_intent is not None:
        raise RuntimeError(
            "Cannot downgrade while dataset-analysis intent provenance or repair lineage "
            "exists; revision 0003 cannot preserve it."
        )

    duplicate_intent = connection.execute(
        sa.text("SELECT task_id FROM analysis_intents GROUP BY task_id HAVING COUNT(*) > 1 LIMIT 1")
    ).first()
    if duplicate_intent is not None:
        raise RuntimeError(
            "Cannot downgrade while multiple analysis intents share a task; revision 0003 "
            "requires one intent per task."
        )

    warning_review = connection.execute(
        sa.text("SELECT id FROM workflow_reviews WHERE verdict = 'passed-with-warnings' LIMIT 1")
    ).first()
    if warning_review is not None:
        raise RuntimeError(
            "Cannot downgrade while passed-with-warnings reviews exist; revision 0003 "
            "cannot represent that verdict."
        )

    dataset_approval = connection.execute(
        sa.text(
            "SELECT id FROM approvals WHERE payload_schema_version IN "
            "('analysis-intent-v2', 'analysis-intent-v3') LIMIT 1"
        )
    ).first()
    if dataset_approval is not None:
        raise RuntimeError(
            "Cannot downgrade while analysis-intent-v2 approvals or "
            "analysis-intent-v3 approvals exist; revision 0003 cannot preserve "
            "their immutable approval payload."
        )

    unsafe_run = connection.execute(
        sa.text(
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
            """
        )
    ).first()
    if unsafe_run is not None:
        raise RuntimeError(
            "Cannot downgrade a run whose analysis intent cannot be derived uniquely from "
            "its legacy task relationship."
        )


def _downstream_downgrade_preflight(connection: sa.Connection) -> None:
    """Refuse before 0004 DDL when a lower target will fail in revision 0003."""
    if context.get_revision_argument() in {"0003_model_assisted_workflows", "-1"}:
        return

    remote_workflow = connection.execute(
        sa.text("SELECT id FROM workflows WHERE generation_mode = 'remote-model-assisted' LIMIT 1")
    ).first()
    if remote_workflow is not None:
        raise RuntimeError(
            "Cannot downgrade while remote-model-assisted workflows exist; revision 0002 "
            "cannot preserve their approval and provenance semantics."
        )

    v2_job = connection.execute(
        sa.text(
            """
            SELECT id
            FROM workflow_jobs
            WHERE handler_version IN (
                  'research-plan-v2',
                  'literature-synthesis-v2',
                  'deterministic-claims-v2'
              )
            LIMIT 1
            """
        )
    ).first()
    if v2_job is not None:
        raise RuntimeError(
            "Cannot downgrade while v2 workflow job history exists; revision 0002 cannot "
            "preserve its event, retry, review, and result contracts."
        )

    v2_review = connection.execute(
        sa.text(
            "SELECT id FROM workflow_reviews "
            "WHERE review_type = 'deterministic-claims-v2' "
            "OR json_extract(result_json, '$.schemaVersion') = '2' LIMIT 1"
        )
    ).first()
    if v2_review is not None:
        raise RuntimeError(
            "Cannot downgrade while v2 workflow reviews exist; revision 0002 cannot "
            "preserve their frozen result contracts."
        )

    v2_workflow_approval = connection.execute(
        sa.text(
            "SELECT id FROM approvals WHERE payload_schema_version = "
            "'workflow-plan-approval-v2' LIMIT 1"
        )
    ).first()
    if v2_workflow_approval is not None:
        raise RuntimeError(
            "Cannot downgrade while v2 workflow approvals exist; revision 0002 cannot "
            "recompute their provenance-bound intent hashes."
        )

    v2_created_event = connection.execute(
        sa.text(
            "SELECT id FROM events WHERE event_type = 'workflow.created' "
            "AND json_type(payload, '$.generationMode') IS NOT NULL LIMIT 1"
        )
    ).first()
    if v2_created_event is not None:
        raise RuntimeError(
            "Cannot downgrade while v2 workflow creation events exist; revision 0002 "
            "cannot parse their generation-mode provenance."
        )

    v2_approval_event = connection.execute(
        sa.text(
            "SELECT id FROM events WHERE event_type = 'approval.requested' AND ("
            "json_type(payload, '$.riskLevel') IS NOT NULL OR "
            "json_type(payload, '$.reason') IS NOT NULL OR "
            "json_type(payload, '$.affectedResources') IS NOT NULL OR "
            "json_type(payload, '$.approvalSchemaVersion') IS NOT NULL"
            ") LIMIT 1"
        )
    ).first()
    if v2_approval_event is not None:
        raise RuntimeError(
            "Cannot downgrade while v2 workflow approval events exist; revision 0002 "
            "cannot parse their consent metadata."
        )

    v2_plan = connection.execute(
        sa.text(
            "SELECT id FROM workflow_plans "
            "WHERE json_type(spec_json, '$.steps[0].inputs.frozenSources') "
            "IS NOT NULL OR json_type(spec_json, '$.steps[0].inputs.sourceIds') "
            "IS NOT NULL LIMIT 1"
        )
    ).first()
    if v2_plan is not None:
        raise RuntimeError(
            "Cannot downgrade while v2 workflow plans exist; revision 0002 cannot parse "
            "their frozen source descriptors."
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


def downgrade() -> None:
    connection = op.get_bind()
    _downgrade_preflight(connection)
    _downstream_downgrade_preflight(connection)

    with op.batch_alter_table("workflow_reviews", recreate="always") as batch:
        batch.drop_constraint("ck_workflow_review_verdict", type_="check")
        batch.create_check_constraint(
            "ck_workflow_review_verdict",
            "verdict IN ('passed','revision-required','blocked','failed')",
        )

    op.drop_index("uq_run_analysis_intent", table_name="runs")
    with op.batch_alter_table("runs", recreate="always") as batch:
        batch.drop_constraint("fk_runs_analysis_intent_id", type_="foreignkey")
        batch.drop_column("analysis_intent_id")

    op.drop_index("uq_analysis_intent_active_task", table_name="analysis_intents")
    op.drop_index("ix_analysis_intents_workflow_step", table_name="analysis_intents")
    op.drop_index("ix_analysis_intents_previous_intent_id", table_name="analysis_intents")
    op.drop_index("ix_analysis_intents_task_id", table_name="analysis_intents")
    with op.batch_alter_table("analysis_intents", recreate="always") as batch:
        batch.drop_constraint("ck_analysis_intent_workflow_binding", type_="check")
        batch.drop_constraint("ck_analysis_intent_timeout_seconds", type_="check")
        batch.drop_constraint("ck_analysis_intent_dataset_content_hash", type_="check")
        batch.drop_constraint("ck_analysis_intent_risk_level", type_="check")
        batch.drop_constraint("ck_analysis_intent_repair_attempt", type_="check")
        batch.drop_constraint("fk_analysis_intents_previous_intent_id", type_="foreignkey")
        batch.drop_constraint("fk_analysis_intents_workflow_id", type_="foreignkey")
        batch.drop_column("code_diff")
        batch.drop_column("error_summary")
        batch.drop_column("repair_attempt")
        batch.drop_column("risk_level")
        batch.drop_column("timeout_seconds")
        batch.drop_column("expected_outputs")
        batch.drop_column("dataset_content_hash")
        batch.drop_column("previous_intent_id")
        batch.drop_column("plan_step_id")
        batch.drop_column("workflow_id")
    op.create_index(
        "ix_analysis_intents_task_id",
        "analysis_intents",
        ["task_id"],
        unique=True,
    )

    op.drop_index("ix_workflows_dataset_source_id", table_name="workflows")
    with op.batch_alter_table("workflows", recreate="always") as batch:
        batch.drop_constraint("ck_workflow_dataset_content_hash", type_="check")
        batch.drop_constraint("ck_workflow_type", type_="check")
        batch.drop_constraint("ck_workflow_dataset_identity", type_="check")
        batch.drop_constraint("fk_workflows_dataset_source_id", type_="foreignkey")
        batch.drop_column("dataset_content_hash")
        batch.drop_column("dataset_source_id")
