import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.core.agent_executor import (
    DEFAULT_AGENT_STEPS,
    MAX_STEPS_ANSWER,
    AgentRunResult,
    AgentStep,
)
from app.core.agent_loop import ReactTextAgentExecutor
from app.core.agent_modes import (
    AGENT_MODE_NATIVE_TOOL_CALLING,
    AGENT_MODE_REACT_TEXT,
)
from app.core.rag import RetrievedChunk
from app.models.agent import (
    AGENT_INVOCATION_STATUS_COMPLETED,
    AGENT_INVOCATION_STATUS_MAX_STEPS_REACHED,
    AGENT_INVOCATION_STATUS_NEEDS_INPUT,
    AgentInvocation,
    AgentStepRecord,
)
from app.models.conversation import Conversation, ConversationMessage
import app.services.agent_service as agent_service_module
from app.services.agent_service import AGENT_EXECUTION_CLAIM_LEASE, AgentService
from app.services.chat_service import ChatResult
from app.services.exceptions import (
    AgentInvocationConflictError,
    AgentInvocationNotFoundError,
    ConversationNotFoundError,
    WorkspaceNotFoundError,
    WorkspacePersistenceError,
)
from app.services.workspace_service import WorkspaceService
from app.tools.calculator import CalculatorTool
from app.tools.clarifying_question import ClarifyingQuestionTool
from app.tools.document_tools import WorkspaceDocumentSearchTool
from app.tools.interactions import ClarificationRequest, ToolInteraction
from app.tools.registry import (
    TOOL_CONFIRMATION_REQUIRED,
    ToolContext,
    ToolRegistry,
    ToolResult,
    build_tool_approval_id,
)
from tests.fakes import (
    CollectingEventEmitter,
    ConfirmationRequiredTool,
    FakeChatService,
    FakeRAGService,
)


class ScriptedAgentChat:
    def __init__(
        self,
        responses: list[str],
        *,
        block_call_index: int | None = None,
    ):
        self.responses = responses
        self.calls = []
        self.block_call_index = block_call_index
        self.call_started = asyncio.Event()
        self.release_call = asyncio.Event()

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
        if response_index == self.block_call_index:
            self.call_started.set()
            await self.release_call.wait()
        return ChatResult(
            message=message,
            answer=self.responses[response_index],
            provider="fake",
            model="fake-agent-model",
        )


class FixedModeExecutor:
    agent_mode = "test_mode"

    def __init__(self):
        self.max_steps = None

    async def run(
        self,
        message,
        context,
        system_prompt,
        history,
        temperature,
        max_steps,
        event_emitter=None,
    ):
        self.max_steps = max_steps
        return AgentRunResult(
            message=message.strip(),
            answer="fixed answer",
            steps=[],
            agent_mode=self.agent_mode,
            provider="fake",
            model="fake-executor-model",
            llm_call_count=1,
            max_steps_reached=False,
        )


class FixedNativeExecutor(FixedModeExecutor):
    agent_mode = AGENT_MODE_NATIVE_TOOL_CALLING


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


def make_services(session, responses, *, registry=None, rag=None, agent_chat=None):
    rag = rag or FakeRAGService()
    workspace_service = WorkspaceService(
        rag=rag,
        chat=FakeChatService(echo=True),
    )
    agent_chat = agent_chat or ScriptedAgentChat(responses)
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


def enable_foreign_key_checks(session):
    session.connection().exec_driver_sql("PRAGMA foreign_keys = ON")
    assert session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def test_agent_service_returns_final_answer_and_saves_exchange(session):
    enable_foreign_key_checks(session)
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
    assert messages[1].metrics["agent_mode"] == AGENT_MODE_REACT_TEXT
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
    assert invocation.agent_mode == AGENT_MODE_REACT_TEXT
    assert invocation.status == AGENT_INVOCATION_STATUS_COMPLETED
    assert invocation.max_steps == DEFAULT_AGENT_STEPS
    assert invocation.step_count == 0
    assert list_steps(session, invocation.id) == []
    session.refresh(conversation)
    assert conversation.title == "hello agent"


