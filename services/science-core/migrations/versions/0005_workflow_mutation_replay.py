"""Persist exact workflow mutation idempotency bindings.

Revision ID: 0005_workflow_mutation_replay
Revises: 0004_dataset_analysis_workflows
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json

from alembic import context, op
import sqlalchemy as sa


revision: str = "0005_workflow_mutation_replay"
down_revision: str | Sequence[str] | None = "0004_dataset_analysis_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_jobs",
        sa.Column("request_payload_sha256", sa.String(length=64), nullable=True),
    )


def _raise_if_row(
    connection: sa.Connection,
    query: str,
    message: str,
) -> None:
    if connection.execute(sa.text(query)).first() is not None:
        raise RuntimeError(message)


def _preflight_dataset_downgrade(connection: sa.Connection) -> None:
    _raise_if_row(
        connection,
        "SELECT id FROM workflows WHERE workflow_type = 'dataset-analysis' "
        "OR dataset_source_id IS NOT NULL OR dataset_content_hash IS NOT NULL LIMIT 1",
        "Cannot downgrade while dataset-analysis workflow provenance exists; "
        "revision 0003 cannot preserve it.",
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
        "SELECT id FROM approvals "
        "WHERE payload_schema_version IN "
        "('analysis-intent-v2', 'analysis-intent-v3') LIMIT 1",
        "Cannot downgrade while analysis-intent-v2 approvals or "
        "analysis-intent-v3 approvals exist; revision 0003 cannot preserve "
        "their immutable approval payload.",
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
        sa.text(
            "SELECT id, goal, workflow_type, create_payload_sha256 FROM workflows"
        )
    ).mappings()
    for workflow in workflows:
        expected_hash = _v2_create_payload_sha256(
            str(workflow["goal"]),
            str(workflow["workflow_type"]),
        )
        if workflow["create_payload_sha256"] != expected_hash:
            raise RuntimeError(
                "Cannot downgrade workflow create hashes because an existing v2 payload "
                "hash does not match its canonical local workflow payload."
            )


def downgrade() -> None:
    connection = op.get_bind()
    bound_request = connection.execute(
        sa.text(
            "SELECT id FROM workflow_jobs "
            "WHERE request_payload_sha256 IS NOT NULL LIMIT 1"
        )
    ).first()
    if bound_request is not None:
        raise RuntimeError(
            "Cannot downgrade while workflow mutation idempotency bindings exist; "
            "the prior schema cannot preserve exact retry or resume replay semantics."
        )
    target_revision = context.get_revision_argument()
    if target_revision not in {"0004_dataset_analysis_workflows", "-1"}:
        _preflight_dataset_downgrade(connection)
    if target_revision not in {
        "0004_dataset_analysis_workflows",
        "0003_model_assisted_workflows",
        "-1",
    }:
        _preflight_model_assisted_downgrade(connection)
    op.drop_column("workflow_jobs", "request_payload_sha256")
