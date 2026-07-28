from __future__ import annotations

from typing import cast

from open_science_core.models import SourcePageRecord
from open_science_core.pdf import PdfPage, locate_quote
from open_science_core.workflow._handlers.evidence import page_contains_verified_quote
from open_science_core.workflow._handlers.text import PassagePage, rank_passages, terms


def _words(blocks: list[list[str]]) -> list[dict[str, object]]:
    words: list[dict[str, object]] = []
    for block_index, block in enumerate(blocks):
        for word_index, value in enumerate(block):
            words.append(
                {
                    "text": value,
                    "x0": float(block_index * 250 + word_index * 10),
                    "y0": float(block_index * 40),
                    "x1": float(block_index * 250 + word_index * 10 + 8),
                    "y1": float(block_index * 40 + 10),
                    "block": block_index,
                    "line": 0,
                    "word": word_index,
                }
            )
    return words


def _page(
    page_index: int,
    blocks: list[list[str]],
    *,
    source_id: str = "source-1",
) -> SourcePageRecord:
    words = _words(blocks)
    return SourcePageRecord(
        source_id=source_id,
        page_index=page_index,
        page_label=str(page_index + 1),
        width=600,
        height=800,
        text=" ".join(str(word["text"]) for word in words),
        words=words,
    )


def test_rank_passages_keeps_columns_separate_and_excludes_references() -> None:
    body = _page(
        0,
        [
            [
                "The",
                "study",
                "evaluated",
                "automatic",
                "methods",
                "to",
                "detect",
                "hallucinations",
                "in",
                "LLMs.",
            ],
            [
                "A",
                "separate",
                "column",
                "describes",
                "unrelated",
                "background",
                "material.",
            ],
        ],
    )
    references = _page(
        1,
        [
            ["References"],
            [
                "Someone.",
                "2024.",
                "Methods",
                "to",
                "detect",
                "hallucinations",
                "in",
                "large",
                "language",
                "models.",
            ],
        ],
    )

    ranked = rank_passages(
        "Which evaluation methods detect hallucinations in large language models?",
        cast(list[PassagePage], [body, references]),
    )

    assert [candidate.text for candidate in ranked] == [
        "The study evaluated automatic methods to detect hallucinations in LLMs."
    ]
    assert ranked[0].page.page_index == 0


def test_rank_passages_normalizes_inflections_and_pdf_line_hyphens() -> None:
    page = _page(
        0,
        [
            [
                "Researchers",
                "evaluated",
                "a",
                "method",
                "that",
                "detects",
                "hallu-",
                "cinations",
                "in",
                "LLMs.",
            ]
        ],
    )

    ranked = rank_passages(
        "How should hallucination detection methods evaluate large language models?",
        cast(list[PassagePage], [page]),
    )

    assert ranked[0].text == (
        "Researchers evaluated a method that detects hallucinations in LLMs."
    )
    assert {
        "evaluate",
        "method",
        "detect",
        "hallucinate",
        "large-language-model",
    }.issubset(terms(ranked[0].text))


def test_locate_quote_returns_dehyphenated_exact_text() -> None:
    words = _words(
        [
            [
                "The",
                "method",
                "detects",
                "hallu-",
                "cinations",
                "reliably.",
            ],
            [
                "A",
                "second",
                "column",
                "must",
                "not",
                "interrupt",
                "the",
                "quote.",
            ],
        ]
    )
    words.sort(
        key=lambda word: (
            float(str(word["y0"])),
            int(str(word["word"])),
        )
    )
    page = PdfPage(
        page_index=0,
        page_label="1",
        width=600,
        height=800,
        text="The method detects hallu-\ncinations reliably.",
        words=words,
    )

    located = locate_quote(
        "The method detects hallucinations reliably.",
        [page],
    )

    assert located is not None
    assert located.verified
    assert located.text == "The method detects hallucinations reliably."


def test_integrity_check_accepts_exact_quote_from_one_pdf_block() -> None:
    words = _words(
        [
            ["The", "method", "detects", "hallucinations", "reliably."],
            ["A", "second", "column", "must", "stay", "separate."],
        ]
    )
    page = SourcePageRecord(
        source_id="source-1",
        page_index=0,
        page_label="1",
        width=600,
        height=800,
        text=(
            "The A method second detects column hallucinations must reliably. "
            "stay separate."
        ),
        words=words,
    )

    assert page_contains_verified_quote(
        page,
        "The method detects hallucinations reliably.",
    )