def test_agent_service_defaults_to_react_text_executor(session):
    agent_service, _, _, _, _ = make_services(
        session,
        ["Final Answer: no tool needed"],
    )

    assert isinstance(agent_service.agent_executor, ReactTextAgentExecutor)


def test_agent_service_persists_agent_mode_from_executor(session):
    workspace_service = WorkspaceService(
        rag=FakeRAGService(),
        chat=FakeChatService(echo=True),
    )
    agent_executor = FixedModeExecutor()
    agent_service = AgentService(
        workspace=workspace_service,
        agent_executor=agent_executor,
    )
    workspace = workspace_service.create_workspace(session, name="Agent workspace")
    conversation = workspace_service.create_conversation(session, workspace.id)

    result = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "hello",
            max_steps=3,
        )
    )

    messages = list_messages(session)
    invocation = session.get(AgentInvocation, result.agent_invocation_id)
    assert messages[1].metrics["agent_mode"] == "test_mode"
    assert invocation.agent_mode == "test_mode"
    assert agent_executor.max_steps == 3


def test_agent_service_selects_native_tool_calling_executor(session):
    workspace_service = WorkspaceService(
        rag=FakeRAGService(),
        chat=FakeChatService(echo=True),
    )
    native_agent_executor = FixedNativeExecutor()
    agent_service = AgentService(
        workspace=workspace_service,
        native_agent_executor=native_agent_executor,
    )
    workspace = workspace_service.create_workspace(session, name="Agent workspace")
    conversation = workspace_service.create_conversation(session, workspace.id)

    result = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "hello",
            max_steps=2,
            agent_mode=AGENT_MODE_NATIVE_TOOL_CALLING,
        )
    )

    messages = list_messages(session)
    invocation = session.get(AgentInvocation, result.agent_invocation_id)
    assert result.answer == "fixed answer"
    assert messages[1].metrics["agent_mode"] == AGENT_MODE_NATIVE_TOOL_CALLING
    assert invocation.agent_mode == AGENT_MODE_NATIVE_TOOL_CALLING
    assert native_agent_executor.max_steps == 2


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
    assert step_records[0].tool_result["artifacts"] == {
        "sources": [],
        "outputs": {"result": 7},
    }
    assert step_records[0].tool_result["error_details"] == {}
    assert "data" not in step_records[0].tool_result

    invocation = agent_service.get_invocation(
        session,
        workspace.id,
        conversation.id,
        result.agent_invocation_id,
    )
    assert invocation.status == AGENT_INVOCATION_STATUS_COMPLETED
    assert invocation.answer == "the result is 7"
    assert invocation.steps == result.steps


def test_agent_service_pauses_and_continues_same_invocation(session):
    enable_foreign_key_checks(session)

    agent_service, _, agent_chat, workspace, conversation = make_services(
        session,
        [
            (
                "Action: request_user_input\nAction Input: "
                '{"question": "Which report?", "input_type": "choice", '
                '"choices": ["annual", "market"]}'
            ),
            "Final Answer: I will use the selected report.",
        ],
        registry=make_registry(ClarifyingQuestionTool()),
    )

    paused = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "summarize a report",
        )
    )

    assert paused.status == AGENT_INVOCATION_STATUS_NEEDS_INPUT
    assert paused.answer is None
    assert paused.pending_input is not None
    assert paused.pending_input.choices == ["annual", "market"]
    assert [message.role for message in list_messages(session)] == ["user"]
    invocation = session.get(AgentInvocation, paused.agent_invocation_id)
    assert invocation is not None
    assert invocation.assistant_message_id is None
    assert invocation.ended_at is None
    assert invocation.status == AGENT_INVOCATION_STATUS_NEEDS_INPUT
    session.refresh(conversation)
    assert conversation.agent_execution_claim_id is None
    assert conversation.agent_execution_claimed_at is None

    completed = asyncio.run(
        agent_service.continue_in_conversation(
            session,
            workspace.id,
            conversation.id,
            paused.agent_invocation_id,
            answer="annual",
        )
    )

    assert completed.agent_invocation_id == paused.agent_invocation_id
    assert completed.status == AGENT_INVOCATION_STATUS_COMPLETED
    assert completed.answer == "I will use the selected report."
    assert completed.metrics.llm_call_count == 2
    assert len(completed.steps) == 1
    interaction = completed.steps[0].tool_result.interaction
    assert interaction is not None
    assert interaction.resolution is not None
    assert interaction.resolution.kind == "answered"
    assert interaction.resolution.answer == "annual"
    assert [message.role for message in list_messages(session)] == ["user", "assistant"]
    assert "User answered clarification: annual" in agent_chat.calls[1]["message"]
    invocation = session.get(AgentInvocation, paused.agent_invocation_id)
    assert invocation is not None
    session.refresh(conversation)
    assert conversation.agent_execution_claim_id is None
    assert conversation.agent_execution_claimed_at is None


