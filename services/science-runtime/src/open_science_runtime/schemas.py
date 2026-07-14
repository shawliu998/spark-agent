from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .config import MAX_TIMEOUT_SECONDS


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
        StringConstraints(strip_whitespace=True, min_length=1, max_length=4096),
    ]
    code: Annotated[str, StringConstraints(min_length=1, max_length=200_000)]
    timeout_seconds: int = Field(default=120, ge=1, le=MAX_TIMEOUT_SECONDS)
    payload_sha256: Sha256

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
