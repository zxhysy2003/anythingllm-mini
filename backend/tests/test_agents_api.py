import json

import pytest
from fastapi.testclient import TestClient

from app.api import agents as agents_api
from app.api import workspaces as workspaces_api
from app.core.agent_modes import AGENT_MODE_REACT_TEXT
from app.core.llm import DeepSeekToolCall, DeepSeekToolCallResult
from app.core.native_tool_calling import DeepSeekNativeToolCallingExecutor
from app.db.session import get_session
from app.main import app
from app.services.agent_service import AgentService
from app.services.chat_service import ChatResult
from app.services.document_service import DocumentService
from app.services.workspace_document_service import WorkspaceDocumentService
from app.services.workspace_service import WorkspaceService
from app.tools.calculator import CalculatorTool
from app.tools.document_tools import WorkspaceDocumentSearchTool
from app.tools.registry import (
    TOOL_CONFIRMATION_REQUIRED,
    ToolContext,
    ToolRegistry,
    build_tool_approval_id,
)
from tests.fakes import ConfirmationRequiredTool, FakeChatService, FakeRAGService


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


class ScriptedToolCallingClient:
    def __init__(self, responses: list[DeepSeekToolCallResult]):
        self.responses = responses
        self.calls = []

    async def chat_with_tools(self, messages, tools, temperature, tool_choice):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "temperature": temperature,
                "tool_choice": tool_choice,
            }
        )
        return self.responses[len(self.calls) - 1]


def native_tool_result(content, *, tool_calls=None, finish_reason="stop"):
    return DeepSeekToolCallResult(
        content=content,
        tool_calls=tool_calls or [],
        finish_reason=finish_reason,
        provider="fake",
        model="fake-native-model",
    )


def native_tool_call(name, arguments, *, call_id="call-1"):
    return DeepSeekToolCall(
        id=call_id,
        name=name,
        arguments=arguments,
    )


def parse_sse_events(text: str) -> list[dict]:
    events = []
    for block in text.strip().split("\n\n"):
        if not block:
            continue
        event_name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            if line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        events.append({"event": event_name, "data": data})
    return events


@pytest.fixture
def agent_api(tmp_path, monkeypatch, session_override):
    def make_client(responses, *, registry=None, native_agent_executor=None):
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
            native_agent_executor=native_agent_executor,
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
    assert payload["steps"][0]["tool_result"]["artifacts"] == {
        "sources": [],
        "outputs": {"result": 7},
    }
    assert "data" not in payload["steps"][0]["tool_result"]
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
    assert invocation["agent_mode"] == "react_text"
    assert invocation["status"] == "completed"
    assert invocation["steps"] == payload["steps"]


def test_agent_stream_endpoint_streams_agent_events(agent_api):
    client, _ = agent_api(
        [
            'Action: calculator\nAction Input: {"expression": "1 + 2 * 3"}',
            "Final Answer: the result is 7",
        ]
    )
    workspace = create_workspace(client, system_prompt="Answer through tools.")
    conversation = create_conversation(client, workspace["id"])

    with client.stream(
        "POST",
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}"
        "/agent/stream",
        json={"message": "calculate", "max_steps": 5},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse_events(body)
    event_names = [event["event"] for event in events]
    assert event_names[0] == "agent_started"
    assert "tool_finished" in event_names
    assert event_names[-1] == "agent_finished"

    finished = events[-1]["data"]
    assert finished["type"] == "agent_finished"
    payload = finished["payload"]
    assert payload["answer"] == "the result is 7"
    assert payload["agent_invocation_id"]
    assert payload["steps"][0]["action"] == "calculator"
    assert payload["metrics"]["tool_call_count"] == 1


