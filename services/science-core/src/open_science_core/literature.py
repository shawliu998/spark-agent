from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import field_serializer

from .config import settings as app_settings


DEFAULT_OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"


class _MaskedSecret(str):
    """A string accepted by provider clients without exposing itself in reprs."""

    def __repr__(self) -> str:
        return "'**********'"


@dataclass(frozen=True, slots=True)
class LiteratureResult:
    answer: str
    evidence_candidates: list[str]


def paper_qa_available() -> bool:
    try:
        import paperqa  # noqa: F401
    except ImportError:
        return False
    return True


class PaperQaAdapter:
    """Small adapter: PaperQA is an algorithm provider, never the state store."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[tuple[str, ...], Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def ask(
        self,
        project_id: str,
        source_paths: list[Path],
        question: str,
        model: str | None,
    ) -> LiteratureResult:
        try:
            from paperqa import Docs, Settings
        except ImportError as error:
            raise RuntimeError("PaperQA2 is not installed in science-core") from error

        lock = self._locks.setdefault(project_id, asyncio.Lock())
        async with lock:
            selected_model = model or app_settings.llm_model
            api_key = app_settings.openai_api_key
            if not selected_model or not api_key:
                raise RuntimeError("PaperQA model gateway is not configured")
            signature = (
                selected_model,
                app_settings.embedding_model or "",
                app_settings.openai_api_base,
                *sorted(str(path) for path in source_paths),
            )
            settings_kwargs = _paperqa_settings_kwargs(
                selected_model,
                app_settings.embedding_model,
                api_key,
                app_settings.openai_api_base,
            )
            settings = _create_paperqa_settings(Settings, settings_kwargs)
            cached = self._cache.get(project_id)
            if cached is None or cached[0] != signature:
                docs = Docs()
                for path in source_paths:
                    await docs.aadd(path, settings=settings)
                self._cache[project_id] = (signature, docs)
            else:
                docs = cached[1]

            response = await docs.aquery(question, settings=settings)
            answer = str(
                getattr(response, "answer", None)
                or getattr(response, "formatted_answer", None)
                or response
            )
            contexts = getattr(response, "contexts", None) or []
            used_contexts = set(getattr(response, "used_contexts", None) or [])
            candidates = list(
                dict.fromkeys(
                    candidate
                    for item in contexts
                    if getattr(item, "id", None) in used_contexts
                    if (candidate := _context_quote(item))
                )
            )
            return LiteratureResult(answer=answer, evidence_candidates=candidates)


def _paperqa_model_identifier(raw_model: str) -> str:
    # The gateway must receive the configured model ID unchanged. PaperQA uses
    # LiteLLM, which separately needs a provider prefix for OpenAI-compatible
    # endpoints; LiteLLM removes only this prefix before making the request.
    return f"openai/{raw_model}"


def _paperqa_settings_kwargs(
    raw_model: str,
    raw_embedding_model: str | None,
    api_key: str,
    api_base: str,
) -> dict[str, Any]:
    model = _paperqa_model_identifier(raw_model)
    embedding = _paperqa_model_identifier(
        raw_embedding_model or DEFAULT_OPENAI_EMBEDDING_MODEL
    )
    secret = _MaskedSecret(api_key)
    return {
        "llm": model,
        "llm_config": _litellm_router_config(model, secret, api_base),
        "summary_llm": model,
        "summary_llm_config": _litellm_router_config(model, secret, api_base),
        "embedding": embedding,
        "embedding_config": {
            "kwargs": {
                "api_key": secret,
                "api_base": api_base,
            }
        },
        "agent": {
            "agent_llm": model,
            "agent_llm_config": _litellm_router_config(model, secret, api_base),
        },
        "parsing": {
            "enrichment_llm": model,
            "enrichment_llm_config": _litellm_router_config(
                model,
                secret,
                api_base,
            ),
        },
    }


def _create_paperqa_settings(
    settings_type: type[Any],
    settings_kwargs: dict[str, Any],
) -> Any:
    if not hasattr(settings_type, "model_fields"):
        return settings_type(**settings_kwargs)
    return _credential_safe_settings_type(settings_type)(**settings_kwargs)


@lru_cache(maxsize=4)
def _credential_safe_settings_type(settings_type: type[Any]) -> type[Any]:
    class CredentialSafePaperQaSettings(settings_type):
        @field_serializer(
            "llm_config",
            "summary_llm_config",
            "embedding_config",
            "agent",
            "parsing",
            mode="wrap",
            when_used="always",
        )
        def _serialize_credential_config(
            self,
            value: Any,
            serialize: Any,
        ) -> Any:
            return _redact_api_keys(serialize(value))

    return CredentialSafePaperQaSettings


def _redact_api_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "**********" if key == "api_key" else _redact_api_keys(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_api_keys(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_api_keys(item) for item in value)
    return value


def _litellm_router_config(
    model: str,
    api_key: _MaskedSecret,
    api_base: str,
) -> dict[str, Any]:
    return {
        "model_list": [
            {
                "model_name": model,
                "litellm_params": {
                    "model": model,
                    "api_key": api_key,
                    "api_base": api_base,
                },
            }
        ]
    }


def _context_quote(context: Any) -> str | None:
    # PaperQA reader/context shapes evolve. Prefer original passage fields and
    # deliberately avoid treating a generated contextual summary as verified.
    candidates: list[Any] = [
        getattr(context, "source_text", None),
        getattr(context, "quote", None),
        getattr(context, "passage", None),
    ]
    text_object = getattr(context, "text", None)
    if text_object is not None and not isinstance(text_object, str):
        candidates.extend(
            [
                getattr(text_object, "text", None),
                getattr(text_object, "content", None),
            ]
        )
    elif isinstance(text_object, str):
        candidates.append(text_object)
    if isinstance(context, dict):
        candidates.extend(
            [
                context.get("source_text"),
                context.get("quote"),
                context.get("passage"),
                context.get("text"),
            ]
        )
    for value in candidates:
        if isinstance(value, str) and len(value.strip()) >= 20:
            return value.strip()
    return None


paper_qa = PaperQaAdapter()
