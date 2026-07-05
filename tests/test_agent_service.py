import asyncio

import pytest
from pydantic import BaseModel, Field
from sqlmodel import select

from app.core.agent_loop import DEFAULT_AGENT_STEPS, MAX_STEPS_ANSWER
from app.core.rag import RetrievedChunk
from app.models.agent import (
    AGENT_INVOCATION_STATUS_COMPLETED,
    AGENT_INVOCATION_STATUS_MAX_STEPS_REACHED,
    AgentInvocation,
    AgentStepRecord,
)
from app.models.conversation import ConversationMessage
from app.services.agent_service import AgentService
from app.services.chat_service import ChatResult
from app.services.exceptions import (
    AgentInvocationNotFoundError,
    ConversationNotFoundError,
    WorkspaceNotFoundError,
    WorkspacePersistenceError,
)
from app.services.workspace_service import WorkspaceService
from app.tools.calculator import CalculatorTool
from app.tools.document_tools import WorkspaceDocumentSearchTool
from app.tools.registry import ToolRegistry, ToolResult
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


class CaptureContextInput(BaseModel):
    value: str = Field(min_length=1)


class CaptureContextTool:
    name = "capture_context"
    description = "Capture ToolContext for AgentService tests."
    input_model = CaptureContextInput

    def __init__(self):
        self.workspace_id = None
        self.conversation_id = None

    async def run(self, input_data, context):
        self.workspace_id = context.workspace_id
        self.conversation_id = context.conversation_id
        return ToolResult(ok=True, content=f"captured {input_data.value}")


def make_chunk(
    workspace_id: str,
    *,
    text: str = "Workspace source text.",
    chunk_index: int = 0,
    score: float = 0.91,
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


def make_registry(*tools):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def make_services(session, responses, *, registry=None, rag=None):
    rag = rag or FakeRAGService()
    workspace_service = WorkspaceService(
        rag=rag,
        chat=FakeChatService(echo=True),
    )
    agent_chat = ScriptedAgentChat(responses)
    agent_service = AgentService(
        workspace=workspace_service,
        chat=agent_chat,
        tool_registry=registry or make_registry(CalculatorTool()),
    )
    workspace = workspace_service.create_workspace(
        session,
        name="Agent workspace",
        system_prompt="Answer as an agent.",
        temperature=0.2,
        history_limit=2,
        top_k=4,
        similarity_threshold=0.7,
    )
    conversation = workspace_service.create_conversation(session, workspace.id)
    return agent_service, workspace_service, agent_chat, workspace, conversation


def list_message_count(session) -> int:
    return len(session.exec(select(ConversationMessage)).all())


def list_invocations(session):
    return list(session.exec(select(AgentInvocation)).all())


def list_steps(session, invocation_id: str | None = None):
    statement = select(AgentStepRecord).order_by(AgentStepRecord.step_index.asc())
    if invocation_id is not None:
        statement = statement.where(AgentStepRecord.invocation_id == invocation_id)
    return list(session.exec(statement).all())


def list_messages(session):
    statement = select(ConversationMessage).order_by(ConversationMessage.created_at)
    return list(session.exec(statement).all())


def test_agent_service_returns_final_answer_and_saves_exchange(session):
    agent_service, _, agent_chat, workspace, conversation = make_services(
        session,
        ["Final Answer: no tool needed"],
        registry=make_registry(),
    )

    result = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            " hello agent ",
        )
    )

    assert result.conversation_id == conversation.id
    assert result.agent_invocation_id
    assert result.message == "hello agent"
    assert result.answer == "no tool needed"
    assert result.steps == []
    assert result.sources == []
    assert result.provider == "fake"
    assert result.model == "fake-agent-model"
    assert result.metrics.max_steps == DEFAULT_AGENT_STEPS
    assert result.metrics.llm_call_count == 1
    assert result.metrics.step_count == 0
    assert result.metrics.tool_call_count == 0
    assert result.metrics.failed_step_count == 0
    assert result.metrics.source_count == 0
    assert result.metrics.max_steps_reached is False
    assert result.metrics.total_latency_ms >= 0
    assert agent_chat.calls[0]["history"] == []
    assert agent_chat.calls[0]["temperature"] == 0.2

    messages = list_messages(session)
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].content == "hello agent"
    assert messages[0].metrics == {}
    assert messages[1].content == "no tool needed"
    assert messages[1].sources == []
    assert messages[1].provider == "fake"
    assert messages[1].model == "fake-agent-model"
    assert messages[1].metrics["agent_mode"] == "react_text"
    assert messages[1].metrics["agent_invocation_id"] == result.agent_invocation_id
    assert "agent_steps" not in messages[1].metrics
    assert messages[1].metrics["tool_call_count"] == 0
    assert messages[1].metrics["max_steps_reached"] is False

    invocation = session.get(AgentInvocation, result.agent_invocation_id)
    assert invocation is not None
    assert invocation.workspace_id == workspace.id
    assert invocation.conversation_id == conversation.id
    assert invocation.user_message_id == messages[0].id
    assert invocation.assistant_message_id == messages[1].id
    assert invocation.input_message == "hello agent"
    assert invocation.agent_mode == "react_text"
    assert invocation.status == AGENT_INVOCATION_STATUS_COMPLETED
    assert invocation.max_steps == DEFAULT_AGENT_STEPS
    assert invocation.step_count == 0
    assert list_steps(session, invocation.id) == []
    session.refresh(conversation)
    assert conversation.title == "hello agent"


