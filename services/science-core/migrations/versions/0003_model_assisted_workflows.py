"""Add explicit model-assisted workflow mode and answer provenance.

Revision ID: 0003_model_assisted_workflows
Revises: 0002_workflow_control_plane
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision: str = "0003_model_assisted_workflows"
down_revision: str | Sequence[str] | None = "0002_workflow_control_plane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_payload_sha256(goal: str, workflow_type: str, *, v2: bool) -> str:
    payload: dict[str, object] = {
        "goal": goal,
        "workflowType": workflow_type,
    }
    if v2:
        payload.update(
            {
                "generationMode": "local-deterministic",
                "remoteDataApproved": False,
            }
        )
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def upgrade() -> None:
    connection = op.get_bind()
    workflows = list(
        connection.execute(
            sa.text(
                "SELECT id, goal, workflow_type, create_payload_sha256 FROM workflows"
            )
        ).mappings()
    )
    for workflow in workflows:
        legacy_hash = _create_payload_sha256(
            str(workflow["goal"]),
            str(workflow["workflow_type"]),
            v2=False,
        )
        if workflow["create_payload_sha256"] != legacy_hash:
            raise RuntimeError(
                "Cannot upgrade workflow create hashes because an existing v1 payload hash "
                "does not match its canonical goal and workflow type."
            )

    with op.batch_alter_table("workflows", recreate="always") as batch:
        batch.add_column(
            sa.Column(
                "generation_mode",
                sa.String(length=32),
                nullable=False,
                server_default="local-deterministic",
            )
        )
        batch.create_check_constraint(
            "ck_workflow_generation_mode",
            "generation_mode IN ('local-deterministic','remote-model-assisted')",
        )

    for workflow in workflows:
        connection.execute(
            sa.text(
                "UPDATE workflows SET create_payload_sha256 = :payload_hash WHERE id = :id"
            ),
            {
                "id": workflow["id"],
                "payload_hash": _create_payload_sha256(
                    str(workflow["goal"]),
                    str(workflow["workflow_type"]),
                    v2=True,
                ),
            },
        )

    with op.batch_alter_table("answers", recreate="always") as batch:
        batch.add_column(
            sa.Column(
                "generator",
                sa.String(length=100),
                nullable=False,
                server_default="legacy-unknown",
            )
        )
        batch.add_column(sa.Column("model", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("prompt_version", sa.String(length=100), nullable=True))
        batch.add_column(
            sa.Column(
                "metadata_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )

    connection.execute(
        sa.text(
            "UPDATE answers SET generator = 'local-extractive-v1' "
            "WHERE workflow_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    remote_workflow = connection.execute(
        sa.text(
            "SELECT id FROM workflows "
            "WHERE generation_mode = 'remote-model-assisted' LIMIT 1"
        )
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
    v2_approval = connection.execute(
        sa.text(
            "SELECT id FROM approvals WHERE payload_schema_version = "
            "'workflow-plan-approval-v2' LIMIT 1"
        )
    ).first()
    if v2_approval is not None:
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
    workflows = list(
        connection.execute(
            sa.text(
                "SELECT id, goal, workflow_type, create_payload_sha256 FROM workflows"
            )
        ).mappings()
    )
    for workflow in workflows:
        current_hash = _create_payload_sha256(
            str(workflow["goal"]),
            str(workflow["workflow_type"]),
            v2=True,
        )
        if workflow["create_payload_sha256"] != current_hash:
            raise RuntimeError(
                "Cannot downgrade workflow create hashes because an existing v2 payload "
                "hash does not match its canonical local workflow payload."
            )
    for workflow in workflows:
        connection.execute(
            sa.text(
                "UPDATE workflows SET create_payload_sha256 = :payload_hash WHERE id = :id"
            ),
            {
                "id": workflow["id"],
                "payload_hash": _create_payload_sha256(
                    str(workflow["goal"]),
                    str(workflow["workflow_type"]),
                    v2=False,
                ),
            },
        )

    with op.batch_alter_table("answers", recreate="always") as batch:
        batch.drop_column("metadata_json")
        batch.drop_column("prompt_version")
        batch.drop_column("model")
        batch.drop_column("generator")

    with op.batch_alter_table("workflows", recreate="always") as batch:
        batch.drop_constraint("ck_workflow_generation_mode", type_="check")
        batch.drop_column("generation_mode")