def test_agent_endpoint_accepts_approved_tool_call_ids(agent_api):
    tool = ConfirmationRequiredTool()
    registry = ToolRegistry()
    registry.register(tool)
    client, _ = agent_api(
        [
            'Action: confirmation_required\nAction Input: {"value": "hello"}',
            "Final Answer: approved",
        ],
        registry=registry,
    )
    workspace = create_workspace(client)
    conversation = create_conversation(client, workspace["id"])
    approval_id = build_tool_approval_id(
        tool_name=tool.name,
        action_input={"value": "hello"},
        context=ToolContext(
            workspace_id=workspace["id"],
            conversation_id=conversation["id"],
            agent_mode=AGENT_MODE_REACT_TEXT,
        ),
    )

    response = client.post(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/agent",
        json={
            "message": "use restricted tool",
            "approved_tool_call_ids": [approval_id],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "approved"
    assert payload["steps"][0]["ok"] is True
    assert payload["steps"][0]["observation"] == "approved hello"
    assert payload["metrics"]["failed_step_count"] == 0
    assert tool.executed is True


def test_agent_stream_endpoint_streams_policy_blocked_step(agent_api):
    tool = ConfirmationRequiredTool()
    registry = ToolRegistry()
    registry.register(tool)
    client, _ = agent_api(
        [
            'Action: confirmation_required\nAction Input: {"value": "hello"}',
            "Final Answer: waiting for approval",
        ],
        registry=registry,
    )
    workspace = create_workspace(client)
    conversation = create_conversation(client, workspace["id"])

    with client.stream(
        "POST",
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}"
        "/agent/stream",
        json={"message": "use restricted tool"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    events = parse_sse_events(body)
    tool_finished = [event for event in events if event["event"] == "tool_finished"][0][
        "data"
    ]
    step = tool_finished["payload"]["step"]
    assert step["ok"] is False
    assert step["error"] == TOOL_CONFIRMATION_REQUIRED
    assert step["tool_result"]["error_details"]["approval_id"].startswith(
        "tool_approval_"
    )
    assert "data" not in step["tool_result"]
    assert tool.executed is False

    finished = events[-1]["data"]
    payload = finished["payload"]
    assert payload["answer"] == "waiting for approval"
    assert payload["steps"][0]["error"] == TOOL_CONFIRMATION_REQUIRED
    assert payload["metrics"]["failed_step_count"] == 1


def test_agent_endpoint_runs_native_tool_calling_mode(agent_api):
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    native_client = ScriptedToolCallingClient(
        [
            native_tool_result(
                "",
                tool_calls=[
                    native_tool_call("calculator", '{"expression": "1 + 2 * 3"}')
                ],
                finish_reason="tool_calls",
            ),
            native_tool_result("the result is 7"),
        ]
    )
    native_executor = DeepSeekNativeToolCallingExecutor(
        llm=native_client,
        tool_registry=registry,
    )
    client, _ = agent_api(
        ["Final Answer: unused"],
        registry=registry,
        native_agent_executor=native_executor,
    )
    workspace = create_workspace(client, system_prompt="Answer through tools.")
    conversation = create_conversation(client, workspace["id"])

    response = client.post(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/agent",
        json={
            "message": "calculate",
            "max_steps": 5,
            "agent_mode": "native_tool_calling",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "agent_mode" not in payload
    assert payload["answer"] == "the result is 7"
    assert payload["provider"] == "fake"
    assert payload["model"] == "fake-native-model"
    assert payload["steps"][0]["action"] == "calculator"
    assert payload["steps"][0]["tool_result"]["artifacts"] == {
        "sources": [],
        "outputs": {"result": 7},
    }
    assert payload["steps"][0]["tool_result"]["error_details"] == {}
    assert "data" not in payload["steps"][0]["tool_result"]
    assert payload["metrics"]["llm_call_count"] == 2
    assert payload["metrics"]["tool_call_count"] == 1

    messages_response = client.get(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/messages"
    )
    messages = messages_response.json()
    assert messages[1]["metrics"]["agent_mode"] == "native_tool_calling"
    assert "agent_steps" not in messages[1]["metrics"]

    invocation_response = client.get(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}"
        f"/agent-invocations/{payload['agent_invocation_id']}"
    )
    invocation = invocation_response.json()
    assert invocation["agent_mode"] == "native_tool_calling"
    assert invocation["steps"] == payload["steps"]
    assert native_client.calls[0]["tool_choice"] == "auto"


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


def test_agent_endpoint_validates_agent_mode(agent_api):
    client, _ = agent_api(["Final Answer: unused"])
    workspace = create_workspace(client)
    conversation = create_conversation(client, workspace["id"])

    response = client.post(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/agent",
        json={"message": "hello", "agent_mode": "missing_mode"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "hello", "max_steps": 0},
        {"message": "hello", "agent_mode": "missing_mode"},
    ],
)
def test_agent_stream_endpoint_uses_request_validation(agent_api, payload):
    client, _ = agent_api(["Final Answer: unused"])
    workspace = create_workspace(client)
    conversation = create_conversation(client, workspace["id"])

    response = client.post(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}"
        "/agent/stream",
        json=payload,
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
    assert "default mode is ReAct text" in operation["description"]
    assert "DeepSeek native tool calling" in operation["description"]
    assert "separate agent invocation record" in operation["description"]
    request_schema = schema["components"]["schemas"]["WorkspaceAgentRequest"]
    assert "approved_tool_call_ids" in request_schema["properties"]
    tool_result_schema = schema["components"]["schemas"]["AgentToolResultRead"]
    assert "artifacts" in tool_result_schema["properties"]
    assert "error_details" in tool_result_schema["properties"]
    assert "data" not in tool_result_schema["properties"]
    artifact_schema = schema["components"]["schemas"]["AgentToolArtifactsRead"]
    assert set(artifact_schema["properties"]) == {"sources", "outputs"}

    stream_operation = schema["paths"][
        "/workspaces/{workspace_id}/conversations/{conversation_id}/agent/stream"
    ]["post"]
    assert stream_operation["summary"] == "Stream workspace agent events"
    assert "not token-by-token answer text" in stream_operation["description"]

    invocation_operation = schema["paths"][
        "/workspaces/{workspace_id}/conversations/{conversation_id}"
        "/agent-invocations/{invocation_id}"
    ]["get"]
    assert invocation_operation["summary"] == "Read workspace agent invocation"
