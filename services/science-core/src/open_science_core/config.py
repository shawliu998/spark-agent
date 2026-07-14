from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path


def _default_data_dir() -> Path:
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Spark Agent" / "science-core"
    return Path.home() / ".local" / "share" / "spark-agent" / "science-core"


def _env(primary: str, legacy: str | None = None) -> str | None:
    value = os.environ.get(primary)
    if value is not None:
        return value
    return os.environ.get(legacy) if legacy else None


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_path: Path
    bearer_token: str | None
    max_upload_bytes: int
    llm_model: str | None
    embedding_model: str | None
    model_gateway_configured: bool
    runtime_exchange_dir: Path
    runtime_socket_path: Path
    execution_timeout_seconds: int

    @classmethod
    def from_environment(cls) -> Settings:
        data_dir = Path(
            _env("SPARK_AGENT_CORE_DATA_DIR", "OPENSCIENCE_CORE_DATA_DIR")
            or str(_default_data_dir())
        ).expanduser()
        return cls(
            data_dir=data_dir,
            database_path=data_dir / "science-core.sqlite3",
            bearer_token=_env("SPARK_AGENT_CORE_TOKEN", "OPENSCIENCE_CORE_TOKEN") or None,
            max_upload_bytes=int(
                _env("SPARK_AGENT_CORE_MAX_UPLOAD_BYTES", "OPENSCIENCE_CORE_MAX_UPLOAD_BYTES")
                or 100 * 1024 * 1024
            ),
            llm_model=_env("SPARK_AGENT_LLM_MODEL", "OPENSCIENCE_LLM_MODEL") or None,
            embedding_model=_env(
                "SPARK_AGENT_EMBEDDING_MODEL", "OPENSCIENCE_EMBEDDING_MODEL"
            )
            or None,
            # PaperQA's default embedding is OpenAI. The first internal MVP
            # therefore reports ready only when that credential is present;
            # other provider/local embedding profiles can be added explicitly.
            model_gateway_configured=bool(os.environ.get("OPENAI_API_KEY")),
            runtime_exchange_dir=Path(
                _env("SPARK_AGENT_RUNTIME_EXCHANGE_DIR", "OPENSCIENCE_RUNTIME_EXCHANGE_DIR")
                or str(data_dir.parent / "science-runtime")
            ).expanduser(),
            runtime_socket_path=Path(
                _env("SPARK_AGENT_RUNTIME_SOCKET_PATH", "OPENSCIENCE_RUNTIME_SOCKET_PATH")
                or str(data_dir.parent / "science-runtime-socket" / "runtime.sock")
            ).expanduser(),
            execution_timeout_seconds=min(
                max(
                    int(
                        _env(
                            "SPARK_AGENT_EXECUTION_TIMEOUT_SECONDS",
                            "OPENSCIENCE_EXECUTION_TIMEOUT_SECONDS",
                        )
                        or "120"
                    ),
                    1,
                ),
                120,
            ),
        )


settings = Settings.from_environment()
