from __future__ import annotations

import difflib
import hashlib
import json
from collections.abc import Sequence
from typing import Any

from ..fixed_analysis_policy import AnalysisPolicyId, FixedAnalysisTemplate
from .errors import (
    ANALYSIS_ACTION,
    ANALYSIS_RISK_LEVEL,
    ANALYSIS_V2_SCHEMA,
    ANALYSIS_V3_SCHEMA,
)


def canonical_workflow_analysis_payload(
    *,
    project_id: str,
    workflow_id: str,
    plan_id: str,
    task_id: str,
    analysis_intent_id: str,
    plan_step_id: str,
    dataset_source_id: str,
    dataset_content_hash: str,
    objective: str,
    expected_outputs: Sequence[str],
    timeout_seconds: int,
    code: str,
    code_diff: str | None,
    error_summary: dict[str, Any] | None,
    previous_intent_id: str | None,
    repair_attempt: int,
    expected_workflow_revision: int,
    schema_version: str = ANALYSIS_V2_SCHEMA,
    policy_profile_id: AnalysisPolicyId | None = None,
    policy_template: FixedAnalysisTemplate | None = None,
) -> tuple[bytes, str]:
    """Return one immutable workflow approval payload and its SHA-256 digest."""

    if schema_version == ANALYSIS_V2_SCHEMA:
        if policy_profile_id is not None or policy_template is not None:
            raise ValueError("analysis-intent-v2 does not bind an execution policy")
    elif schema_version == ANALYSIS_V3_SCHEMA:
        if policy_profile_id is None or policy_template is None:
            raise ValueError("analysis-intent-v3 requires an execution policy binding")
    else:
        raise ValueError("unsupported workflow analysis approval schema")

    payload = {
        "action": ANALYSIS_ACTION,
        "analysisIntentId": analysis_intent_id,
        "code": code,
        "codeDiff": code_diff,
        "datasetContentHash": dataset_content_hash,
        "datasetSourceId": dataset_source_id,
        "errorSummary": error_summary,
        "expectedOutputs": list(expected_outputs),
        "expectedWorkflowRevision": expected_workflow_revision,
        "objective": objective,
        "planStepId": plan_step_id,
        "planId": plan_id,
        "previousIntentId": previous_intent_id,
        "projectId": project_id,
        "repairAttempt": repair_attempt,
        "riskLevel": ANALYSIS_RISK_LEVEL,
        "schemaVersion": schema_version,
        "taskId": task_id,
        "timeoutSeconds": timeout_seconds,
        "workflowId": workflow_id,
    }
    if schema_version == ANALYSIS_V3_SCHEMA:
        payload.update(
            {
                "policyProfileId": policy_profile_id,
                "policyTemplate": policy_template,
            }
        )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return encoded, hashlib.sha256(encoded).hexdigest()


def analysis_code_diff(previous_code: str, proposed_code: str) -> str:
    """Return the one canonical human-reviewable old-to-new code diff."""

    return "\n".join(
        difflib.unified_diff(
            previous_code.splitlines(),
            proposed_code.splitlines(),
            fromfile="approved.py",
            tofile="proposed.py",
            lineterm="",
        )
    )
