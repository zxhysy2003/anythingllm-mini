from fastapi.testclient import TestClient

from app.api import rag as rag_api
from app.main import app
from app.services.chat_service import ChatServiceError
from app.services.rag_service import RAGQueryError, RAGQueryResult, RAGSource

client = TestClient(app)


class FakeRAGService:
    async def query(self, question):
        return RAGQueryResult(
            question=question,
            answer="The indexed document answer.",
            sources=[
                RAGSource(
                    document_id="a" * 32,
                    original_filename="guide.txt",
                    chunk_index=0,
                    text="Relevant document context.",
                    score=0.92,
                )
            ],
        )


def test_rag_query_endpoint_returns_answer_and_sources(monkeypatch):
    monkeypatch.setattr(rag_api, "rag_service", FakeRAGService())

    response = client.post("/rag/query", json={"question": " What is covered? "})

    assert response.status_code == 200
    assert response.json() == {
        "question": "What is covered?",
        "answer": "The indexed document answer.",
        "sources": [
            {
                "document_id": "a" * 32,
                "original_filename": "guide.txt",
                "chunk_index": 0,
                "text": "Relevant document context.",
                "score": 0.92,
            }
        ],
    }


def test_rag_query_endpoint_rejects_blank_question():
    response = client.post("/rag/query", json={"question": "   "})

    assert response.status_code == 422


def test_rag_query_endpoint_maps_chat_failure(monkeypatch):
    class BrokenRAGService:
        async def query(self, question):
            raise ChatServiceError("DeepSeek chat failed: timeout")

    monkeypatch.setattr(rag_api, "rag_service", BrokenRAGService())

    response = client.post("/rag/query", json={"question": "question"})

    assert response.status_code == 502


def test_rag_query_endpoint_maps_retrieval_failure(monkeypatch):
    class BrokenRAGService:
        async def query(self, question):
            raise RAGQueryError("failed to retrieve document context")

    monkeypatch.setattr(rag_api, "rag_service", BrokenRAGService())

    response = client.post("/rag/query", json={"question": "question"})

    assert response.status_code == 500


def test_rag_query_openapi_schema_is_registered():
    app.openapi_schema = None
    schema = app.openapi()
    operation = schema["paths"]["/rag/query"]["post"]

    assert operation["tags"] == ["rag"]
    assert operation["deprecated"] is True
    assert "Legacy V2" in operation["summary"]
    assert "/workspaces/{workspace_id}/conversations/{conversation_id}/chat" in (
        operation["description"]
    )
    assert "RAGQueryRequest" in str(operation["requestBody"])
    assert "200" in operation["responses"]
