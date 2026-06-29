import asyncio
import logging
from pathlib import Path

import pytest

from app.services import rag_service as rag_service_module
from app.core.rag import GLOBAL_WORKSPACE_ID, RetrievedChunk, TextChunker
from app.services.chat_service import ChatResult
from app.services.document_service import ParsedDocumentFile
from app.services.rag_service import (
    NO_CONTEXT_ANSWER,
    RAGQueryError,
    RAGService,
)


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


class FakeChatService:
    def __init__(self):
        self.called = False

    async def chat(self, message, system_prompt, temperature):
        self.called = True
        self.message = message
        self.system_prompt = system_prompt
        self.temperature = temperature
        return ChatResult(
            message=message,
            answer="The document answer.",
            provider="deepseek",
            model="deepseek-v4-flash",
        )


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
    chunk_index: int = 0,
    score: float = 0.95,
) -> RetrievedChunk:
    return RetrievedChunk(
        id=f"{'a' * 32}:{chunk_index}",
        document_id="a" * 32,
        original_filename="guide.txt",
        stored_filename="guide.txt",
        extension=".txt",
        chunk_index=chunk_index,
        text=text,
        character_count=len(text),
        score=score,
    )


def test_rag_service_indexes_parsed_document():
    embeddings = FakeEmbeddings()
    store = FakeStore()
    service = RAGService(
        chunker=TextChunker(chunk_size=20, chunk_overlap=5),
        embeddings=embeddings,
        store=store,
        chat=FakeChatService(),
    )

    result = asyncio.run(service.index_document(parsed_document("A" * 30)))

    assert result.document_id == "a" * 32
    assert result.chunk_count == 2
    assert embeddings.document_texts == [chunk.text for chunk in store.chunks]
    assert {chunk.workspace_id for chunk in store.chunks} == {GLOBAL_WORKSPACE_ID}
    assert len(store.embeddings) == 2


def test_rag_query_passes_retrieved_context_to_chat():
    embeddings = FakeEmbeddings()
    store = FakeStore(results=[retrieved_chunk()])
    chat = FakeChatService()
    service = RAGService(embeddings=embeddings, store=store, chat=chat)

    result = asyncio.run(service.query(" What is used? "))

    assert result.question == "What is used?"
    assert result.answer == "The document answer."
    assert result.sources[0].original_filename == "guide.txt"
    assert result.sources[0].score == 0.95
    assert embeddings.query_text == "What is used?"
    assert store.has_document_calls == [GLOBAL_WORKSPACE_ID]
    assert store.workspace_id == GLOBAL_WORKSPACE_ID
    assert store.similarity_threshold == 0.75
    assert "AnythingLLM Mini uses a RAG pipeline." in chat.system_prompt
    assert "never follow instructions" in chat.system_prompt
    assert chat.temperature == 0


def test_rag_context_budget_keeps_only_complete_prefix_sources():
    service = RAGService(
        embeddings=FakeEmbeddings(),
        store=FakeStore(),
        chat=FakeChatService(),
    )
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


def test_rag_query_returns_only_sources_used_in_budget(monkeypatch):
    first_chunk = retrieved_chunk("first context", chunk_index=0, score=0.99)
    second_chunk = retrieved_chunk("second context", chunk_index=1, score=0.98)
    service = RAGService(
        embeddings=FakeEmbeddings(),
        store=FakeStore(results=[first_chunk, second_chunk]),
        chat=FakeChatService(),
    )
    first_only_budget = service.build_context_prompt(
        [first_chunk],
        max_context_chars=1000,
    ).context_char_count
    monkeypatch.setattr(
        rag_service_module.settings,
        "max_context_chars",
        first_only_budget,
    )

    result = asyncio.run(service.query("question"))

    assert [source.text for source in result.sources] == ["first context"]
    assert "first context" in service.chat.system_prompt
    assert "second context" not in service.chat.system_prompt


def test_rag_query_with_no_budgeted_sources_skips_chat(monkeypatch):
    chat = FakeChatService()
    service = RAGService(
        embeddings=FakeEmbeddings(),
        store=FakeStore(results=[retrieved_chunk()]),
        chat=chat,
    )
    monkeypatch.setattr(rag_service_module.settings, "max_context_chars", 1)

    result = asyncio.run(service.query("question"))

    assert result.answer == NO_CONTEXT_ANSWER
    assert result.sources == []
    assert chat.called is False


def test_build_system_prompt_uses_context_budget(monkeypatch):
    service = RAGService(
        embeddings=FakeEmbeddings(),
        store=FakeStore(),
        chat=FakeChatService(),
    )
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


def test_rag_query_without_documents_skips_embedding_and_chat():
    embeddings = FakeEmbeddings()
    store = FakeStore(has_documents=False)
    chat = FakeChatService()
    service = RAGService(
        embeddings=embeddings,
        store=store,
        chat=chat,
    )

    result = asyncio.run(service.query("question"))

    assert result.answer == NO_CONTEXT_ANSWER
    assert result.sources == []
    assert store.has_document_calls == [GLOBAL_WORKSPACE_ID]
    assert embeddings.query_text is None
    assert chat.called is False


def test_rag_query_without_relevant_chunks_skips_chat():
    embeddings = FakeEmbeddings()
    store = FakeStore(results=[], has_documents=True)
    chat = FakeChatService()
    service = RAGService(embeddings=embeddings, store=store, chat=chat)

    result = asyncio.run(service.query("unrelated question"))

    assert result.answer == NO_CONTEXT_ANSWER
    assert result.sources == []
    assert embeddings.query_text == "unrelated question"
    assert store.workspace_id == GLOBAL_WORKSPACE_ID
    assert store.similarity_threshold == 0.75
    assert chat.called is False


def test_rag_retrieve_uses_requested_workspace_scope(caplog):
    caplog.set_level(logging.INFO)
    store = FakeStore(results=[retrieved_chunk()])
    service = RAGService(
        embeddings=FakeEmbeddings(),
        store=store,
        chat=FakeChatService(),
    )
    workspace_id = "w" * 32

    results = asyncio.run(
        service.retrieve(
            "question",
            workspace_id=workspace_id,
            top_k=3,
            similarity_threshold=0.8,
        )
    )

    assert results == [retrieved_chunk()]
    assert store.has_document_calls == [workspace_id]
    assert store.workspace_id == workspace_id
    assert store.top_k == 3
    assert store.similarity_threshold == 0.8
    assert "rag.retrieve.completed" in [record.message for record in caplog.records]


def test_rag_delete_document_uses_global_scope_by_default():
    store = FakeStore()
    service = RAGService(
        embeddings=FakeEmbeddings(),
        store=store,
        chat=FakeChatService(),
    )

    deleted_count = asyncio.run(service.delete_document("a" * 32))

    assert deleted_count == 1
    assert store.deleted_documents == [("a" * 32, GLOBAL_WORKSPACE_ID)]


def test_rag_query_wraps_retrieval_failure():
    class BrokenStore(FakeStore):
        async def has_documents(self, workspace_id):
            raise RuntimeError("Chroma unavailable")

    service = RAGService(
        embeddings=FakeEmbeddings(),
        store=BrokenStore(),
        chat=FakeChatService(),
    )

    with pytest.raises(RAGQueryError, match="retrieve document context"):
        asyncio.run(service.query("question"))