def test_agent_service_rejects_continuation_already_claimed_by_another_request(session):
    agent_service, _, agent_chat, workspace, conversation = make_services(
        session,
        [
            (
                "Action: request_user_input\nAction Input: "
                '{"question": "Which report?", "input_type": "text"}'
            ),
            "Final Answer: resumed after the claim was released.",
        ],
        registry=make_registry(ClarifyingQuestionTool()),
    )
    paused = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "summarize a report",
        )
    )
    invocation = session.get(AgentInvocation, paused.agent_invocation_id)
    assert invocation is not None
    agent_service._claim_agent_execution(
        session,
        conversation,
        "other-request",
        pending_invocation_id=invocation.id,
    )

    with pytest.raises(AgentInvocationConflictError, match="active Agent execution"):
        asyncio.run(
            agent_service.continue_in_conversation(
                session,
                workspace.id,
                conversation.id,
                paused.agent_invocation_id,
                answer="annual",
            )
        )

    assert len(agent_chat.calls) == 1
    agent_service._release_agent_execution(
        session,
        workspace.id,
        conversation.id,
        "other-request",
    )

    completed = asyncio.run(
        agent_service.continue_in_conversation(
            session,
            workspace.id,
            conversation.id,
            paused.agent_invocation_id,
            answer="annual",
        )
    )

    assert completed.answer == "resumed after the claim was released."


def test_agent_service_rejects_concurrent_initial_run_before_second_llm_call(session):
    agent_chat = ScriptedAgentChat(
        ["Final Answer: first request completed"],
        block_call_index=0,
    )
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [],
        registry=make_registry(),
        agent_chat=agent_chat,
    )

    async def run_concurrently():
        first_run = asyncio.create_task(
            agent_service.run_in_conversation(
                session,
                workspace.id,
                conversation.id,
                "first request",
            )
        )
        await asyncio.wait_for(agent_chat.call_started.wait(), timeout=1)
        with Session(session.get_bind()) as second_session:
            with pytest.raises(
                AgentInvocationConflictError, match="active Agent execution"
            ):
                await agent_service.run_in_conversation(
                    second_session,
                    workspace.id,
                    conversation.id,
                    "second request",
                )
        assert len(agent_chat.calls) == 1
        agent_chat.release_call.set()
        return await first_run

    result = asyncio.run(run_concurrently())

    assert result.answer == "first request completed"
    session.refresh(conversation)
    assert conversation.agent_execution_claim_id is None


def test_agent_service_claim_blocks_initial_run_when_pending_state_appears(session):
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [
            (
                "Action: request_user_input\nAction Input: "
                '{"question": "Which report?", "input_type": "text"}'
            ),
        ],
        registry=make_registry(ClarifyingQuestionTool()),
    )
    paused = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "summarize a report",
        )
    )

    with pytest.raises(AgentInvocationConflictError, match="active Agent execution"):
        agent_service._claim_agent_execution(session, conversation, "late-initial-run")

    session.refresh(conversation)
    assert conversation.agent_execution_claim_id is None
    assert session.get(AgentInvocation, paused.agent_invocation_id) is not None


