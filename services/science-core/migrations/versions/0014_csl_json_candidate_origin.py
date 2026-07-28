"""Allow local CSL-JSON as a candidate origin, never as a remote provider.

Revision ID: 0014_csl_json_candidate_origin
Revises: 0013_research_memory_context
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0014_csl_json_candidate_origin"
down_revision: str | Sequence[str] | None = "0013_research_memory_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REMOTE = "provider IN ('arxiv','crossref','openalex','pubmed')"
_CANDIDATE_ORIGIN = (
    "provider IN ('arxiv','crossref','openalex','pubmed','csl-json-file')"
)


def _replace_provider_checks(expression: str) -> None:
    with op.batch_alter_table("tool_invocations", recreate="always") as batch:
        batch.drop_constraint("ck_tool_invocation_provider", type_="check")
        batch.create_check_constraint("ck_tool_invocation_provider", expression)
    with op.batch_alter_table("discovery_candidates", recreate="always") as batch:
        batch.drop_constraint("ck_discovery_candidate_provider", type_="check")
        batch.create_check_constraint("ck_discovery_candidate_provider", expression)


def upgrade() -> None:
    _replace_provider_checks(_CANDIDATE_ORIGIN)


def downgrade() -> None:
    connection = op.get_bind()
    for table in (
        "discovery_specs",
        "tool_invocations",
        "discovery_candidates",
        "discovery_candidate_occurrences",
    ):
        if connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("Cannot downgrade while discovery provenance exists.")
    _replace_provider_checks(_REMOTE)
