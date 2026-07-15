from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

_MODEL_SECRET_MAX_BYTES = 4096
_MODEL_IDENTIFIER_MAX_CHARS = 200
_MODEL_API_BASE_MAX_CHARS = 2048
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")


def _default_data_dir() -> Path:
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Spark Agent" / "science-core"
    return Path.home() / ".local" / "share" / "spark-agent" / "science-core"


def _env(primary: str, legacy: str | None = None) -> str | None:
    value = os.environ.get(primary)
    if value is not None:
        return value
    return os.environ.get(legacy) if legacy else None


def _read_model_secret(path_value: str | None) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    try:
        with path.open("rb") as secret_file:
            raw_secret = secret_file.read(_MODEL_SECRET_MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError("The model credential file could not be read") from None
    if len(raw_secret) > _MODEL_SECRET_MAX_BYTES:
        raise ValueError("The model credential file exceeds the safe size limit")
    try:
        secret = raw_secret.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise ValueError("The model credential file is not valid UTF-8") from None
    if any(ord(character) < 32 or ord(character) == 127 for character in secret):
        raise ValueError("The model credential file contains invalid data")
    return secret or None


def _valid_hostname(hostname: str) -> tuple[bool, bool]:
    """Return (valid, loopback) without DNS resolution."""

    try:
        address = ip_address(hostname)
    except ValueError:
        if hostname.endswith("."):
            return False, False
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            return False, False
        if len(ascii_hostname) > 253:
            return False, False
        labels = ascii_hostname.split(".")
        if not labels or any(not _HOST_LABEL.fullmatch(label) for label in labels):
            return False, False
        return True, ascii_hostname.lower() == "localhost"
    return True, address.is_loopback


def normalize_model_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("The configured model identifier contains invalid data")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > _MODEL_IDENTIFIER_MAX_CHARS:
        raise ValueError("The configured model identifier exceeds the safe size limit")
    return normalized


def is_valid_model_identifier(value: str | None) -> bool:
    try:
        return bool(value and normalize_model_identifier(value) == value)
    except ValueError:
        return False


def _normalize_model_api_base(value: str | None) -> str:
    raw_value = value or "https://api.openai.com/v1"
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_value):
        raise ValueError("The configured model API base contains invalid data")
    normalized = raw_value.strip().rstrip("/")
    if not normalized:
        raise ValueError("The configured model API base is empty")
    if len(normalized) > _MODEL_API_BASE_MAX_CHARS:
        raise ValueError("The configured model API base exceeds the safe size limit")
    return normalized


def is_valid_model_api_base(value: str) -> bool:
    try:
        if (
            not value
            or len(value) > _MODEL_API_BASE_MAX_CHARS
            or any(ord(character) <= 32 or ord(character) == 127 for character in value)
        ):
            return False
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            return False
        valid_hostname, loopback = _valid_hostname(hostname)
        if not valid_hostname:
            return False
        return parsed.scheme == "https" or loopback
    except (UnicodeError, ValueError):
        return False


def canonical_model_api_endpoint(value: str) -> str | None:
    """Return the credential-free endpoint used for approval identity hashing."""

    if not is_valid_model_api_base(value):
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        if hostname is None:
            return None
        try:
            address = ip_address(hostname)
            canonical_host = address.compressed
            if address.version == 6:
                canonical_host = f"[{canonical_host}]"
        except ValueError:
            canonical_host = hostname.encode("idna").decode("ascii").lower()
        port = parsed.port
        default_port = 443 if parsed.scheme == "https" else 80
        authority = canonical_host if port in {None, default_port} else f"{canonical_host}:{port}"
        base_path = parsed.path.rstrip("/")
        return f"{parsed.scheme.lower()}://{authority}{base_path}/chat/completions"
    except (UnicodeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_path: Path
    bearer_token: str | None
    max_upload_bytes: int
    llm_model: str | None
    embedding_model: str | None
    model_gateway_configured: bool
    openai_api_base: str
    openai_api_key: str | None = field(repr=False)
    runtime_exchange_dir: Path
    runtime_socket_path: Path
    execution_timeout_seconds: int

    @classmethod
    def from_environment(cls) -> Settings:
        data_dir = Path(
            _env("SPARK_AGENT_CORE_DATA_DIR", "OPENSCIENCE_CORE_DATA_DIR")
            or str(_default_data_dir())
        ).expanduser()
        # Credentials enter the service only through a bounded secret file.
        # In particular, an inherited OPENAI_API_KEY is deliberately ignored.
        openai_api_key = _read_model_secret(
            os.environ.get("SPARK_AGENT_OPENAI_API_KEY_FILE")
        )
        openai_api_base = _normalize_model_api_base(os.environ.get("OPENAI_API_BASE"))
        llm_model = normalize_model_identifier(
            _env("SPARK_AGENT_LLM_MODEL", "OPENSCIENCE_LLM_MODEL")
        )
        embedding_model = normalize_model_identifier(
            _env("SPARK_AGENT_EMBEDDING_MODEL", "OPENSCIENCE_EMBEDDING_MODEL")
        )
        return cls(
            data_dir=data_dir,
            database_path=data_dir / "science-core.sqlite3",
            bearer_token=_env("SPARK_AGENT_CORE_TOKEN", "OPENSCIENCE_CORE_TOKEN") or None,
            max_upload_bytes=int(
                _env("SPARK_AGENT_CORE_MAX_UPLOAD_BYTES", "OPENSCIENCE_CORE_MAX_UPLOAD_BYTES")
                or 100 * 1024 * 1024
            ),
            llm_model=llm_model,
            embedding_model=embedding_model,
            # PaperQA's default embedding is OpenAI-compatible. This internal MVP
            # therefore reports ready only when that credential is present; local
            # embedding profiles remain a later, separately declared capability.
            model_gateway_configured=bool(
                openai_api_key and llm_model and is_valid_model_api_base(openai_api_base)
            ),
            openai_api_base=openai_api_base,
            openai_api_key=openai_api_key,
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
