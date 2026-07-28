from __future__ import annotations

import difflib
import hashlib
import json
from collections.abc import Sequence
from typing import Any

from ..fixed_analysis_policy import (
    COMPILED_ANALYSIS_POLICY_ID,
    COMPILED_ANALYSIS_TEMPLATE,
    AnalysisPolicyId,
    AnalysisPolicyTemplate,
)
from .errors import (
    ANALYSIS_ACTION,
    ANALYSIS_RISK_LEVEL,
    ANALYSIS_V2_SCHEMA,
    ANALYSIS_V3_SCHEMA,
    ANALYSIS_V4_SCHEMA,
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
    policy_template: AnalysisPolicyTemplate | None = None,
    analysis_spec_id: str | None = None,
    analysis_spec_sha256: str | None = None,
    dataset_profile_sha256: str | None = None,
    compiler_version: str | None = None,
    code_sha256: str | None = None,
    runtime_policy_id: AnalysisPolicyId | None = None,
) -> tuple[bytes, str]:
    """Return one immutable workflow approval payload and its SHA-256 digest."""

    compiled_fields = (
        analysis_spec_id,
        analysis_spec_sha256,
        dataset_profile_sha256,
        compiler_version,
        code_sha256,
        runtime_policy_id,
    )
    if schema_version == ANALYSIS_V2_SCHEMA:
        if policy_profile_id is not None or policy_template is not None:
            raise ValueError("analysis-intent-v2 does not bind an execution policy")
        if any(value is not None for value in compiled_fields):
            raise ValueError("analysis-intent-v2 does not bind compiled provenance")
    elif schema_version == ANALYSIS_V3_SCHEMA:
        if policy_profile_id is None or policy_template is None:
            raise ValueError("analysis-intent-v3 requires an execution policy binding")
        if any(value is not None for value in compiled_fields):
            raise ValueError("analysis-intent-v3 does not bind compiled provenance")
    elif schema_version == ANALYSIS_V4_SCHEMA:
        compiled_hashes = (
            analysis_spec_sha256,
            dataset_profile_sha256,
            code_sha256,
        )
        if (
            any(value is None for value in compiled_fields)
            or analysis_spec_id is None
            or not analysis_spec_id.strip()
            or len(analysis_spec_id) > 36
            or any(not _is_sha256(value) for value in compiled_hashes)
            or policy_profile_id != COMPILED_ANALYSIS_POLICY_ID
            or runtime_policy_id != COMPILED_ANALYSIS_POLICY_ID
            or policy_template != COMPILED_ANALYSIS_TEMPLATE
            or compiler_version != COMPILED_ANALYSIS_TEMPLATE
            or repair_attempt != 0
            or previous_intent_id is not None
            or code_diff is not None
            or error_summary is not None
            or hashlib.sha256(code.encode("utf-8")).hexdigest() != code_sha256
        ):
            raise ValueError("analysis-intent-v4 compiled provenance is invalid")
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
    if schema_version in {ANALYSIS_V3_SCHEMA, ANALYSIS_V4_SCHEMA}:
        payload.update(
            {
                "policyProfileId": policy_profile_id,
                "policyTemplate": policy_template,
            }
        )
    if schema_version == ANALYSIS_V4_SCHEMA:
        payload.update(
            {
                "analysisSpecId": analysis_spec_id,
                "analysisSpecSha256": analysis_spec_sha256,
                "codeSha256": code_sha256,
                "compilerVersion": compiler_version,
                "datasetProfileSha256": dataset_profile_sha256,
                "runtimePolicyId": runtime_policy_id,
            }
        )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return encoded, hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: str | None) -> bool:
    return (
        value is not None
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


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