def test_agent_service_returns_conflict_when_stale_execution_late_pauses(session):
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [
            (
                "Action: request_user_input\nAction Input: "
                '{"question": "Which report?", "input_type": "text"}'
            ),
        ],
        registry=make_registry(ClarifyingQuestionTool()),
    )
    current_pause = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "current request",
        )
    )
    current_invocation = session.get(AgentInvocation, current_pause.agent_invocation_id)
    assert current_invocation is not None
    agent_service._claim_agent_execution(
        session,
        conversation,
        "new-request",
        pending_invocation_id=current_invocation.id,
    )

    request = ClarificationRequest(
        question="Which format should I use?",
        input_type="text",
    )
    stale_result = AgentRunResult(
        message="stale request",
        answer=None,
        steps=[
            AgentStep(
                step_index=1,
                llm_output=(
                    "Action: request_user_input\nAction Input: "
                    '{"question": "Which format should I use?", '
                    '"input_type": "text"}'
                ),
                action="request_user_input",
                action_input=request.model_dump(),
                observation="Clarification requested. Waiting for the user response.",
                ok=True,
                tool_result=ToolResult(
                    ok=True,
                    content="Clarification requested. Waiting for the user response.",
                    interaction=ToolInteraction(
                        kind="clarification",
                        request=request,
                    ),
                ),
            )
        ],
        agent_mode=AGENT_MODE_REACT_TEXT,
        provider="fake",
        model="fake-agent-model",
        llm_call_count=1,
        max_steps_reached=False,
        pending_interaction=ToolInteraction(kind="clarification", request=request),
    )
    metrics = agent_service._metrics(
        max_steps=3,
        agent_result=stale_result,
        total_latency_ms=1,
    )

    with pytest.raises(AgentInvocationConflictError, match="claim was lost"):
        agent_service._save_pending_invocation(
            session=session,
            workspace_id=workspace.id,
            conversation=conversation,
            user_content="stale request",
            agent_result=stale_result,
            metrics=metrics,
            pending_input=agent_service._pending_input(stale_result),
            resume_state=agent_service_module.AgentResumeState(
                system_prompt="Answer as an agent.",
                history=[],
                temperature=0.2,
            ),
            started_at=datetime.now(UTC),
            execution_claim_id="old-request",
        )

    session.expire_all()
    assert [message.content for message in list_messages(session)] == [
        "current request"
    ]
    assert [invocation.id for invocation in list_invocations(session)] == [
        current_invocation.id
    ]
    persisted_conversation = session.get(Conversation, conversation.id)
    assert persisted_conversation is not None
    assert persisted_conversation.agent_execution_claim_id == "new-request"


def test_agent_service_preserves_title_updated_by_concurrent_normal_chat(session):
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [],
        registry=make_registry(),
    )
    agent_service._claim_agent_execution(session, conversation, "agent-request")

    with Session(session.get_bind()) as normal_chat_session:
        normal_chat_conversation = normal_chat_session.get(
            Conversation, conversation.id
        )
        assert normal_chat_conversation is not None
        normal_chat_conversation.title = "normal chat title"
        normal_chat_session.add(normal_chat_conversation)
        normal_chat_session.commit()

    agent_service._release_agent_execution_in_transaction(
        session,
        conversation,
        "agent-request",
        user_content="agent request title",
    )
    session.commit()
    session.expire_all()

    persisted_conversation = session.get(Conversation, conversation.id)
    assert persisted_conversation is not None
    assert persisted_conversation.title == "normal chat title"
    assert persisted_conversation.agent_execution_claim_id is None


