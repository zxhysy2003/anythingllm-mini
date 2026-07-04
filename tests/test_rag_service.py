import asyncio
import logging
from pathlib import Path

import pytest

from app.services import rag_service as rag_service_module
from app.core.rag import RetrievedChunk, TextChunker
from app.services.document_service import ParsedDocumentFile
from app.services.rag_service import RAGQueryError, RAGService

WORKSPACE_ID = "1" * 32


class FakeEmbeddings:
    def __init__(self):
        self.document_texts = None
        self.query_text = None

    async def embed_documents(self, texts):
        self.document_texts = texts
        return [[float(index), 1.0] for index, _ in enumerate(texts)]

    async def embed_query(self, text):
        self.query_text = text
        return [1.0, 0.0]


class FakeStore:
    def __init__(self, results=None, has_documents=True):
        self.results = results or []
        self.has_document_records = has_documents
        self.has_document_calls = []
        self.deleted_documents = []

    async def upsert_chunks(self, chunks, embeddings):
        self.chunks = chunks
        self.embeddings = embeddings
        return len(chunks)

    async def has_documents(self, workspace_id):
        self.has_document_calls.append(workspace_id)
        return self.has_document_records

    async def query(self, query_embedding, top_k, similarity_threshold, workspace_id):
        self.query_embedding = query_embedding
        self.top_k = top_k
        self.similarity_threshold = similarity_threshold
        self.workspace_id = workspace_id
        return self.results

    async def delete_document(self, document_id, workspace_id):
        self.deleted_documents.append((document_id, workspace_id))
        return 1


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


def retrieved_chunk(
    text: str = "AnythingLLM Mini uses a RAG pipeline.",
    *,
    workspace_id: str = WORKSPACE_ID,
    chunk_index: int = 0,
    score: float = 0.95,
) -> RetrievedChunk:
    return RetrievedChunk(
        id=f"{'a' * 32}:{chunk_index}",
        document_id="a" * 32,
        workspace_id=workspace_id,
        original_filename="guide.txt",
        stored_filename="guide.txt",
        extension=".txt",
        chunk_index=chunk_index,
        text=text,
        character_count=len(text),
        score=score,
    )


def test_rag_service_indexes_parsed_document_in_workspace():
    embeddings = FakeEmbeddings()
    store = FakeStore()
    service = RAGService(
        chunker=TextChunker(chunk_size=20, chunk_overlap=5),
        embeddings=embeddings,
        store=store,
    )

    result = asyncio.run(
        service.index_document(
            parsed_document("A" * 30),
            workspace_id=WORKSPACE_ID,
        )
    )

    assert result.document_id == "a" * 32
    assert result.chunk_count == 2
    assert embeddings.document_texts == [chunk.text for chunk in store.chunks]
    assert {chunk.workspace_id for chunk in store.chunks} == {WORKSPACE_ID}
    assert len(store.embeddings) == 2


def test_rag_retrieve_uses_requested_workspace_scope(caplog):
    caplog.set_level(logging.INFO)
    store = FakeStore(results=[retrieved_chunk()])
    service = RAGService(embeddings=FakeEmbeddings(), store=store)

    results = asyncio.run(
        service.retrieve(
            "question",
            workspace_id=WORKSPACE_ID,
            top_k=3,
            similarity_threshold=0.8,
        )
    )

    assert results == [retrieved_chunk()]
    assert store.has_document_calls == [WORKSPACE_ID]
    assert store.workspace_id == WORKSPACE_ID
    assert store.top_k == 3
    assert store.similarity_threshold == 0.8
    assert "rag.retrieve.completed" in [record.message for record in caplog.records]


def test_rag_retrieve_without_documents_skips_embedding():
    embeddings = FakeEmbeddings()
    store = FakeStore(has_documents=False)
    service = RAGService(embeddings=embeddings, store=store)

    result = asyncio.run(service.retrieve("question", workspace_id=WORKSPACE_ID))

    assert result == []
    assert store.has_document_calls == [WORKSPACE_ID]
    assert embeddings.query_text is None


