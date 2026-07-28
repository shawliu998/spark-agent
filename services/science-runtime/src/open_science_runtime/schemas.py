from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .config import MAX_TIMEOUT_SECONDS
from .fixed_analysis_policy import (
    COMPILED_ANALYSIS_POLICY_ID,
    COMPILED_ANALYSIS_TEMPLATE,
    FIXED_ANALYSIS_POLICY_ID,
    GENERAL_ANALYSIS_POLICY_ID,
    AnalysisPolicyId,
    AnalysisPolicyTemplate,
)


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )


RunId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ExecuteIn(ApiModel):
    run_id: RunId
    run_dir: Annotated[str, StringConstraints(min_length=2, max_length=4096)]
    dataset_path: Annotated[str, StringConstraints(min_length=2, max_length=4096)]
    objective: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=8000),
    ]
    code: Annotated[str, StringConstraints(min_length=1, max_length=200_000)]
    timeout_seconds: int = Field(default=120, ge=1, le=MAX_TIMEOUT_SECONDS)
    payload_sha256: Sha256
    policy_profile_id: AnalysisPolicyId
    policy_template: AnalysisPolicyTemplate | None = None
    analysis_spec_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=36),
    ] | None = None
    analysis_spec_sha256: Sha256 | None = None
    dataset_profile_sha256: Sha256 | None = None
    compiler_version: Literal["analysis-spec-compiler-v1"] | None = None
    approved_code_sha256: Sha256 | None = None

    @field_validator("run_dir", "dataset_path", "code")
    @classmethod
    def reject_nul_bytes(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("NUL bytes are not allowed")
        return value

    @field_validator("code")
    @classmethod
    def reject_blank_code(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("code must contain Python source")
        return value

    @model_validator(mode="after")
    def validate_policy_contract(self) -> Self:
        compiled_provenance = (
            self.analysis_spec_id,
            self.analysis_spec_sha256,
            self.dataset_profile_sha256,
            self.compiler_version,
            self.approved_code_sha256,
        )
        if self.policy_profile_id == FIXED_ANALYSIS_POLICY_ID:
            if self.policy_template not in {"baseline", "repair-1", "repair-2"}:
                raise ValueError("fixed analysis policy requires policyTemplate")
            if any(value is not None for value in compiled_provenance):
                raise ValueError("fixed analysis policy does not accept compiled provenance")
        elif self.policy_profile_id == COMPILED_ANALYSIS_POLICY_ID:
            if (
                self.policy_template != COMPILED_ANALYSIS_TEMPLATE
                or any(value is None for value in compiled_provenance)
                or self.compiler_version != COMPILED_ANALYSIS_TEMPLATE
            ):
                raise ValueError("compiled analysis policy requires exact provenance")
        elif self.policy_profile_id == GENERAL_ANALYSIS_POLICY_ID:
            if self.policy_template is not None or any(
                value is not None for value in compiled_provenance
            ):
                raise ValueError("general analysis policy does not accept policy provenance")
        return self


ArtifactType = Literal[
    "notebook-input",
    "notebook-executed",
    "environment",
    "stdout",
    "stderr",
    "log",
    "image",
    "table",
    "json",
]


class ArtifactOut(ApiModel):
    path: str
    mime_type: str
    content_hash: Sha256
    size_bytes: int = Field(ge=0)
    artifact_type: ArtifactType


class ExecuteOut(ApiModel):
    status: Literal["completed", "failed"]
    environment_hash: Sha256
    stdout: str
    stderr: str
    log: str
    artifacts: list[ArtifactOut]


class HealthOut(ApiModel):
    status: Literal["ok", "degraded"]
    version: str
    data_root: str
    kernel: Literal["python3"]
    kernel_available: bool
    max_timeout_seconds: int
