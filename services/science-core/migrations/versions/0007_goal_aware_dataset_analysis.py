"""Add goal-aware dataset analysis provenance foundations.

Revision ID: 0007_goal_aware_dataset_analysis
Revises: 0006_autonomous_agent_intake
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib import import_module

from alembic import context, op
import sqlalchemy as sa


revision: str = "0007_goal_aware_dataset_analysis"
down_revision: str | Sequence[str] | None = "0006_autonomous_agent_intake"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_prior_revision = import_module("migrations.versions.0006_autonomous_agent_intake")

_GOAL_AWARE_EVENT_TYPES = (
    "analysis.method-selection-started",
    "analysis.clarification-requested",
    "analysis.spec-created",
    "analysis.spec-superseded",
    "analysis.spec-approved",
    "analysis.compiled",
    "analysis.execution-approval-requested",
    "analysis.execution-started",
    "analysis.structured-result-created",
    "analysis.review-completed",
    "analysis.unsupported",
)


def upgrade() -> None:
    # Early 0006 development databases did not materialize the redundant
    # UNIQUE(workflow_id, id) constraint because id is already the primary key.
    # The explicit index makes the composite provenance FK valid for both those
    # databases and fresh 0006 schemas.
    op.create_index(
        "uq_model_invocations_workflow_id_id_compat",
        "model_invocations",
        ["workflow_id", "id"],
        unique=True,
    )
    op.create_table(
        "analysis_specs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("previous_spec_id", sa.String(length=36), nullable=True),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("selector_kind", sa.String(length=32), nullable=False),
        sa.Column("selector_reason", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("model_invocation_id", sa.String(length=36), nullable=True),
        sa.Column("dataset_source_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_content_hash", sa.String(length=64), nullable=False),
        sa.Column("dataset_profile_sha256", sa.String(length=64), nullable=False),
        sa.Column("spec_json", sa.JSON(), nullable=False),
        sa.Column("spec_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_analysis_spec_revision"),
        sa.CheckConstraint(
            "schema_version = '1'", name="ck_analysis_spec_schema_version"
        ),
        sa.CheckConstraint(
            "selector_kind IN ('local-deterministic','remote-model-assisted')",
            name="ck_analysis_spec_selector_kind",
        ),
        sa.CheckConstraint(
            "length(trim(selector_reason)) BETWEEN 1 AND 2000",
            name="ck_analysis_spec_selector_reason",
        ),
        sa.CheckConstraint(
            "(selector_kind = 'local-deterministic' "
            "AND model_invocation_id IS NULL) OR "
            "(selector_kind = 'remote-model-assisted' "
            "AND model_invocation_id IS NOT NULL "
            "AND prompt_version IS NOT NULL "
            "AND length(trim(prompt_version)) > 0)",
            name="ck_analysis_spec_selector_provenance",
        ),
        sa.CheckConstraint(
            "(revision = 1 AND previous_spec_id IS NULL) OR "
            "(revision > 1 AND previous_spec_id IS NOT NULL)",
            name="ck_analysis_spec_revision_lineage",
        ),
        sa.CheckConstraint(
            "status IN ('pending-approval','approved','superseded','rejected')",
            name="ck_analysis_spec_status",
        ),
        sa.CheckConstraint(
            "length(dataset_content_hash) = 64 "
            "AND dataset_content_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_analysis_spec_dataset_content_hash",
        ),
        sa.CheckConstraint(
            "length(dataset_profile_sha256) = 64 "
            "AND dataset_profile_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_analysis_spec_dataset_profile_sha256",
        ),
        sa.CheckConstraint(
            "json_valid(spec_json) AND json_type(spec_json) = 'object'",
            name="ck_analysis_spec_json",
        ),
        sa.CheckConstraint(
            "length(spec_sha256) = 64 "
            "AND spec_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_analysis_spec_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["dataset_source_id"], ["sources.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "previous_spec_id"],
            ["analysis_specs.workflow_id", "analysis_specs.id"],
            name="fk_analysis_specs_previous_spec",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "model_invocation_id"],
            ["model_invocations.workflow_id", "model_invocations.id"],
            name="fk_analysis_specs_model_invocation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_id", "id", name="uq_analysis_spec_workflow_id"
        ),
        sa.UniqueConstraint(
            "workflow_id", "revision", name="uq_analysis_spec_workflow_revision"
        ),
    )
    op.create_index(
        "ix_analysis_specs_dataset_source_id",
        "analysis_specs",
        ["dataset_source_id"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_specs_dataset_profile_sha256",
        "analysis_specs",
        ["dataset_profile_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_specs_spec_sha256",
        "analysis_specs",
        ["spec_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_specs_workflow_status",
        "analysis_specs",
        ["workflow_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_analysis_spec_model_invocation",
        "analysis_specs",
        ["model_invocation_id"],
        unique=True,
        sqlite_where=sa.text("model_invocation_id IS NOT NULL"),
    )

    with op.batch_alter_table("analysis_intents", recreate="always") as batch:
        batch.add_column(
            sa.Column("analysis_spec_id", sa.String(length=36), nullable=True)
        )
        batch.add_column(sa.Column("spec_sha256", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("dataset_profile_sha256", sa.String(length=64), nullable=True)
        )
        batch.add_column(
            sa.Column("compiler_version", sa.String(length=100), nullable=True)
        )
        batch.add_column(sa.Column("code_sha256", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("runtime_policy_id", sa.String(length=100), nullable=True)
        )
        batch.create_foreign_key(
            "fk_analysis_intents_analysis_spec_id",
            "analysis_specs",
            ["analysis_spec_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_analysis_intent_spec_sha256",
            "spec_sha256 IS NULL OR "
            "(length(spec_sha256) = 64 "
            "AND spec_sha256 NOT GLOB '*[^0-9a-f]*')",
        )
        batch.create_check_constraint(
            "ck_analysis_intent_dataset_profile_sha256",
            "dataset_profile_sha256 IS NULL OR "
            "(length(dataset_profile_sha256) = 64 "
            "AND dataset_profile_sha256 NOT GLOB '*[^0-9a-f]*')",
        )
        batch.create_check_constraint(
            "ck_analysis_intent_code_sha256",
            "code_sha256 IS NULL OR "
            "(length(code_sha256) = 64 "
            "AND code_sha256 NOT GLOB '*[^0-9a-f]*')",
        )
        batch.create_check_constraint(
            "ck_analysis_intent_compiled_provenance",
            "(analysis_spec_id IS NULL "
            "AND spec_sha256 IS NULL "
            "AND dataset_profile_sha256 IS NULL "
            "AND compiler_version IS NULL "
            "AND code_sha256 IS NULL "
            "AND runtime_policy_id IS NULL) OR "
            "(analysis_spec_id IS NOT NULL "
            "AND spec_sha256 IS NOT NULL "
            "AND dataset_profile_sha256 IS NOT NULL "
            "AND compiler_version IS NOT NULL "
            "AND length(trim(compiler_version)) > 0 "
            "AND code_sha256 IS NOT NULL "
            "AND runtime_policy_id IS NOT NULL "
            "AND length(trim(runtime_policy_id)) > 0)",
        )
    op.create_index(
        "ix_analysis_intents_analysis_spec_id",
        "analysis_intents",
        ["analysis_spec_id"],
        unique=False,
    )

    op.create_table(
        "structured_analysis_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("analysis_spec_id", sa.String(length=36), nullable=False),
        sa.Column("analysis_intent_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("result_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "schema_version = '1'", name="ck_structured_result_schema_version"
        ),
        sa.CheckConstraint(
            "json_valid(result_json) AND json_type(result_json) = 'object'",
            name="ck_structured_result_json",
        ),
        sa.CheckConstraint(
            "length(result_sha256) = 64 "
            "AND result_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_structured_result_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_spec_id"], ["analysis_specs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["analysis_intent_id"], ["analysis_intents.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_spec_id", name="uq_structured_result_analysis_spec"
        ),
        sa.UniqueConstraint(
            "analysis_intent_id", name="uq_structured_result_analysis_intent"
        ),
        sa.UniqueConstraint("run_id", name="uq_structured_result_run"),
    )
    op.create_index(
        "ix_structured_analysis_results_result_sha256",
        "structured_analysis_results",
        ["result_sha256"],
        unique=False,
    )


def _raise_if_new_provenance_exists(connection: sa.Connection) -> None:
    event_placeholders = ",".join(
        f":event_type_{index}" for index in range(len(_GOAL_AWARE_EVENT_TYPES))
    )
    event = connection.execute(
        sa.text(
            "SELECT id FROM events WHERE event_type IN "
            f"({event_placeholders}) LIMIT 1"
        ),
        {
            f"event_type_{index}": event_type
            for index, event_type in enumerate(_GOAL_AWARE_EVENT_TYPES)
        },
    ).first()
    if event is not None:
        raise RuntimeError(
            "Cannot downgrade while goal-aware analysis workflow events exist; "
            "revision 0006 cannot validate their strict payload contracts."
        )

    spec = connection.execute(sa.text("SELECT id FROM analysis_specs LIMIT 1")).first()
    if spec is not None:
        raise RuntimeError(
            "Cannot downgrade while goal-aware analysis spec provenance exists; "
            "revision 0006 cannot preserve it."
        )

    result = connection.execute(
        sa.text("SELECT id FROM structured_analysis_results LIMIT 1")
    ).first()
    if result is not None:
        raise RuntimeError(
            "Cannot downgrade while structured analysis result provenance exists; "
            "revision 0006 cannot preserve it."
        )

    intent = connection.execute(
        sa.text(
            "SELECT id FROM analysis_intents WHERE "
            "analysis_spec_id IS NOT NULL OR spec_sha256 IS NOT NULL OR "
            "dataset_profile_sha256 IS NOT NULL OR compiler_version IS NOT NULL OR "
            "code_sha256 IS NOT NULL OR runtime_policy_id IS NOT NULL LIMIT 1"
        )
    ).first()
    if intent is not None:
        raise RuntimeError(
            "Cannot downgrade while compiled analysis intent provenance exists; "
            "revision 0006 cannot preserve it."
        )


def downgrade() -> None:
    connection = op.get_bind()
    _raise_if_new_provenance_exists(connection)
    target_revision = context.get_revision_argument()
    if target_revision not in {down_revision, "-1"}:
        _prior_revision._preflight_autonomous_downgrade(connection)
        _prior_revision._downstream_downgrade_preflight(connection)

    op.drop_index(
        "ix_structured_analysis_results_result_sha256",
        table_name="structured_analysis_results",
    )
    op.drop_table("structured_analysis_results")

    op.drop_index("ix_analysis_intents_analysis_spec_id", table_name="analysis_intents")
    with op.batch_alter_table("analysis_intents", recreate="always") as batch:
        batch.drop_constraint(
            "ck_analysis_intent_compiled_provenance", type_="check"
        )
        batch.drop_constraint("ck_analysis_intent_code_sha256", type_="check")
        batch.drop_constraint(
            "ck_analysis_intent_dataset_profile_sha256", type_="check"
        )
        batch.drop_constraint("ck_analysis_intent_spec_sha256", type_="check")
        batch.drop_constraint(
            "fk_analysis_intents_analysis_spec_id", type_="foreignkey"
        )
        batch.drop_column("runtime_policy_id")
        batch.drop_column("code_sha256")
        batch.drop_column("compiler_version")
        batch.drop_column("dataset_profile_sha256")
        batch.drop_column("spec_sha256")
        batch.drop_column("analysis_spec_id")

    op.drop_index("uq_analysis_spec_model_invocation", table_name="analysis_specs")
    op.drop_index("ix_analysis_specs_workflow_status", table_name="analysis_specs")
    op.drop_index("ix_analysis_specs_spec_sha256", table_name="analysis_specs")
    op.drop_index(
        "ix_analysis_specs_dataset_profile_sha256", table_name="analysis_specs"
    )
    op.drop_index("ix_analysis_specs_dataset_source_id", table_name="analysis_specs")
    op.drop_table("analysis_specs")
    op.drop_index(
        "uq_model_invocations_workflow_id_id_compat",
        table_name="model_invocations",
    )
