from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import settings as app_settings


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
            settings_kwargs: dict[str, str] = {}
            if selected_model:
                settings_kwargs.update(llm=selected_model, summary_llm=selected_model)
            if app_settings.embedding_model:
                settings_kwargs["embedding"] = app_settings.embedding_model
            settings = Settings(**settings_kwargs)
            signature = (
                selected_model or "",
                app_settings.embedding_model or "",
                *sorted(str(path) for path in source_paths),
            )
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
            [context.get("source_text"), context.get("quote"), context.get("passage"), context.get("text")]
        )
    for value in candidates:
        if isinstance(value, str) and len(value.strip()) >= 20:
            return value.strip()
    return None


paper_qa = PaperQaAdapter()
