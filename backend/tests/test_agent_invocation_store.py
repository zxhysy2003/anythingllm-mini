from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from app.core.agent_executor import AgentRunResult, AgentStep
from app.core.agent_modes import AGENT_MODE_REACT_TEXT
from app.models.agent import (
    AGENT_INVOCATION_STATUS_COMPLETED,
    AGENT_INVOCATION_STATUS_NEEDS_INPUT,
    AgentInvocation,
)
from app.models.conversation import Conversation, ConversationMessage
from app.services.agent_invocation_store import (
    AGENT_EXECUTION_CLAIM_LEASE,
    AgentInvocationStore,
)
from app.services.exceptions import AgentInvocationConflictError
from app.services.workspace_service import WorkspaceService
from app.tools.interactions import ClarificationRequest, ToolInteraction
from app.tools.registry import ToolResult
from tests.fakes import FakeChatService, FakeRAGService


def make_workspace_conversation(session):
    workspace_service = WorkspaceService(
        rag=FakeRAGService(),
        chat=FakeChatService(echo=True),
    )
    workspace = workspace_service.create_workspace(
        session,
        name="Agent workspace",
        system_prompt="Answer as an agent.",
        temperature=0.2,
    )
    conversation = workspace_service.create_conversation(session, workspace.id)
    return workspace, conversation


def pending_agent_result(message: str = "summarize a report") -> AgentRunResult:
    request = ClarificationRequest(
        question="Which report?",
        input_type="text",
    )
    interaction = ToolInteraction(kind="clarification", request=request)
    return AgentRunResult(
        message=message,
        answer=None,
        steps=[
            AgentStep(
                step_index=1,
                llm_output=(
                    "Action: request_user_input\nAction Input: "
                    '{"question": "Which report?", "input_type": "text"}'
                ),
                action="request_user_input",
                action_input=request.model_dump(),
                observation="Clarification requested. Waiting for the user response.",
                ok=True,
                tool_result=ToolResult(
                    ok=True,
                    content="Clarification requested. Waiting for the user response.",
                    interaction=interaction,
                ),
            )
        ],
        agent_mode=AGENT_MODE_REACT_TEXT,
        provider="fake",
        model="fake-agent-model",
        llm_call_count=1,
        max_steps_reached=False,
        pending_interaction=interaction,
    )


def pending_metrics() -> dict:
    return {
        "max_steps": 3,
        "llm_call_count": 1,
        "step_count": 1,
        "tool_call_count": 1,
        "failed_step_count": 0,
        "source_count": 0,
        "max_steps_reached": False,
        "total_latency_ms": 1,
    }


