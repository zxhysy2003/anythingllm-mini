from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import workspaces as workspaces_api
from app.db.session import get_session
from app.main import app
from app.services.document_service import DocumentService
from app.services.workspace_document_service import WorkspaceDocumentService
from app.services.workspace_service import WorkspaceService
from tests.fakes import FakeChatService, FakeRAGService


@pytest.fixture
def workspace_api(tmp_path, monkeypatch, session_override):
    rag = FakeRAGService(indexed_chunk_count=1, deleted_chunk_count=1)
    document_service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    rag.document_service = document_service
    workspace_service = WorkspaceService(
        rag=rag,
        chat=FakeChatService(echo=True),
        documents=document_service,
    )
    workspace_document_service = WorkspaceDocumentService(
        documents=document_service,
        rag=rag,
    )
    app.dependency_overrides[get_session] = session_override
    monkeypatch.setattr(workspaces_api, "workspace_service", workspace_service)
    monkeypatch.setattr(
        workspaces_api,
        "workspace_document_service",
        workspace_document_service,
    )
    client = TestClient(app)
    yield client, rag
    app.dependency_overrides.clear()


def create_workspace(client, **overrides):
    payload = {"name": "Study workspace", **overrides}
    response = client.post("/workspaces", json=payload)
    assert response.status_code == 201
    return response.json()


def test_workspace_conversation_chat_http_flow(workspace_api):
    client, _ = workspace_api
    workspace = create_workspace(
        client,
        system_prompt="Answer concisely.",
        top_k=3,
        similarity_threshold=0.8,
    )

    conversation_response = client.post(f"/workspaces/{workspace['id']}/conversations")
    assert conversation_response.status_code == 201
    conversation = conversation_response.json()

    chat_response = client.post(
        f"/workspaces/{workspace['id']}/conversations/" f"{conversation['id']}/chat",
        json={"message": " hello "},
    )
    messages_response = client.get(
        f"/workspaces/{workspace['id']}/conversations/" f"{conversation['id']}/messages"
    )

    assert chat_response.status_code == 200
    assert chat_response.json() == {
        "conversation_id": conversation["id"],
        "message": "hello",
        "answer": "echo: hello",
        "sources": [],
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
    }
    assert messages_response.status_code == 200
    assert [item["role"] for item in messages_response.json()] == [
        "user",
        "assistant",
    ]
    assert messages_response.json()[1]["provider"] == "deepseek"

    conversations = client.get(f"/workspaces/{workspace['id']}/conversations").json()
    assert conversations[0]["title"] == "hello"


def test_workspace_can_be_listed_and_updated(workspace_api):
    client, _ = workspace_api
    workspace = create_workspace(client)

    update_response = client.patch(
        f"/workspaces/{workspace['id']}",
        json={"name": "Updated", "chat_mode": "query", "history_limit": 4},
    )
    list_response = client.get("/workspaces")

    assert update_response.status_code == 200
    assert update_response.json()["name"] == "Updated"
    assert update_response.json()["chat_mode"] == "query"
    assert update_response.json()["history_limit"] == 4
    assert list_response.json()[0]["id"] == workspace["id"]


def test_workspace_document_upload_is_scoped_and_registered(workspace_api):
    client, rag = workspace_api
    workspace = create_workspace(client)

    response = client.post(
        f"/workspaces/{workspace['id']}/documents/upload",
        files={"file": ("guide.txt", b"hello workspace", "text/plain")},
    )

    assert response.status_code == 201
    document = response.json()
    assert document["workspace_id"] == workspace["id"]
    assert document["chunk_count"] == 1
    assert "upload_path" not in document
    assert "parsed_path" not in document
    assert rag.indexed_workspace_id == workspace["id"]
    upload_path = (
        rag.document_service.upload_dir / document["id"] / document["stored_filename"]
    )
    assert upload_path.read_bytes() == b"hello workspace"

    documents = client.get(f"/workspaces/{workspace['id']}/documents").json()
    assert [item["id"] for item in documents] == [document["id"]]
    assert "upload_path" not in documents[0]
    assert "parsed_path" not in documents[0]


