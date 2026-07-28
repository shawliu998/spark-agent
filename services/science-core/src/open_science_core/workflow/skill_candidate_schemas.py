from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..schemas import to_camel

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class _SkillModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        from_attributes=True,
        populate_by_name=True,
        strict=True,
    )


class CreateSkillCandidateIn(_SkillModel):
    memory_id: str
    expected_memory_content_hash: Sha256
    episode_id: str | None = None
    expected_episode_sha256: Sha256 | None = None


class ActiveSkillCapabilityInvokeIn(_SkillModel):
    evidence_id: str
    expected_source_content_hash: Sha256
    expected_quote_hash: Sha256


class ActiveSkillCapabilityInvokeOut(_SkillModel):
    memory_candidate_id: str
    memory_content_hash: Sha256
    revision: Annotated[int, Field(strict=True, ge=1)]
    episode_id: str
    episode_hash: Sha256
    outcome: Literal[
        "candidate-created",
        "candidate-reopened",
        "already-remembered",
    ]


class ReplayResultOut(_SkillModel):
    name: Literal[
        "happy",
        "malformed",
        "tool-failure",
        "permission-denial",
        "prompt-injection",
        "restart-recovery",
    ]
    fixture_sha256: Sha256
    outcome: str
    passed: bool
    postcondition_sha256: Sha256
    result_sha256: Sha256


class SkillCandidateOut(_SkillModel):
    id: str
    project_id: str
    workflow_id: str
    schema_version: Literal["1"]
    name: Literal["remember-verified-evidence"]
    description: str
    scope: Literal["project"]
    trigger_json: dict[str, Any]
    inputs_json: dict[str, Any]
    preconditions_json: list[dict[str, Any]]
    allowed_tools_json: list[str]
    required_permissions_json: list[str]
    procedure_json: list[dict[str, Any]]
    postconditions_json: list[dict[str, Any]]
    failure_policy_json: dict[str, Any]
    provenance_requirements_json: list[str]
    origin_trace_ids: list[str]
    sanitized_source_hash: Sha256
    parent_skill_id: None
    version: Annotated[int, Field(strict=True, ge=1)]
    content_hash: Sha256
    status: Literal["failed-validation", "awaiting-approval"]
    generated_skill_md: str
    evaluation_json: dict[str, Any]
    created_at: datetime


class CreateSkillCandidateOut(_SkillModel):
    outcome: Literal["candidate-created", "already-exists"]
    candidate: SkillCandidateOut


class SkillActivationOut(_SkillModel):
    id: str
    project_id: str
    workflow_id: str
    candidate_id: str
    schema_version: Literal["1"]
    target_relative_path: Literal[".opencode/skills/remember-verified-evidence/SKILL.md"]
    candidate_content_hash: Sha256
    template_sha256: Sha256
    evaluation_sha256: Sha256
    approval_sha256: Sha256
    prior_present: bool
    prior_sha256: Sha256 | None
    installed_sha256: Sha256
    created_directory: bool
    status: Literal[
        "installing",
        "active",
        "rollback-pending",
        "rolled-back",
        "blocked",
    ]
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None
    rolled_back_at: datetime | None


class SkillActivationPreviewOut(_SkillModel):
    schema_version: Literal["1"]
    project_id: str
    workflow_id: str
    candidate_id: str
    expected_status: Literal["awaiting-approval"]
    target_relative_path: Literal[".opencode/skills/remember-verified-evidence/SKILL.md"]
    candidate_content_hash: Sha256
    template_sha256: Sha256
    evaluation_sha256: Sha256
    approval_sha256: Sha256
    prior_present: bool
    prior_sha256: Sha256 | None
    target_directory_present: bool
    latest_activation: SkillActivationOut | None


class ApproveSkillActivationIn(_SkillModel):
    expected_status: Literal["awaiting-approval"]
    expected_candidate_content_hash: Sha256
    expected_template_sha256: Sha256
    expected_evaluation_sha256: Sha256
    expected_approval_sha256: Sha256
    expected_prior_present: bool
    expected_prior_sha256: Sha256 | None
    expected_target_directory_present: bool


class RollbackSkillActivationIn(_SkillModel):
    expected_status: Literal["active"]
    expected_activation_id: str
    expected_approval_sha256: Sha256
    expected_installed_sha256: Sha256
    expected_current_target_sha256: Sha256


__all__ = (
    "ActiveSkillCapabilityInvokeIn",
    "ActiveSkillCapabilityInvokeOut",
    "ApproveSkillActivationIn",
    "CreateSkillCandidateIn",
    "CreateSkillCandidateOut",
    "ReplayResultOut",
    "RollbackSkillActivationIn",
    "SkillActivationOut",
    "SkillActivationPreviewOut",
    "SkillCandidateOut",
)