def test_agent_service_calls_calculator_then_returns_final_answer(session):
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [
            'Action: calculator\nAction Input: {"expression": "1 + 2 * 3"}',
            "Final Answer: the result is 7",
        ],
    )

    result = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "calculate",
        )
    )

    assert result.answer == "the result is 7"
    assert len(result.steps) == 1
    assert result.steps[0].ok is True
    assert result.steps[0].action == "calculator"
    assert result.steps[0].observation == "7"
    assert result.metrics.llm_call_count == 2
    assert result.metrics.step_count == 1
    assert result.metrics.tool_call_count == 1
    assert result.metrics.failed_step_count == 0

    assistant_message = list_messages(session)[1]
    assert "agent_steps" not in assistant_message.metrics
    assert (
        assistant_message.metrics["agent_invocation_id"] == result.agent_invocation_id
    )
    step_records = list_steps(session, result.agent_invocation_id)
    assert len(step_records) == 1
    assert step_records[0].action == "calculator"
    assert step_records[0].tool_result["data"] == {"result": 7}

    invocation = agent_service.get_invocation(
        session,
        workspace.id,
        conversation.id,
        result.agent_invocation_id,
    )
    assert invocation.status == AGENT_INVOCATION_STATUS_COMPLETED
    assert invocation.steps == result.steps


def test_agent_service_get_invocation_rejects_wrong_conversation(session):
    agent_service, workspace_service, _, workspace, conversation = make_services(
        session,
        ["Final Answer: answer"],
    )
    result = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "hello",
        )
    )
    other_conversation = workspace_service.create_conversation(session, workspace.id)

    with pytest.raises(AgentInvocationNotFoundError):
        agent_service.get_invocation(
            session,
            workspace.id,
            other_conversation.id,
            result.agent_invocation_id,
        )


def test_agent_service_passes_tool_context_to_tools(session):
    capture_tool = CaptureContextTool()
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [
            'Action: capture_context\nAction Input: {"value": "hello"}',
            "Final Answer: captured",
        ],
        registry=make_registry(capture_tool),
    )

    result = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "capture",
        )
    )

    assert result.answer == "captured"
    assert capture_tool.workspace_id == workspace.id
    assert capture_tool.conversation_id == conversation.id


def test_agent_service_collects_workspace_document_search_sources(session):
    rag = FakeRAGService()
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [
            (
                "Action: workspace_document_search\n"
                'Action Input: {"question": "where is the answer?"}'
            ),
            "Final Answer: found it in the workspace document",
        ],
        registry=make_registry(WorkspaceDocumentSearchTool(rag=rag)),
        rag=rag,
    )
    rag.chunks = [make_chunk(workspace.id)]

    result = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "where is the answer?",
        )
    )

    assert result.answer == "found it in the workspace document"
    assert rag.retrieve_calls == [("where is the answer?", workspace.id, None, None)]
    assert len(result.sources) == 1
    assert result.sources[0].document_id == "a" * 32
    assert result.sources[0].original_filename == "guide.txt"
    assert "upload_path" not in result.sources[0].model_dump()
    assert "parsed_path" not in result.sources[0].model_dump()
    assert result.metrics.source_count == 1

    assistant_message = list_messages(session)[1]
    assert assistant_message.sources == [
        result.sources[0].model_dump(mode="json"),
    ]
    assert assistant_message.metrics["source_count"] == 1


