from pathlib import Path

import pytest

from app.core.rag import TextChunker
from app.services.document_service import ParsedDocumentFile

WORKSPACE_ID = "1" * 32


def parsed_document(text: str) -> ParsedDocumentFile:
    return ParsedDocumentFile(
        id="a" * 32,
        original_filename="guide.txt",
        stored_filename="guide.txt",
        extension=".txt",
        text=text,
        character_count=len(text),
        parsed_path=str(Path("storage/parsed") / ("a" * 32) / "guide.txt"),
    )


def test_chunker_prefers_paragraph_boundaries():
    text = f"{'A' * 16}\n\n{'B' * 16}"

    chunks = TextChunker(chunk_size=30, chunk_overlap=0).split_text(text)

    assert chunks == ["A" * 16, "B" * 16]


def test_chunker_splits_long_text_with_overlap():
    chunks = TextChunker(chunk_size=10, chunk_overlap=3).split_text(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    )

    assert all(len(chunk) <= 10 for chunk in chunks)
    assert chunks[0][-3:] == chunks[1][:3]
    assert "".join([chunks[0], *[chunk[3:] for chunk in chunks[1:]]]) == (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    )


def test_chunker_builds_deterministic_chunk_metadata():
    document = parsed_document("ABCDEFGHIJKLMNO")

    chunks = TextChunker(chunk_size=10, chunk_overlap=2).chunk_document(
        document,
        workspace_id=WORKSPACE_ID,
    )

    assert [chunk.id for chunk in chunks] == [f"{document.id}:0", f"{document.id}:1"]
    assert chunks[0].document_id == document.id
    assert chunks[0].workspace_id == WORKSPACE_ID
    assert chunks[0].original_filename == "guide.txt"
    assert chunks[0].chunk_index == 0
    assert chunks[0].character_count == len(chunks[0].text)


def test_chunker_rejects_blank_text():
    with pytest.raises(ValueError, match="document text cannot be empty"):
        TextChunker(chunk_size=10, chunk_overlap=2).split_text(" \n\t ")


def test_chunker_rejects_invalid_limits():
    with pytest.raises(ValueError, match="chunk_size"):
        TextChunker(chunk_size=0, chunk_overlap=0)

    with pytest.raises(ValueError, match="smaller than chunk_size"):
        TextChunker(chunk_size=10, chunk_overlap=10)
