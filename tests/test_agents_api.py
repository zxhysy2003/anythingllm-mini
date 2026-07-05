import pytest
from fastapi.testclient import TestClient

from app.api import agents as agents_api
from app.api import workspaces as workspaces_api
from app.db.session import get_session
from app.main import app
from app.services.agent_service import AgentService
from app.services.chat_service import ChatResult
from app.services.document_service import DocumentService
from app.services.workspace_document_service import WorkspaceDocumentService
from app.services.workspace_service import WorkspaceService
from app.tools.calculator import CalculatorTool
from app.tools.document_tools import WorkspaceDocumentSearchTool
from app.tools.registry import ToolRegistry
from tests.fakes import FakeChatService, FakeRAGService


class ScriptedAgentChat:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = []

    async def chat(self, message, system_prompt, history, temperature):
        self.calls.append(
            {
                "message": message,
                "system_prompt": system_prompt,
                "history": history,
                "temperature": temperature,
            }
        )
        response_index = len(self.calls) - 1
        return ChatResult(
            message=message,
            answer=self.responses[response_index],
            provider="fake",
            model="fake-agent-model",
        )


@pytest.fixture
def agent_api(tmp_path, monkeypatch, session_override):
    def make_client(responses, *, registry=None):
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
        if registry is None:
            registry = ToolRegistry()
            registry.register(CalculatorTool())
            registry.register(WorkspaceDocumentSearchTool(rag=rag))
        agent_chat = ScriptedAgentChat(responses)
        agent_service = AgentService(
            workspace=workspace_service,
            chat=agent_chat,
            tool_registry=registry,
        )
        app.dependency_overrides[get_session] = session_override
        monkeypatch.setattr(workspaces_api, "workspace_service", workspace_service)
        monkeypatch.setattr(
            workspaces_api,
            "workspace_document_service",
            workspace_document_service,
        )
        monkeypatch.setattr(agents_api, "agent_service", agent_service)
        return TestClient(app), agent_chat

    yield make_client
    app.dependency_overrides.clear()


def create_workspace(client, **overrides):
    payload = {"name": "Agent workspace", **overrides}
    response = client.post("/workspaces", json=payload)
    assert response.status_code == 201
    return response.json()


def create_conversation(client, workspace_id):
    response = client.post(f"/workspaces/{workspace_id}/conversations")
    assert response.status_code == 201
    return response.json()