def test_agent_service_rejects_initial_run_while_continue_is_executing(session):
    agent_chat = ScriptedAgentChat(
        [
            (
                "Action: request_user_input\nAction Input: "
                '{"question": "Which report?", "input_type": "text"}'
            ),
            "Final Answer: resumed request completed",
        ],
        block_call_index=1,
    )
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [],
        registry=make_registry(ClarifyingQuestionTool()),
        agent_chat=agent_chat,
    )
    paused = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "summarize a report",
        )
    )

    async def run_concurrently():
        continuation = asyncio.create_task(
            agent_service.continue_in_conversation(
                session,
                workspace.id,
                conversation.id,
                paused.agent_invocation_id,
                answer="annual",
            )
        )
        await asyncio.wait_for(agent_chat.call_started.wait(), timeout=1)
        with Session(session.get_bind()) as second_session:
            with pytest.raises(
                AgentInvocationConflictError, match="waiting for user input"
            ):
                await agent_service.run_in_conversation(
                    second_session,
                    workspace.id,
                    conversation.id,
                    "new request",
                )
            with pytest.raises(
                AgentInvocationConflictError, match="active Agent execution"
            ):
                await agent_service.continue_in_conversation(
                    second_session,
                    workspace.id,
                    conversation.id,
                    paused.agent_invocation_id,
                    answer="annual",
                )
        assert len(agent_chat.calls) == 2
        agent_chat.release_call.set()
        return await continuation

    result = asyncio.run(run_concurrently())

    assert result.answer == "resumed request completed"
    session.refresh(conversation)
    assert conversation.agent_execution_claim_id is None


def test_agent_service_heartbeat_renews_a_slow_execution_claim(session, monkeypatch):
    monkeypatch.setattr(
        agent_service_module,
        "AGENT_EXECUTION_CLAIM_LEASE",
        timedelta(milliseconds=100),
    )
    monkeypatch.setattr(
        agent_service_module,
        "AGENT_EXECUTION_CLAIM_HEARTBEAT_INTERVAL",
        timedelta(milliseconds=20),
    )
    agent_chat = ScriptedAgentChat(
        ["Final Answer: slow request completed"],
        block_call_index=0,
    )
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [],
        registry=make_registry(),
        agent_chat=agent_chat,
    )

    async def run_slow_execution():
        first_run = asyncio.create_task(
            agent_service.run_in_conversation(
                session,
                workspace.id,
                conversation.id,
                "slow request",
            )
        )
        await asyncio.wait_for(agent_chat.call_started.wait(), timeout=1)
        await asyncio.sleep(0.16)
        with Session(session.get_bind()) as second_session:
            with pytest.raises(
                AgentInvocationConflictError, match="active Agent execution"
            ):
                await agent_service.run_in_conversation(
                    second_session,
                    workspace.id,
                    conversation.id,
                    "competing request",
                )
        assert len(agent_chat.calls) == 1
        agent_chat.release_call.set()
        return await first_run

    result = asyncio.run(run_slow_execution())

    assert result.answer == "slow request completed"


def test_agent_service_rolls_back_when_execution_claim_is_taken_over(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        agent_service_module,
        "AGENT_EXECUTION_CLAIM_HEARTBEAT_INTERVAL",
        timedelta(milliseconds=10),
    )
    agent_chat = ScriptedAgentChat(
        ["Final Answer: stale request completed"],
        block_call_index=0,
    )
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [],
        registry=make_registry(),
        agent_chat=agent_chat,
    )

    async def run_and_take_over_claim():
        first_run = asyncio.create_task(
            agent_service.run_in_conversation(
                session,
                workspace.id,
                conversation.id,
                "stale request",
            )
        )
        await asyncio.wait_for(agent_chat.call_started.wait(), timeout=1)
        with Session(session.get_bind()) as takeover_session:
            taken_over = takeover_session.get(Conversation, conversation.id)
            assert taken_over is not None
            taken_over.agent_execution_claim_id = "new-request"
            taken_over.agent_execution_claimed_at = datetime.now(UTC)
            takeover_session.add(taken_over)
            takeover_session.commit()
        await asyncio.sleep(0.04)
        agent_chat.release_call.set()
        with pytest.raises(AgentInvocationConflictError, match="claim was lost"):
            await first_run

    asyncio.run(run_and_take_over_claim())

    session.expire_all()
    persisted_conversation = session.get(Conversation, conversation.id)
    assert persisted_conversation is not None
    assert persisted_conversation.agent_execution_claim_id == "new-request"
    assert list_messages(session) == []
    assert session.exec(select(AgentInvocation)).all() == []


