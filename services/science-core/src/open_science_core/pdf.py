from __future__ import annotations

import importlib
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal, Protocol, cast, overload

_PdfWord = tuple[float, float, float, float, str, int, int, int]


class _PdfRect(Protocol):
    width: float
    height: float


class _PdfPageHandle(Protocol):
    rect: _PdfRect

    @overload
    def get_text(self, option: Literal["words"], *, sort: bool) -> list[_PdfWord]: ...

    @overload
    def get_text(self, option: Literal["text"], *, sort: bool) -> str: ...

    def get_label(self) -> str: ...


class _PdfDocument(Protocol):
    metadata: dict[str, str] | None
    page_count: int

    def load_page(self, page_id: int) -> _PdfPageHandle: ...

    def close(self) -> None: ...


class _FitzModule(Protocol):
    def open(self, path: Path) -> _PdfDocument: ...


_fitz = cast(_FitzModule, importlib.import_module("fitz"))


@dataclass(frozen=True, slots=True)
class PdfPage:
    page_index: int
    page_label: str | None
    width: float
    height: float
    text: str
    words: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class PdfExtraction:
    title: str | None
    authors: list[str]
    pages: list[PdfPage]


@dataclass(frozen=True, slots=True)
class LocatedQuote:
    page_index: int
    page_label: str | None
    text: str
    bbox: dict[str, float] | None
    confidence: float
    verified: bool


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).replace("\u00ad", "")
    normalized = re.sub(
        r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])",
        "",
        normalized,
    )
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def extract_pdf(path: Path) -> PdfExtraction:
    document = _fitz.open(path)
    try:
        metadata = document.metadata or {}
        title = (metadata.get("title") or "").strip() or None
        author_value = (metadata.get("author") or "").strip()
        authors = [part.strip() for part in re.split(r"[;,]", author_value) if part.strip()]
        pages: list[PdfPage] = []
        for index in range(document.page_count):
            page = document.load_page(index)
            page_rect = page.rect
            words: list[dict[str, Any]] = []
            for item in page.get_text("words", sort=True):
                x0, y0, x1, y1, text, block, line, word = item[:8]
                words.append(
                    {
                        "text": str(text),
                        "x0": float(x0),
                        "y0": float(y0),
                        "x1": float(x1),
                        "y1": float(y1),
                        "block": int(block),
                        "line": int(line),
                        "word": int(word),
                    }
                )
            label = page.get_label() or str(index + 1)
            pages.append(
                PdfPage(
                    page_index=index,
                    page_label=label,
                    width=float(page_rect.width),
                    height=float(page_rect.height),
                    text=page.get_text("text", sort=True).strip(),
                    words=words,
                )
            )
        return PdfExtraction(title=title, authors=authors, pages=pages)
    finally:
        document.close()


def locate_quote(quote: str, pages: list[PdfPage]) -> LocatedQuote | None:
    normalized_quote = normalize_text(quote)
    if len(normalized_quote) < 12:
        return None

    best: tuple[
        float,
        PdfPage,
        list[dict[str, Any]],
        int,
        int,
        str,
    ] | None = None
    for page in pages:
        for words in _ordered_word_groups(page.words):
            stream, spans = _word_stream(words)
            exact = stream.find(normalized_quote)
            if exact >= 0:
                end = exact + len(normalized_quote)
                return LocatedQuote(
                    page_index=page.page_index,
                    page_label=page.page_label,
                    text=_text_for_range(words, spans, exact, end),
                    bbox=_bbox_for_range(
                        words, spans, exact, end, page.width, page.height
                    ),
                    confidence=1.0,
                    verified=True,
                )

            # Contextual summaries are not always verbatim. Compare a bounded lead
            # window and keep it unverified unless similarity is exceptionally high.
            probe = normalized_quote[:500]
            if not stream or not probe:
                continue
            window_size = min(max(len(probe), 120), len(stream))
            step = max(20, window_size // 5)
            for start in range(0, max(1, len(stream) - window_size + 1), step):
                candidate = stream[start : start + window_size]
                score = SequenceMatcher(None, probe, candidate).ratio()
                if best is None or score > best[0]:
                    best = (
                        score,
                        page,
                        words,
                        start,
                        start + len(candidate),
                        candidate,
                    )

    if best is None or best[0] < 0.72:
        return None
    score, page, words, start, end, candidate = best
    _, spans = _word_stream(words)
    return LocatedQuote(
        page_index=page.page_index,
        page_label=page.page_label,
        text=_text_for_range(words, spans, start, end),
        bbox=_bbox_for_range(words, spans, start, end, page.width, page.height),
        confidence=round(score, 4),
        verified=score >= 0.92,
    )


def _ordered_word_groups(
    words: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for word in words:
        raw_block = word.get("block", 0)
        block = int(raw_block) if isinstance(raw_block, (int, float)) else 0
        grouped.setdefault(block, []).append(word)
    return [
        sorted(
            group,
            key=lambda word: (
                int(word.get("line", 0)),
                int(word.get("word", 0)),
                float(word.get("x0", 0)),
            ),
        )
        for group in grouped.values()
    ]


def _word_stream(words: list[dict[str, Any]]) -> tuple[str, list[tuple[int, int]]]:
    text = ""
    spans: list[tuple[int, int]] = []
    for word in words:
        normalized = normalize_text(str(word.get("text", "")))
        if not normalized:
            spans.append((len(text), len(text)))
            continue
        if (
            text.endswith("-")
            and normalized[0].islower()
            and spans
        ):
            text = text[:-1]
            previous_start, _previous_end = spans[-1]
            spans[-1] = (previous_start, len(text))
        elif text:
            text += " "
        start = len(text)
        text += normalized
        spans.append((start, len(text)))
    return text, spans


def _bbox_for_range(
    words: list[dict[str, Any]],
    spans: list[tuple[int, int]],
    start: int,
    end: int,
    page_width: float,
    page_height: float,
) -> dict[str, float] | None:
    selected = [word for word, span in zip(words, spans, strict=False) if span[1] > start and span[0] < end]
    if not selected:
        return None
    if page_width <= 0 or page_height <= 0:
        return None
    return {
        "x0": min(float(word["x0"]) for word in selected) / page_width,
        "y0": min(float(word["y0"]) for word in selected) / page_height,
        "x1": max(float(word["x1"]) for word in selected) / page_width,
        "y1": max(float(word["y1"]) for word in selected) / page_height,
    }


def _text_for_range(
    words: list[dict[str, Any]],
    spans: list[tuple[int, int]],
    start: int,
    end: int,
) -> str:
    selected = [
        str(word.get("text", ""))
        for word, span in zip(words, spans, strict=False)
        if span[1] > start and span[0] < end
    ]
    text = ""
    for value in selected:
        if not value:
            continue
        if text.endswith("-") and value[0].islower():
            text = f"{text[:-1]}{value}"
        else:
            text = f"{text} {value}".strip()
    return text
