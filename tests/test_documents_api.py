import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import documents as documents_api
from app.core.rag import TextChunker
from app.core.vectorstore import ChromaVectorStore
from app.main import app
from app.services.document_service import DocumentService
from app.services.rag_service import IndexedDocument, RAGIndexError, RAGService

client = TestClient(app)


class FakeRAGService:
    async def index_document(self, parsed_file):
        self.parsed_file = parsed_file
        return IndexedDocument(document_id=parsed_file.id, chunk_count=1)


@pytest.fixture
def temp_document_service(tmp_path, monkeypatch):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    monkeypatch.setattr(documents_api, "document_service", service)
    monkeypatch.setattr(documents_api, "rag_service", FakeRAGService())
    return service


def test_upload_document_saves_and_parses_txt(temp_document_service):
    response = client.post(
        "/documents/upload",
        files={"file": ("notes.txt", b"hello\nworld", "text/plain")},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["original_filename"] == "notes.txt"
    assert result["extension"] == ".txt"
    assert result["size_bytes"] == len(b"hello\nworld")
    assert result["character_count"] == len("hello\nworld")
    assert result["chunk_count"] == 1
    assert "text" not in result
    assert Path(result["upload_path"]).read_bytes() == b"hello\nworld"
    assert Path(result["parsed_path"]).read_text(encoding="utf-8") == "hello\nworld"


def test_upload_document_indexes_parsed_text_in_chroma(tmp_path, monkeypatch):
    class FakeEmbeddings:
        async def embed_documents(self, texts):
            self.texts = texts
            return [[1.0, float(index)] for index, _ in enumerate(texts)]

    document_service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    embeddings = FakeEmbeddings()
    store = ChromaVectorStore(
        persist_dir=tmp_path / "chroma",
        collection_name="upload-integration",
        embedding_model_name="fake-embedding-model",
    )
    rag_service = RAGService(
        chunker=TextChunker(chunk_size=10, chunk_overlap=2),
        embeddings=embeddings,
        store=store,
    )
    monkeypatch.setattr(documents_api, "document_service", document_service)
    monkeypatch.setattr(documents_api, "rag_service", rag_service)

    response = client.post(
        "/documents/upload",
        files={"file": ("notes.txt", b"ABCDEFGHIJKLMNO", "text/plain")},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["chunk_count"] == 2
    assert embeddings.texts == ["ABCDEFGHIJ", "IJKLMNO"]
    assert asyncio.run(store.count()) == 2

    indexed_chunks = asyncio.run(
        store.query(
            [1.0, 0.0],
            top_k=5,
            similarity_threshold=0.0,
        )
    )
    assert len(indexed_chunks) == 2
    assert {chunk.document_id for chunk in indexed_chunks} == {result["id"]}


def test_upload_document_preserves_files_when_indexing_fails(
    temp_document_service,
    monkeypatch,
):
    class FailingRAGService:
        async def index_document(self, parsed_file):
            raise RAGIndexError("failed to index document: notes.txt")

    monkeypatch.setattr(documents_api, "rag_service", FailingRAGService())

    response = client.post(
        "/documents/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "failed to index document: notes.txt"
    assert len(list(temp_document_service.upload_dir.rglob("*.txt"))) == 1
    assert len(list(temp_document_service.parsed_dir.rglob("*.txt"))) == 1


def test_upload_document_rejects_unsupported_extension(temp_document_service):
    response = client.post(
        "/documents/upload",
        files={"file": ("image.png", b"not supported", "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unsupported file extension: .png"
    assert not any(temp_document_service.upload_dir.rglob("*"))


def test_upload_document_rejects_empty_file(temp_document_service):
    response = client.post(
        "/documents/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "file cannot be empty"
    assert not any(temp_document_service.upload_dir.rglob("*"))


def test_upload_document_preserves_corrupt_source_file(temp_document_service):
    response = client.post(
        "/documents/upload",
        files={
            "file": (
                "broken.docx",
                b"not a docx archive",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "failed to parse document: broken.docx"
    uploaded_files = [
        path for path in temp_document_service.upload_dir.rglob("*") if path.is_file()
    ]
    assert len(uploaded_files) == 1
    assert uploaded_files[0].read_bytes() == b"not a docx archive"
    assert not temp_document_service.parsed_dir.exists()


def test_upload_document_requires_multipart_file():
    response = client.post("/documents/upload")

    assert response.status_code == 422


def test_upload_document_openapi_schema_uses_multipart():
    schema = app.openapi()
    upload_operation = schema["paths"]["/documents/upload"]["post"]

    assert "multipart/form-data" in upload_operation["requestBody"]["content"]
    assert "201" in upload_operation["responses"]
