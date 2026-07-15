from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, cast

from ...models import SourcePageRecord
from ..state import WorkflowBlockedError

ENGLISH_STOPWORDS = {
    "about",
    "after",
    "also",
    "among",
    "and",
    "are",
    "been",
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


@dataclass(frozen=True, slots=True)
class PassageCandidate:
    source_id: str
    page: SourcePageRecord
    text: str
    score: float


def string_list(value: Any) -> list[str]:
    return (
        [item for item in cast(list[object], value) if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


def terms(text: str) -> set[str]:
    lowered = text.lower()
    terms = {
        item
        for item in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", lowered)
        if item not in ENGLISH_STOPWORDS
    }
    for run in re.findall(r"[\u3400-\u9fff]+", lowered):
        if len(run) == 1:
            terms.add(run)
        else:
            terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms


def sentence_candidates(text: str) -> Iterable[str]:
    normalized = re.sub(r"[ \t\r\f\v]+", " ", text)
    for item in re.split(r"(?<=[.!?。！？])\s+|\n+", normalized):
        candidate = item.strip()
        if 30 <= len(candidate) <= 1_200:
            yield candidate


def rank_passages(
    query: str, pages: Iterable[SourcePageRecord]
) -> list[PassageCandidate]:
    query_terms = terms(query)
    if not query_terms:
        raise WorkflowBlockedError(
            "query-has-no-search-terms",
            "The research goal contains no usable local-search terms.",
        )
    candidates: list[PassageCandidate] = []
    for page in pages:
        for sentence in sentence_candidates(page.text):
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
    return " ".join(value.casefold().split())


def is_exact_atomic_sentence(passage: str, statement: str) -> bool:
    return any(candidate == statement for candidate in sentence_candidates(passage))


def normalized_contains(haystack: str, needle: str) -> bool:
    return normalize_text(needle) in normalize_text(haystack)