def test_agent_endpoint_runs_agent_loop(agent_api):
    client, agent_chat = agent_api(
        [
            'Action: calculator\nAction Input: {"expression": "1 + 2 * 3"}',
            "Final Answer: the result is 7",
        ]
    )
    workspace = create_workspace(client, system_prompt="Answer through tools.")
    conversation = create_conversation(client, workspace["id"])

    response = client.post(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/agent",
        json={"message": " calculate ", "max_steps": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == conversation["id"]
    assert payload["agent_invocation_id"]
    assert payload["message"] == "calculate"
    assert payload["answer"] == "the result is 7"
    assert payload["provider"] == "fake"
    assert payload["model"] == "fake-agent-model"
    assert payload["steps"][0]["action"] == "calculator"
    assert payload["steps"][0]["observation"] == "7"
    assert payload["metrics"]["max_steps"] == 5
    assert payload["metrics"]["llm_call_count"] == 2
    assert payload["metrics"]["step_count"] == 1
    assert payload["metrics"]["tool_call_count"] == 1
    assert payload["metrics"]["failed_step_count"] == 0
    assert agent_chat.calls[0]["temperature"] == workspace["temperature"]

    messages_response = client.get(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/messages"
    )
    assert messages_response.status_code == 200
    messages = messages_response.json()
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "calculate"
    assert messages[1]["content"] == "the result is 7"
    assert messages[1]["provider"] == "fake"
    assert messages[1]["model"] == "fake-agent-model"
    assert messages[1]["metrics"]["agent_mode"] == "react_text"
    assert (
        messages[1]["metrics"]["agent_invocation_id"] == payload["agent_invocation_id"]
    )
    assert "agent_steps" not in messages[1]["metrics"]
    assert messages[1]["metrics"]["tool_call_count"] == 1

    invocation_response = client.get(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}"
        f"/agent-invocations/{payload['agent_invocation_id']}"
    )
    assert invocation_response.status_code == 200
    invocation = invocation_response.json()
    assert invocation["id"] == payload["agent_invocation_id"]
    assert invocation["assistant_message_id"] == messages[1]["id"]
    assert invocation["status"] == "completed"
    assert invocation["steps"] == payload["steps"]


@pytest.mark.parametrize("max_steps", [0, 11])
def test_agent_endpoint_validates_max_steps(agent_api, max_steps):
    client, _ = agent_api(["Final Answer: unused"])
    workspace = create_workspace(client)
    conversation = create_conversation(client, workspace["id"])

    response = client.post(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/agent",
        json={"message": "hello", "max_steps": max_steps},
    )

    assert response.status_code == 422


def test_agent_invocation_endpoint_rejects_wrong_conversation(agent_api):
    client, _ = agent_api(["Final Answer: answer"])
    workspace = create_workspace(client)
    conversation = create_conversation(client, workspace["id"])
    response = client.post(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/agent",
        json={"message": "hello"},
    )
    assert response.status_code == 200
    payload = response.json()
    other_conversation = create_conversation(client, workspace["id"])

    invocation_response = client.get(
        f"/workspaces/{workspace['id']}/conversations/{other_conversation['id']}"
        f"/agent-invocations/{payload['agent_invocation_id']}"
    )

    assert invocation_response.status_code == 404


def test_agent_endpoint_returns_404_for_missing_workspace(agent_api):
    client, _ = agent_api(["Final Answer: unused"])

    response = client.post(
        f"/workspaces/{'f' * 32}/conversations/{'c' * 32}/agent",
        json={"message": "hello"},
    )

    assert response.status_code == 404


def test_agent_endpoint_returns_404_for_conversation_outside_workspace(agent_api):
    client, _ = agent_api(["Final Answer: unused"])
    first_workspace = create_workspace(client, name="First")
    second_workspace = create_workspace(client, name="Second")
    conversation = create_conversation(client, first_workspace["id"])

    response = client.post(
        (
            f"/workspaces/{second_workspace['id']}/conversations/"
            f"{conversation['id']}/agent"
        ),
        json={"message": "hello"},
    )

    assert response.status_code == 404


def test_agent_endpoint_returns_tool_failure_as_step(agent_api):
    client, _ = agent_api(
        [
            'Action: missing_tool\nAction Input: {"value": "x"}',
            "Final Answer: I cannot use that tool.",
        ]
    )
    workspace = create_workspace(client)
    conversation = create_conversation(client, workspace["id"])

    response = client.post(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/agent",
        json={"message": "use a missing tool"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "I cannot use that tool."
    assert payload["steps"][0]["ok"] is False
    assert payload["steps"][0]["error"] == "unknown_tool"
    assert payload["steps"][0]["tool_result"]["error"] == "unknown_tool"
    assert payload["metrics"]["failed_step_count"] == 1

    messages = client.get(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/messages"
    ).json()
    assert "agent_steps" not in messages[1]["metrics"]

    invocation_response = client.get(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}"
        f"/agent-invocations/{payload['agent_invocation_id']}"
    )
    assert invocation_response.status_code == 200
    invocation = invocation_response.json()
    assert invocation["steps"][0]["ok"] is False
    assert invocation["steps"][0]["error"] == "unknown_tool"


def test_workspace_chat_endpoint_still_behaves_normally(agent_api):
    client, _ = agent_api(["Final Answer: unused"])
    workspace = create_workspace(client)
    conversation = create_conversation(client, workspace["id"])

    response = client.post(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/chat",
        json={"message": " hello "},
    )
    messages_response = client.get(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/messages"
    )

    assert response.status_code == 200
    assert response.json()["message"] == "hello"
    assert response.json()["answer"] == "echo: hello"
    assert [item["role"] for item in messages_response.json()] == [
        "user",
        "assistant",
    ]


def test_agent_openapi_route_documents_agent_endpoint():
    app.openapi_schema = None
    schema = app.openapi()

    operation = schema["paths"][
        "/workspaces/{workspace_id}/conversations/{conversation_id}/agent"
    ]["post"]
    assert operation["summary"] == "Workspace agent loop"
    assert "minimal ReAct text agent loop" in operation["description"]
    assert "separate agent invocation record" in operation["description"]

    invocation_operation = schema["paths"][
        "/workspaces/{workspace_id}/conversations/{conversation_id}"
        "/agent-invocations/{invocation_id}"
    ]["get"]
    assert invocation_operation["summary"] == "Read workspace agent invocation"