def test_agent_service_reclaims_an_expired_execution_claim(session):
    agent_service, _, agent_chat, workspace, conversation = make_services(
        session,
        [
            (
                "Action: request_user_input\nAction Input: "
                '{"question": "Which report?", "input_type": "text"}'
            ),
            "Final Answer: recovered from an interrupted continuation.",
        ],
        registry=make_registry(ClarifyingQuestionTool()),
    )
    paused = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "summarize a report",
        )
    )
    conversation.agent_execution_claim_id = "interrupted-request"
    conversation.agent_execution_claimed_at = (
        datetime.now(UTC) - AGENT_EXECUTION_CLAIM_LEASE
    )
    session.add(conversation)
    session.commit()

    completed = asyncio.run(
        agent_service.continue_in_conversation(
            session,
            workspace.id,
            conversation.id,
            paused.agent_invocation_id,
            answer="annual",
        )
    )

    assert completed.answer == "recovered from an interrupted continuation."
    assert len(agent_chat.calls) == 2


def test_agent_service_fences_an_execution_that_lost_its_claim(session):
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [
            (
                "Action: request_user_input\nAction Input: "
                '{"question": "Which report?", "input_type": "text"}'
            ),
        ],
        registry=make_registry(ClarifyingQuestionTool()),
    )
    paused = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "summarize a report",
        )
    )
    invocation = session.get(AgentInvocation, paused.agent_invocation_id)
    assert invocation is not None
    agent_service._claim_agent_execution(
        session,
        conversation,
        "old-request",
        pending_invocation_id=invocation.id,
    )
    conversation.agent_execution_claimed_at = (
        datetime.now(UTC) - AGENT_EXECUTION_CLAIM_LEASE
    )
    session.add(conversation)
    session.commit()
    agent_service._claim_agent_execution(
        session,
        conversation,
        "new-request",
        pending_invocation_id=invocation.id,
    )

    agent_result = AgentRunResult(
        message="summarize a report",
        answer="stale answer",
        steps=[],
        agent_mode=AGENT_MODE_REACT_TEXT,
        provider="fake",
        model="fake-agent-model",
        llm_call_count=2,
        max_steps_reached=False,
    )
    metrics = agent_service._metrics(
        max_steps=invocation.max_steps,
        agent_result=agent_result,
        total_latency_ms=1,
    )

    with pytest.raises(AgentInvocationConflictError, match="claim was lost"):
        agent_service._finalize_invocation(
            session=session,
            invocation=invocation,
            conversation=conversation,
            agent_result=agent_result,
            metrics=metrics,
            sources=[],
            ended_at=datetime.now(UTC),
            execution_claim_id="old-request",
        )

    session.expire_all()
    invocation = session.get(AgentInvocation, paused.agent_invocation_id)
    assert invocation is not None
    assert invocation.status == AGENT_INVOCATION_STATUS_NEEDS_INPUT
    session.refresh(conversation)
    assert conversation.agent_execution_claim_id == "new-request"
    assert [message.role for message in list_messages(session)] == ["user"]


def test_agent_service_releases_execution_claim_after_initial_run_error(session):
    agent_service, _, agent_chat, workspace, conversation = make_services(
        session,
        [],
        registry=make_registry(),
    )

    with pytest.raises(IndexError):
        asyncio.run(
            agent_service.run_in_conversation(
                session,
                workspace.id,
                conversation.id,
                "first attempt",
            )
        )

    session.refresh(conversation)
    assert conversation.agent_execution_claim_id is None
    assert list_messages(session) == []

    agent_chat.responses.extend(["unused", "Final Answer: retry completed"])
    completed = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "retry",
        )
    )

    assert completed.answer == "retry completed"


