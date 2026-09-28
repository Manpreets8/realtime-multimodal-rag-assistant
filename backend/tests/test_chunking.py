from itertools import pairwise

import pytest

from app.rag.chunking import chunk_document, merge_small_blocks, split_text
from app.rag.extraction import ExtractedDocument, TextBlock

SENTENCES = [f"Sentence number {i} explains policy detail {i}." for i in range(60)]
LONG_TEXT = " ".join(SENTENCES)


def test_short_text_is_a_single_chunk() -> None:
    assert split_text("Just one short paragraph.", 200, 20) == ["Just one short paragraph."]


def test_chunks_respect_size_limit() -> None:
    chunks = split_text(LONG_TEXT, 200, 40)

    assert len(chunks) > 5
    assert all(len(chunk) <= 200 for chunk in chunks)


def test_no_text_is_lost() -> None:
    chunks = split_text(LONG_TEXT, 200, 40)

    for sentence in SENTENCES:
        assert any(sentence in chunk for chunk in chunks), sentence


def test_consecutive_chunks_overlap() -> None:
    chunks = split_text(LONG_TEXT, 200, 60)

    for previous, current in pairwise(chunks):
        # The start of each chunk repeats a sentence from the end of the previous one.
        first_sentence = current.split(". ")[0]
        assert first_sentence in previous


def test_zero_overlap_produces_disjoint_chunks() -> None:
    chunks = split_text(LONG_TEXT, 200, 0)

    assert sum(len(chunk) for chunk in chunks) <= len(LONG_TEXT)


def test_prefers_sentence_boundaries_over_mid_sentence_cuts() -> None:
    chunks = split_text(LONG_TEXT, 200, 0)

    assert all(chunk.endswith(".") for chunk in chunks)


def test_prefers_paragraph_boundaries() -> None:
    paragraphs = ["Alpha paragraph " * 5, "Beta paragraph " * 5, "Gamma paragraph " * 5]
    chunks = split_text("\n\n".join(p.strip() for p in paragraphs), 100, 0)

    assert [chunk.split()[0] for chunk in chunks] == ["Alpha", "Beta", "Gamma"]


def test_unbreakable_text_is_hard_split() -> None:
    url = "https://example.com/" + "a" * 450

    chunks = split_text(url, 100, 10)

    assert all(len(chunk) <= 100 for chunk in chunks)
    assert "".join(chunks).startswith("https://example.com/")


def test_whitespace_or_punctuation_only_chunks_are_dropped() -> None:
    assert split_text("\n\n  ---  \n\n", 100, 10) == []


def test_chunks_never_cross_pages_and_keep_page_numbers() -> None:
    document = ExtractedDocument(
        blocks=[TextBlock(LONG_TEXT, page_number=1), TextBlock("Page two has its own text.", page_number=2)],
        page_count=2,
    )

    chunks = chunk_document(document, chunk_size=300, chunk_overlap=50)

    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert chunks[-1].page_number == 2
    assert chunks[-1].text == "Page two has its own text."
    assert all(c.page_number == 1 for c in chunks[:-1])


def test_small_sections_are_merged_with_the_next() -> None:
    blocks = [
        TextBlock("Leave Policy", section="Leave Policy"),
        TextBlock(
            "Annual leave\n\nEmployees get 18 days per year." * 3, section="Leave Policy > Annual leave"
        ),
    ]

    merged = merge_small_blocks(blocks, chunk_size=400)

    assert len(merged) == 1
    assert merged[0].text.startswith("Leave Policy\n\nAnnual leave")
    assert merged[0].section == "Leave Policy > Annual leave"


def test_short_sibling_sections_are_not_merged() -> None:
    blocks = [
        TextBlock("Leave Policy\n\n18 days.", section="Leave Policy"),
        TextBlock("Remote Work\n\nNeeds approval.", section="Remote Work"),
    ]

    merged = merge_small_blocks(blocks, chunk_size=400)

    assert [block.section for block in merged] == ["Leave Policy", "Remote Work"]


def test_blocks_on_different_pages_are_not_merged() -> None:
    blocks = [TextBlock("Tiny", page_number=1), TextBlock("Also tiny", page_number=2)]

    assert len(merge_small_blocks(blocks, chunk_size=400)) == 2


def test_invalid_overlap_is_rejected() -> None:
    with pytest.raises(ValueError):
        chunk_document(ExtractedDocument(blocks=[]), chunk_size=100, chunk_overlap=100)
