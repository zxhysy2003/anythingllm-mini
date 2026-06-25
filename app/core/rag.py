from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.core.config import settings

if TYPE_CHECKING:
    from app.services.document_service import ParsedDocumentFile

GLOBAL_WORKSPACE_ID = "__global__"


class DocumentChunk(BaseModel):
    id: str
    document_id: str
    workspace_id: str = GLOBAL_WORKSPACE_ID
    original_filename: str
    stored_filename: str
    extension: str
    chunk_index: int
    text: str
    character_count: int


class RetrievedChunk(DocumentChunk):
    score: float


class TextChunker:
    def __init__(
        self,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ):
        self.chunk_size = settings.chunk_size if chunk_size is None else chunk_size
        self.chunk_overlap = (
            settings.chunk_overlap if chunk_overlap is None else chunk_overlap
        )
        self._validate_limits()

    def chunk_document(
        self,
        document: ParsedDocumentFile,
        workspace_id: str | None = None,
    ) -> list[DocumentChunk]:
        text_chunks = self.split_text(document.text)
        scope = workspace_id or GLOBAL_WORKSPACE_ID
        return [
            DocumentChunk(
                id=f"{document.id}:{index}",
                document_id=document.id,
                workspace_id=scope,
                original_filename=document.original_filename,
                stored_filename=document.stored_filename,
                extension=document.extension,
                chunk_index=index,
                text=text,
                character_count=len(text),
            )
            for index, text in enumerate(text_chunks)
        ]

    def split_text(self, text: str) -> list[str]:
        normalized_text = re.sub(r"\r\n?", "\n", text).strip()
        if not normalized_text:
            raise ValueError("document text cannot be empty")

        chunks = []
        start = 0
        text_length = len(normalized_text)

        while start < text_length:
            hard_end = min(start + self.chunk_size, text_length)
            end = hard_end

            if hard_end < text_length:
                paragraph_break = normalized_text.rfind("\n\n", start, hard_end + 1)
                minimum_break = start + self.chunk_size // 2
                if paragraph_break >= minimum_break:
                    end = paragraph_break

            chunk = normalized_text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            if end >= text_length:
                break

            next_start = end - self.chunk_overlap
            start = max(next_start, start + 1)

        return chunks

    def _validate_limits(self) -> None:
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        if self.chunk_overlap < 0:
            raise ValueError("chunk_overlap cannot be negative")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