def test_workspace_document_delete_removes_record_files_and_index(workspace_api):
    client, rag = workspace_api
    workspace = create_workspace(client)
    upload_response = client.post(
        f"/workspaces/{workspace['id']}/documents/upload",
        files={"file": ("guide.txt", b"hello workspace", "text/plain")},
    )
    document = upload_response.json()
    upload_path = (
        rag.document_service.upload_dir / document["id"] / document["stored_filename"]
    )
    parsed_path = (
        rag.document_service.parsed_dir
        / document["id"]
        / f"{Path(document['stored_filename']).stem}.txt"
    )

    delete_response = client.delete(
        f"/workspaces/{workspace['id']}/documents/{document['id']}"
    )

    assert delete_response.status_code == 200
    assert delete_response.json() == {
        "id": document["id"],
        "workspace_id": workspace["id"],
        "original_filename": "guide.txt",
        "deleted_chunks": 1,
        "upload_file_deleted": True,
        "parsed_file_deleted": True,
    }
    assert "upload_path" not in delete_response.json()
    assert "parsed_path" not in delete_response.json()
    assert rag.deleted_documents == [(document["id"], workspace["id"])]
    assert not upload_path.exists()
    assert not parsed_path.exists()
    assert client.get(f"/workspaces/{workspace['id']}/documents").json() == []


def test_workspace_delete_removes_documents_conversations_and_returns_counts(
    workspace_api,
):
    client, rag = workspace_api
    workspace = create_workspace(client)
    upload_response = client.post(
        f"/workspaces/{workspace['id']}/documents/upload",
        files={"file": ("guide.txt", b"hello workspace", "text/plain")},
    )
    document = upload_response.json()
    upload_path = (
        rag.document_service.upload_dir / document["id"] / document["stored_filename"]
    )
    parsed_path = (
        rag.document_service.parsed_dir
        / document["id"]
        / f"{Path(document['stored_filename']).stem}.txt"
    )
    conversation_response = client.post(f"/workspaces/{workspace['id']}/conversations")
    conversation = conversation_response.json()
    chat_response = client.post(
        f"/workspaces/{workspace['id']}/conversations/" f"{conversation['id']}/chat",
        json={"message": "hello"},
    )
    assert chat_response.status_code == 200

    delete_response = client.delete(f"/workspaces/{workspace['id']}")

    assert delete_response.status_code == 200
    assert delete_response.json() == {
        "id": workspace["id"],
        "deleted_documents": 1,
        "deleted_conversations": 1,
        "deleted_messages": 2,
        "deleted_chunks": 1,
        "upload_files_deleted": 1,
        "parsed_files_deleted": 1,
    }
    assert "upload_path" not in delete_response.json()
    assert "parsed_path" not in delete_response.json()
    assert rag.deleted_documents == [(document["id"], workspace["id"])]
    assert not upload_path.exists()
    assert not parsed_path.exists()
    assert client.get(f"/workspaces/{workspace['id']}").status_code == 404
    assert client.get(f"/workspaces/{workspace['id']}/documents").status_code == 404
    assert client.get(f"/workspaces/{workspace['id']}/conversations").status_code == 404


def test_workspace_delete_returns_not_found_for_missing_workspace(workspace_api):
    client, _ = workspace_api

    response = client.delete(f"/workspaces/{'f' * 32}")

    assert response.status_code == 404


def test_workspace_document_delete_returns_not_found_for_missing_scope(workspace_api):
    client, rag = workspace_api
    first_workspace = create_workspace(client, name="First")
    second_workspace = create_workspace(client, name="Second")
    upload_response = client.post(
        f"/workspaces/{first_workspace['id']}/documents/upload",
        files={"file": ("guide.txt", b"hello workspace", "text/plain")},
    )
    document = upload_response.json()

    cross_workspace_response = client.delete(
        f"/workspaces/{second_workspace['id']}/documents/{document['id']}"
    )
    missing_workspace_response = client.delete(
        f"/workspaces/{'f' * 32}/documents/{document['id']}"
    )

    assert cross_workspace_response.status_code == 404
    assert missing_workspace_response.status_code == 404
    assert rag.deleted_documents == []


def test_workspace_routes_return_validation_and_not_found_errors(workspace_api):
    client, _ = workspace_api

    invalid_response = client.post(
        "/workspaces",
        json={"name": "   ", "similarity_threshold": 2},
    )
    missing_response = client.get(f"/workspaces/{'f' * 32}")

    assert invalid_response.status_code == 422
    assert missing_response.status_code == 404


def test_workspace_openapi_routes_are_registered():
    app.openapi_schema = None
    schema = app.openapi()

    assert "/workspaces" in schema["paths"]
    assert "delete" in schema["paths"]["/workspaces/{workspace_id}"]
    assert "/workspaces/{workspace_id}/documents/upload" in schema["paths"]
    assert "/workspaces/{workspace_id}/documents/{document_id}" in schema["paths"]
    assert (
        "/workspaces/{workspace_id}/conversations/{conversation_id}/chat"
        in schema["paths"]
    )
    upload = schema["paths"]["/workspaces/{workspace_id}/documents/upload"]["post"]
    assert "multipart/form-data" in upload["requestBody"]["content"]