def test_agent_service_does_not_pre_retrieve_workspace_context(session):
    rag = FakeRAGService(chunks=[make_chunk("placeholder")])
    agent_service, _, _, workspace, conversation = make_services(
        session,
        ["Final Answer: direct answer"],
        registry=make_registry(),
        rag=rag,
    )

    result = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "answer directly",
        )
    )

    assert result.answer == "direct answer"
    assert rag.retrieve_calls == []


def test_agent_service_persists_failed_tool_step(session):
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [
            'Action: missing_tool\nAction Input: {"value": "x"}',
            "Final Answer: I cannot use that tool.",
        ],
        registry=make_registry(),
    )

    result = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "use a missing tool",
        )
    )

    assert result.metrics.failed_step_count == 1
    persisted_step = list_steps(session, result.agent_invocation_id)[0]
    assert persisted_step.ok is False
    assert persisted_step.error == "unknown_tool"
    assert persisted_step.tool_result["error"] == "unknown_tool"


def test_agent_service_persists_invalid_tool_input_step(session):
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [
            'Action: calculator\nAction Input: {"expression": ""}',
            "Final Answer: invalid expression",
        ],
    )

    result = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "calculate invalid expression",
        )
    )

    assert result.metrics.failed_step_count == 1
    persisted_step = list_steps(session, result.agent_invocation_id)[0]
    assert persisted_step.ok is False
    assert persisted_step.error == "invalid_tool_input"
    assert persisted_step.tool_result["error"] == "invalid_tool_input"


def test_agent_service_reports_max_steps_reached(session):
    agent_service, _, _, workspace, conversation = make_services(
        session,
        ['Action: calculator\nAction Input: {"expression": "1 + 1"}'],
    )

    result = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "loop once",
            max_steps=1,
        )
    )

    assert result.answer == MAX_STEPS_ANSWER
    assert result.metrics.max_steps_reached is True
    assert result.metrics.max_steps == 1
    assert result.metrics.llm_call_count == 1
    assert result.metrics.step_count == 1
    assistant_message = list_messages(session)[1]
    assert assistant_message.metrics["max_steps_reached"] is True
    invocation = session.get(AgentInvocation, result.agent_invocation_id)
    assert invocation.status == AGENT_INVOCATION_STATUS_MAX_STEPS_REACHED


def test_agent_service_rolls_back_when_saving_exchange_fails(session, monkeypatch):
    agent_service, _, _, workspace, conversation = make_services(
        session,
        ["Final Answer: no tool needed"],
        registry=make_registry(),
    )
    original_commit = session.commit

    def broken_commit():
        from sqlalchemy.exc import SQLAlchemyError

        raise SQLAlchemyError("write failed")

    monkeypatch.setattr(session, "commit", broken_commit)
    with pytest.raises(
        WorkspacePersistenceError,
        match="failed to save agent conversation messages",
    ):
        asyncio.run(
            agent_service.run_in_conversation(
                session,
                workspace.id,
                conversation.id,
                "hello",
            )
        )

    monkeypatch.setattr(session, "commit", original_commit)
    assert list_message_count(session) == 0
    assert list_invocations(session) == []
    assert list_steps(session) == []


def test_agent_service_raises_for_missing_workspace(session):
    agent_service = AgentService(
        workspace=WorkspaceService(rag=FakeRAGService(), chat=FakeChatService()),
        chat=ScriptedAgentChat(["Final Answer: unused"]),
        tool_registry=make_registry(),
    )

    with pytest.raises(WorkspaceNotFoundError):
        asyncio.run(
            agent_service.run_in_conversation(
                session,
                "missing-workspace",
                "missing-conversation",
                "hello",
            )
        )


def test_agent_service_raises_for_missing_conversation(session):
    workspace_service = WorkspaceService(
        rag=FakeRAGService(),
        chat=FakeChatService(),
    )
    workspace = workspace_service.create_workspace(session, name="Agent workspace")
    agent_service = AgentService(
        workspace=workspace_service,
        chat=ScriptedAgentChat(["Final Answer: unused"]),
        tool_registry=make_registry(),
    )

    with pytest.raises(ConversationNotFoundError):
        asyncio.run(
            agent_service.run_in_conversation(
                session,
                workspace.id,
                "missing-conversation",
                "hello",
            )
        )