def test_agent_service_releases_execution_claim_after_resume_error(session):
    agent_service, _, agent_chat, workspace, conversation = make_services(
        session,
        [
            (
                "Action: request_user_input\nAction Input: "
                '{"question": "Which report?", "input_type": "text"}'
            ),
        ],
        registry=make_registry(ClarifyingQuestionTool()),
    )
    paused = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "summarize a report",
        )
    )

    with pytest.raises(IndexError):
        asyncio.run(
            agent_service.continue_in_conversation(
                session,
                workspace.id,
                conversation.id,
                paused.agent_invocation_id,
                answer="annual",
            )
        )

    invocation = session.get(AgentInvocation, paused.agent_invocation_id)
    assert invocation is not None
    assert invocation.status == AGENT_INVOCATION_STATUS_NEEDS_INPUT
    session.refresh(conversation)
    assert conversation.agent_execution_claim_id is None
    assert [message.role for message in list_messages(session)] == ["user"]

    agent_chat.responses.extend(
        ["unused response for the failed retry", "Final Answer: recovered."]
    )
    completed = asyncio.run(
        agent_service.continue_in_conversation(
            session,
            workspace.id,
            conversation.id,
            paused.agent_invocation_id,
            answer="annual",
        )
    )

    assert completed.answer == "recovered."


def test_agent_service_releases_execution_claim_when_resume_is_cancelled(session):
    agent_service, _, agent_chat, workspace, conversation = make_services(
        session,
        [
            (
                "Action: request_user_input\nAction Input: "
                '{"question": "Which report?", "input_type": "text"}'
            ),
        ],
        registry=make_registry(ClarifyingQuestionTool()),
    )
    paused = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "summarize a report",
        )
    )

    async def cancelled_chat(*args, **kwargs):
        raise asyncio.CancelledError

    agent_chat.chat = cancelled_chat
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            agent_service.continue_in_conversation(
                session,
                workspace.id,
                conversation.id,
                paused.agent_invocation_id,
                answer="annual",
            )
        )

    invocation = session.get(AgentInvocation, paused.agent_invocation_id)
    assert invocation is not None
    assert invocation.status == AGENT_INVOCATION_STATUS_NEEDS_INPUT
    session.refresh(conversation)
    assert conversation.agent_execution_claim_id is None
    assert conversation.agent_execution_claimed_at is None


def test_agent_service_rejects_invalid_choice_and_second_run_while_paused(session):
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [
            (
                "Action: request_user_input\nAction Input: "
                '{"question": "Which?", "input_type": "choice", '
                '"choices": ["a", "b"]}'
            )
        ],
        registry=make_registry(ClarifyingQuestionTool()),
    )
    paused = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "choose",
        )
    )

    with pytest.raises(ValueError, match="must match"):
        asyncio.run(
            agent_service.continue_in_conversation(
                session,
                workspace.id,
                conversation.id,
                paused.agent_invocation_id,
                answer="missing",
            )
        )
    with pytest.raises(AgentInvocationConflictError, match="waiting for user input"):
        asyncio.run(
            agent_service.run_in_conversation(
                session,
                workspace.id,
                conversation.id,
                "another request",
            )
        )


def test_agent_service_times_out_pending_input_when_continued(session):
    agent_service, _, agent_chat, workspace, conversation = make_services(
        session,
        [
            (
                "Action: request_user_input\nAction Input: "
                '{"question": "Need input", "input_type": "text"}'
            ),
            "Final Answer: I continued without the input.",
        ],
        registry=make_registry(ClarifyingQuestionTool()),
    )
    paused = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "continue later",
        )
    )
    invocation = session.get(AgentInvocation, paused.agent_invocation_id)
    assert invocation is not None
    invocation.pending_input = {
        **invocation.pending_input,
        "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }
    session.add(invocation)
    session.commit()

    completed = asyncio.run(
        agent_service.continue_in_conversation(
            session,
            workspace.id,
            conversation.id,
            paused.agent_invocation_id,
            answer="ignored after expiry",
        )
    )

    interaction = completed.steps[0].tool_result.interaction
    assert interaction is not None
    assert interaction.resolution is not None
    assert interaction.resolution.kind == "timed_out"
    assert "timed out" in agent_chat.calls[1]["message"]


def test_agent_service_emits_started_and_finished_events(session):
    emitter = CollectingEventEmitter()
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [
            'Action: calculator\nAction Input: {"expression": "1 + 2"}',
            "Final Answer: the result is 3",
        ],
    )

    result = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "calculate",
            event_emitter=emitter,
        )
    )

    event_types = [event["type"] for event in emitter.events]
    assert event_types[0] == "agent_started"
    assert event_types[-1] == "agent_finished"
    assert "tool_finished" in event_types
    assert emitter.events[0]["payload"]["workspace_id"] == workspace.id
    assert (
        emitter.events[-1]["payload"]["agent_invocation_id"]
        == result.agent_invocation_id
    )
    assert emitter.events[-1]["payload"]["answer"] == "the result is 3"


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