def save_pending_invocation(
    store: AgentInvocationStore,
    session,
    workspace,
    conversation,
    *,
    execution_claim_id: str = "pending-request",
) -> AgentInvocation:
    store.claim_execution(session, conversation, execution_claim_id)
    result = pending_agent_result()
    invocation_id = store.save_pending(
        session=session,
        workspace_id=workspace.id,
        conversation=conversation,
        user_content=result.message,
        agent_result=result,
        metrics=pending_metrics(),
        pending_input={
            **result.pending_interaction.request.model_dump(mode="json"),
            "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        },
        resume_state={
            "system_prompt": "Answer as an agent.",
            "history": [],
            "temperature": 0.2,
            "executor_state": None,
        },
        started_at=datetime.now(UTC),
        execution_claim_id=execution_claim_id,
    )
    invocation = session.get(AgentInvocation, invocation_id)
    assert invocation is not None
    return invocation


def test_pending_invocation_blocks_new_execution_claim(session):
    store = AgentInvocationStore()
    workspace, conversation = make_workspace_conversation(session)
    invocation = save_pending_invocation(store, session, workspace, conversation)

    with pytest.raises(AgentInvocationConflictError, match="waiting for user input"):
        store.require_no_pending_invocation(
            session,
            workspace.id,
            conversation.id,
        )
    with pytest.raises(AgentInvocationConflictError, match="active Agent execution"):
        store.claim_execution(session, conversation, "late-initial-run")

    session.refresh(conversation)
    assert conversation.agent_execution_claim_id is None
    assert session.get(AgentInvocation, invocation.id) is not None


def test_stale_execution_late_pause_rolls_back(session):
    store = AgentInvocationStore()
    workspace, conversation = make_workspace_conversation(session)
    current_invocation = save_pending_invocation(
        store,
        session,
        workspace,
        conversation,
    )
    store.claim_execution(
        session,
        conversation,
        "new-request",
        pending_invocation_id=current_invocation.id,
    )
    stale_result = pending_agent_result("stale request")

    with pytest.raises(AgentInvocationConflictError, match="claim was lost"):
        store.save_pending(
            session=session,
            workspace_id=workspace.id,
            conversation=conversation,
            user_content=stale_result.message,
            agent_result=stale_result,
            metrics=pending_metrics(),
            pending_input={
                **stale_result.pending_interaction.request.model_dump(mode="json"),
                "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            },
            resume_state={
                "system_prompt": "Answer as an agent.",
                "history": [],
                "temperature": 0.2,
                "executor_state": None,
            },
            started_at=datetime.now(UTC),
            execution_claim_id="old-request",
        )

    session.expire_all()
    messages = session.exec(select(ConversationMessage)).all()
    invocations = session.exec(select(AgentInvocation)).all()
    persisted_conversation = session.get(Conversation, conversation.id)
    assert [message.content for message in messages] == ["summarize a report"]
    assert [invocation.id for invocation in invocations] == [current_invocation.id]
    assert persisted_conversation is not None
    assert persisted_conversation.agent_execution_claim_id == "new-request"


def test_completed_save_preserves_concurrent_conversation_title(session):
    store = AgentInvocationStore()
    workspace, conversation = make_workspace_conversation(session)
    store.claim_execution(session, conversation, "agent-request")

    with Session(session.get_bind()) as normal_chat_session:
        normal_chat_conversation = normal_chat_session.get(
            Conversation,
            conversation.id,
        )
        assert normal_chat_conversation is not None
        normal_chat_conversation.title = "normal chat title"
        normal_chat_session.add(normal_chat_conversation)
        normal_chat_session.commit()

    result = AgentRunResult(
        message="agent request title",
        answer="completed",
        steps=[],
        agent_mode=AGENT_MODE_REACT_TEXT,
        provider="fake",
        model="fake-agent-model",
        llm_call_count=1,
        max_steps_reached=False,
    )
    store.save_completed(
        session=session,
        workspace_id=workspace.id,
        conversation=conversation,
        user_content=result.message,
        agent_result=result,
        status=AGENT_INVOCATION_STATUS_COMPLETED,
        metrics={
            **pending_metrics(),
            "step_count": 0,
            "tool_call_count": 0,
        },
        sources=[],
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        execution_claim_id="agent-request",
    )
    session.expire_all()

    persisted_conversation = session.get(Conversation, conversation.id)
    assert persisted_conversation is not None
    assert persisted_conversation.title == "normal chat title"
    assert persisted_conversation.agent_execution_claim_id is None


def test_finalize_rolls_back_after_execution_claim_is_taken_over(session):
    store = AgentInvocationStore()
    workspace, conversation = make_workspace_conversation(session)
    invocation = save_pending_invocation(store, session, workspace, conversation)
    store.claim_execution(
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
    store.claim_execution(
        session,
        conversation,
        "new-request",
        pending_invocation_id=invocation.id,
    )
    completed_result = AgentRunResult(
        message=invocation.input_message,
        answer="stale answer",
        steps=[],
        agent_mode=AGENT_MODE_REACT_TEXT,
        provider="fake",
        model="fake-agent-model",
        llm_call_count=2,
        max_steps_reached=False,
    )

    with pytest.raises(AgentInvocationConflictError, match="claim was lost"):
        store.finalize(
            session=session,
            invocation=invocation,
            conversation=conversation,
            agent_result=completed_result,
            status=AGENT_INVOCATION_STATUS_COMPLETED,
            metrics={
                **pending_metrics(),
                "llm_call_count": 2,
                "step_count": 0,
                "tool_call_count": 0,
            },
            sources=[],
            ended_at=datetime.now(UTC),
            execution_claim_id="old-request",
        )

    session.expire_all()
    persisted_invocation = session.get(AgentInvocation, invocation.id)
    persisted_conversation = session.get(Conversation, conversation.id)
    messages = session.exec(select(ConversationMessage)).all()
    assert persisted_invocation is not None
    assert persisted_invocation.status == AGENT_INVOCATION_STATUS_NEEDS_INPUT
    assert persisted_invocation.assistant_message_id is None
    assert persisted_conversation is not None
    assert persisted_conversation.agent_execution_claim_id == "new-request"
    assert [message.role for message in messages] == ["user"]
