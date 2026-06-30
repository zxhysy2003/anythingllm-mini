from fastapi.testclient import TestClient

from app.api import chat as chat_api
from app.main import app
from app.services.chat_service import ChatResult, ChatServiceError

client = TestClient(app)


async def fake_chat(message, system_prompt, history=None, temperature=None):
    return ChatResult(
        message=message,
        answer=f"echo: {message}",
        provider="deepseek",
        model="deepseek-v4-flash",
    )


def test_chat_endpoint_returns_result(monkeypatch):
    monkeypatch.setattr(chat_api.chat_service, "chat", fake_chat)

    response = client.post(
        "/chat",
        json={"message": "  hello  ", "temperature": 0.2},
    )

    assert response.status_code == 200
    assert response.json() == {
        "message": "hello",
        "answer": "echo: hello",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
    }


def test_chat_endpoint_rejects_invalid_temperature():
    response = client.post(
        "/chat",
        json={"message": "hello", "temperature": 3},
    )

    assert response.status_code == 422


def test_chat_endpoint_rejects_blank_message():
    response = client.post("/chat", json={"message": "   "})

    assert response.status_code == 422


def test_chat_endpoint_maps_service_error(monkeypatch):
    async def broken_chat(message, system_prompt, history=None, temperature=None):
        raise ChatServiceError("DeepSeek chat failed: timeout")

    monkeypatch.setattr(chat_api.chat_service, "chat", broken_chat)

    response = client.post("/chat", json={"message": "hello"})

    assert response.status_code == 502
    assert response.json()["detail"] == "DeepSeek chat failed: timeout"


def test_chat_endpoint_schema_has_example():
    app.openapi_schema = None
    schema = app.openapi()

    operation = schema["paths"]["/chat"]["post"]
    assert operation["deprecated"] is True
    assert "Legacy V0" in operation["summary"]
    assert "/workspaces/{workspace_id}/conversations/{conversation_id}/chat" in (
        operation["description"]
    )
    chat_request_schema = schema["components"]["schemas"]["ChatRequest"]
    assert chat_request_schema["examples"][0]["message"]
    assert chat_request_schema["examples"][0]["temperature"] == 0.7