def test_agent_service_persists_confirmation_required_tool_step(session):
    tool = ConfirmationRequiredTool()
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [
            'Action: confirmation_required\nAction Input: {"value": "hello"}',
            "Final Answer: waiting for approval",
        ],
        registry=make_registry(tool),
    )

    result = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "use restricted tool",
        )
    )

    assert result.metrics.failed_step_count == 1
    assert result.steps[0].error == TOOL_CONFIRMATION_REQUIRED
    assert result.steps[0].tool_result is not None
    assert (
        result.steps[0]
        .tool_result.error_details["approval_id"]
        .startswith("tool_approval_")
    )
    assert tool.executed is False
    persisted_step = list_steps(session, result.agent_invocation_id)[0]
    assert persisted_step.ok is False
    assert persisted_step.error == TOOL_CONFIRMATION_REQUIRED
    assert persisted_step.tool_result["error"] == TOOL_CONFIRMATION_REQUIRED
    assert persisted_step.tool_result["error_details"]["reason"] == (
        "confirmation_required"
    )


def test_agent_service_runs_confirmation_required_tool_with_approval(session):
    tool = ConfirmationRequiredTool()
    agent_service, _, _, workspace, conversation = make_services(
        session,
        [
            'Action: confirmation_required\nAction Input: {"value": "hello"}',
            "Final Answer: approved",
        ],
        registry=make_registry(tool),
    )
    approval_id = build_tool_approval_id(
        tool_name=tool.name,
        action_input={"value": "hello"},
        context=ToolContext(
            workspace_id=workspace.id,
            conversation_id=conversation.id,
            agent_mode=AGENT_MODE_REACT_TEXT,
        ),
    )

    result = asyncio.run(
        agent_service.run_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "use restricted tool",
            approved_tool_call_ids=[approval_id],
        )
    )

    assert result.metrics.failed_step_count == 0
    assert result.steps[0].ok is True
    assert result.steps[0].observation == "approved hello"
    assert tool.executed is True


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
    commit_calls = 0

    def broken_commit():
        nonlocal commit_calls
        from sqlalchemy.exc import SQLAlchemyError

        commit_calls += 1
        if commit_calls == 2:
            raise SQLAlchemyError("write failed")
        return original_commit()

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


def test_agent_service_emits_failed_event_when_saving_exchange_fails(
    session,
    monkeypatch,
):
    emitter = CollectingEventEmitter()
    agent_service, _, _, workspace, conversation = make_services(
        session,
        ["Final Answer: no tool needed"],
        registry=make_registry(),
    )
    original_commit = session.commit
    commit_calls = 0

    def broken_commit():
        nonlocal commit_calls
        from sqlalchemy.exc import SQLAlchemyError

        commit_calls += 1
        if commit_calls == 2:
            raise SQLAlchemyError("write failed")
        return original_commit()

    monkeypatch.setattr(session, "commit", broken_commit)
    with pytest.raises(WorkspacePersistenceError):
        asyncio.run(
            agent_service.run_in_conversation(
                session,
                workspace.id,
                conversation.id,
                "hello",
                event_emitter=emitter,
            )
        )

    assert emitter.events[-1]["type"] == "agent_failed"
    assert emitter.events[-1]["payload"]["error_type"] == "WorkspacePersistenceError"


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
