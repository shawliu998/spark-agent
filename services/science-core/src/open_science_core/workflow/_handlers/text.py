from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Protocol, cast

from ..state import WorkflowBlockedError

ENGLISH_STOPWORDS = {
    "about",
    "after",
    "also",
    "among",
    "and",
    "are",
    "been",
    "best",
    "between",
    "can",
    "could",
    "does",
    "for",
    "from",
    "has",
    "have",
    "into",
    "its",
    "may",
    "more",
    "most",
    "not",
    "our",
    "should",
    "that",
    "the",
    "their",
    "these",
    "this",
    "those",
    "through",
    "using",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
}

REFERENCE_SECTION_HEADINGS = {
    "bibliography",
    "references",
    "works cited",
}


@dataclass(frozen=True, slots=True)
class PassageCandidate:
    source_id: str
    page: PassagePage
    text: str
    score: float


class PassagePage(Protocol):
    source_id: str
    page_index: int
    page_label: str | None
    width: float
    height: float
    text: str
    words: list[dict[str, Any]]


def string_list(value: Any) -> list[str]:
    return (
        [item for item in cast(list[object], value) if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


def terms(text: str) -> set[str]:
    lowered = re.sub(
        r"\b(?:large[\s-]+language[\s-]+models?|llms?)\b",
        "large-language-model",
        text.lower(),
    )
    terms = {
        _stem_english_term(item)
        for item in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", lowered)
        if item not in ENGLISH_STOPWORDS
    }
    for run in re.findall(r"[\u3400-\u9fff]+", lowered):
        if len(run) == 1:
            terms.add(run)
        else:
            terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms


def _stem_english_term(value: str) -> str:
    term = value.strip("_-")
    if len(term) > 3 and term.endswith("s") and not term.endswith(("is", "ss")):
        term = term[:-1]
    if len(term) > 6 and term.endswith("ation"):
        return f"{term[:-5]}ate"
    if len(term) > 6 and term.endswith("ating"):
        return f"{term[:-5]}ate"
    if len(term) > 5 and term.endswith("ated"):
        return f"{term[:-4]}ate"
    if len(term) > 5 and term.endswith("tion"):
        return term[:-3]
    if len(term) > 4 and term.endswith("ing"):
        return term[:-3]
    if len(term) > 4 and term.endswith("ed"):
        return term[:-2]
    return term


def sentence_candidates(text: str) -> Iterable[str]:
    normalized = re.sub(r"[ \t\r\f\v]+", " ", text)
    for item in re.split(r"(?<=[.!?。！？])\s+|\n+", normalized):
        candidate = item.strip()
        if 30 <= len(candidate) <= 1_200:
            yield candidate


def _join_pdf_words(words: list[dict[str, Any]]) -> str:
    text = ""
    for word in sorted(
        words,
        key=lambda item: (
            int(item.get("line", 0)),
            int(item.get("word", 0)),
            float(item.get("x0", 0)),
        ),
    ):
        value = str(word.get("text", "")).strip()
        if not value:
            continue
        if text.endswith("-") and value[0].islower():
            text = f"{text[:-1]}{value}"
        else:
            text = f"{text} {value}".strip()
    return text


def _page_blocks(page: PassagePage) -> list[str]:
    if not page.words:
        return [page.text]
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for word in page.words:
        block = word.get("block", 0)
        grouped[int(block) if isinstance(block, (int, float)) else 0].append(word)
    ordered = sorted(
        grouped.values(),
        key=lambda words: (
            min(float(word.get("y0", 0)) for word in words),
            min(float(word.get("x0", 0)) for word in words),
        ),
    )
    return [text for words in ordered if (text := _join_pdf_words(words))]


def _reference_start_pages(
    pages: list[PassagePage],
) -> dict[str, int]:
    starts: dict[str, int] = {}
    for page in pages:
        if page.source_id in starts:
            continue
        headings = {
            normalize_text(block).strip(" .:")
            for block in _page_blocks(page)
            if len(block) <= 80
        }
        if headings.intersection(REFERENCE_SECTION_HEADINGS):
            starts[page.source_id] = page.page_index
    return starts


def _is_complete_prose_sentence(value: str) -> bool:
    if not re.search(r"[.!?。！？][\])}\"'”’]*$", value):
        return False
    if len(re.findall(r"[A-Za-z]{2,}", value)) < 6:
        return bool(re.search(r"[\u3400-\u9fff]", value))
    first_letter = next((character for character in value if character.isalpha()), "")
    return not (
        first_letter
        and first_letter.isascii()
        and first_letter.islower()
    )


def rank_passages(
    query: str, pages: Iterable[PassagePage]
) -> list[PassageCandidate]:
    materialized_pages = list(pages)
    query_terms = terms(query)
    if not query_terms:
        raise WorkflowBlockedError(
            "query-has-no-search-terms",
            "The research goal contains no usable local-search terms.",
        )
    reference_starts = _reference_start_pages(materialized_pages)
    candidates: list[PassageCandidate] = []
    for page in materialized_pages:
        reference_start = reference_starts.get(page.source_id)
        if reference_start is not None and page.page_index >= reference_start:
            continue
        for block in _page_blocks(page):
            for sentence in sentence_candidates(block):
                if not _is_complete_prose_sentence(sentence):
                    continue
                sentence_terms = terms(sentence)
                overlap = query_terms.intersection(sentence_terms)
                if not overlap:
                    continue
                coverage = len(overlap) / len(query_terms)
                density = len(overlap) / max(1, len(sentence_terms))
                score = coverage * 0.8 + density * 0.2
                candidates.append(
                    PassageCandidate(
                        source_id=page.source_id,
                        page=page,
                        text=sentence,
                        score=score,
                    )
                )
    return sorted(
        candidates,
        key=lambda item: (-item.score, item.source_id, item.page.page_index),
    )


def select_diverse_passages(
    candidates: list[PassageCandidate], *, max_passages: int, max_per_source: int
) -> list[PassageCandidate]:
    by_source: dict[str, list[PassageCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_source[candidate.source_id].append(candidate)
    selected: list[PassageCandidate] = []
    seen: set[tuple[str, int, str]] = set()

    def add(candidate: PassageCandidate) -> bool:
        key = (candidate.source_id, candidate.page.page_index, candidate.text)
        if key in seen:
            return False
        source_count = sum(item.source_id == candidate.source_id for item in selected)
        if source_count >= max_per_source:
            return False
        seen.add(key)
        selected.append(candidate)
        return True

    # Give every source one opportunity before filling remaining slots by score.
    for source_id in sorted(by_source):
        if by_source[source_id] and len(selected) < max_passages:
            add(by_source[source_id][0])
    for candidate in candidates:
        if len(selected) >= max_passages:
            break
        add(candidate)
    return selected


def atomic_statement(text: str) -> str:
    for candidate in sentence_candidates(text):
        return candidate
    return " ".join(text.split())[:800].strip()


def normalize_text(value: str) -> str:
    dehyphenated = re.sub(
        r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])",
        "",
        value,
    )
    return " ".join(dehyphenated.casefold().split())


def is_exact_atomic_sentence(passage: str, statement: str) -> bool:
    return any(candidate == statement for candidate in sentence_candidates(passage))


def normalized_contains(haystack: str, needle: str) -> bool:
    return normalize_text(needle) in normalize_text(haystack)