def test_rag_retrieve_without_relevant_chunks_returns_empty():
    embeddings = FakeEmbeddings()
    store = FakeStore(results=[], has_documents=True)
    service = RAGService(embeddings=embeddings, store=store)

    result = asyncio.run(
        service.retrieve("unrelated question", workspace_id=WORKSPACE_ID)
    )

    assert result == []
    assert embeddings.query_text == "unrelated question"
    assert store.workspace_id == WORKSPACE_ID
    assert store.similarity_threshold == 0.75


def test_rag_context_budget_keeps_only_complete_prefix_sources():
    service = RAGService(embeddings=FakeEmbeddings(), store=FakeStore())
    first_chunk = retrieved_chunk("first context", chunk_index=0, score=0.99)
    second_chunk = retrieved_chunk("second context", chunk_index=1, score=0.98)
    first_only_budget = service.build_context_prompt(
        [first_chunk],
        max_context_chars=1000,
    ).context_char_count

    result = service.build_context_prompt(
        [first_chunk, second_chunk],
        max_context_chars=first_only_budget,
    )

    assert result.chunks == [first_chunk]
    assert [source.text for source in result.sources] == ["first context"]
    assert result.retrieved_count == 2
    assert result.dropped_count == 1
    assert result.context_char_count == first_only_budget
    assert "[SOURCE 1]" in result.system_prompt
    assert "first context" in result.system_prompt
    assert "second context" not in result.system_prompt


def test_build_context_prompt_returns_only_sources_used_in_budget(monkeypatch):
    first_chunk = retrieved_chunk("first context", chunk_index=0, score=0.99)
    second_chunk = retrieved_chunk("second context", chunk_index=1, score=0.98)
    service = RAGService(embeddings=FakeEmbeddings(), store=FakeStore())
    first_only_budget = service.build_context_prompt(
        [first_chunk],
        max_context_chars=1000,
    ).context_char_count
    monkeypatch.setattr(
        rag_service_module.settings,
        "max_context_chars",
        first_only_budget,
    )

    result = service.build_context_prompt([first_chunk, second_chunk])

    assert [source.text for source in result.sources] == ["first context"]
    assert "first context" in result.system_prompt
    assert "second context" not in result.system_prompt


def test_build_system_prompt_uses_context_budget(monkeypatch):
    service = RAGService(embeddings=FakeEmbeddings(), store=FakeStore())
    first_chunk = retrieved_chunk("first context", chunk_index=0, score=0.99)
    second_chunk = retrieved_chunk("second context", chunk_index=1, score=0.98)
    first_only_budget = service.build_context_prompt(
        [first_chunk],
        max_context_chars=1000,
    ).context_char_count
    monkeypatch.setattr(
        rag_service_module.settings,
        "max_context_chars",
        first_only_budget,
    )

    system_prompt = service.build_system_prompt([first_chunk, second_chunk])

    assert "first context" in system_prompt
    assert "second context" not in system_prompt


def test_rag_delete_document_uses_workspace_scope():
    store = FakeStore()
    service = RAGService(embeddings=FakeEmbeddings(), store=store)

    deleted_count = asyncio.run(
        service.delete_document("a" * 32, workspace_id=WORKSPACE_ID)
    )

    assert deleted_count == 1
    assert store.deleted_documents == [("a" * 32, WORKSPACE_ID)]


def test_rag_retrieve_wraps_retrieval_failure():
    class BrokenStore(FakeStore):
        async def has_documents(self, workspace_id):
            raise RuntimeError("Chroma unavailable")

    service = RAGService(embeddings=FakeEmbeddings(), store=BrokenStore())

    with pytest.raises(RAGQueryError, match="retrieve document context"):
        asyncio.run(service.retrieve("question", workspace_id=WORKSPACE_ID))
