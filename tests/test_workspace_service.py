import asyncio

import pytest

from app.core.rag import RetrievedChunk
from app.services.chat_service import ChatServiceError
from app.services.rag_service import NO_CONTEXT_ANSWER
from app.services.workspace_service import (
    ConversationNotFoundError,
    WorkspacePersistenceError,
    WorkspaceService,
)
from tests.fakes import FakeChatService, FakeRAGService


def make_chunk(
    workspace_id: str,
    *,
    text: str = "Workspace-scoped context.",
    chunk_index: int = 0,
    score: float = 0.93,
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


def test_workspace_chat_loads_limited_history_and_auto_titles(session):
    rag = FakeRAGService()
    chat = FakeChatService()
    service = WorkspaceService(rag=rag, chat=chat)
    workspace = service.create_workspace(
        session,
        name="Study",
        history_limit=1,
        top_k=3,
        similarity_threshold=0.8,
    )
    conversation = service.create_conversation(session, workspace.id)

    asyncio.run(
        service.chat_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "first question",
        )
    )
    asyncio.run(
        service.chat_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "second question",
        )
    )
    asyncio.run(
        service.chat_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "third question",
        )
    )

    assert chat.calls[0]["history"] == []
    assert chat.calls[1]["history"] == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "answer-1"},
    ]
    assert chat.calls[2]["history"] == [
        {"role": "user", "content": "second question"},
        {"role": "assistant", "content": "answer-2"},
    ]
    assert rag.retrieve_calls[-1] == (
        "third question",
        workspace.id,
        3,
        0.8,
    )

    conversations = service.list_conversations(session, workspace.id)
    messages = service.list_messages(session, workspace.id, conversation.id)
    assert conversations[0].title == "first question"
    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_query_mode_without_context_skips_llm_and_saves_refusal(session):
    chat = FakeChatService()
    service = WorkspaceService(rag=FakeRAGService(), chat=chat)
    workspace = service.create_workspace(
        session,
        name="Strict knowledge base",
        chat_mode="query",
    )
    conversation = service.create_conversation(session, workspace.id)

    result = asyncio.run(
        service.chat_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "unknown question",
        )
    )

    assert result.answer == NO_CONTEXT_ANSWER
    assert result.provider is None
    assert result.sources == []
    assert chat.calls == []
    messages = service.list_messages(session, workspace.id, conversation.id)
    assert [message.content for message in messages] == [
        "unknown question",
        NO_CONTEXT_ANSWER,
    ]


def test_rag_sources_and_model_metadata_are_persisted(session):
    service = WorkspaceService(rag=FakeRAGService(), chat=FakeChatService())
    workspace = service.create_workspace(session, name="RAG workspace")
    service.rag.chunks = [make_chunk(workspace.id)]
    conversation = service.create_conversation(session, workspace.id)

    result = asyncio.run(
        service.chat_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "question",
        )
    )

    messages = service.list_messages(session, workspace.id, conversation.id)
    assistant_message = messages[1]
    assert result.sources[0].document_id == "a" * 32
    assert assistant_message.sources[0]["score"] == 0.93
    assert assistant_message.provider == "deepseek"
    assert assistant_message.model == "deepseek-v4-flash"
    assert "Workspace-scoped context." in service.chat.calls[0]["system_prompt"]


def test_workspace_chat_returns_and_persists_only_budgeted_sources(session):
    chat = FakeChatService()
    rag = FakeRAGService(context_chunk_limit=1)
    service = WorkspaceService(rag=rag, chat=chat)
    workspace = service.create_workspace(session, name="Budgeted RAG workspace")
    first_chunk = make_chunk(
        workspace.id,
        text="first workspace context",
        chunk_index=0,
        score=0.99,
    )
    second_chunk = make_chunk(
        workspace.id,
        text="second workspace context",
        chunk_index=1,
        score=0.98,
    )
    rag.chunks = [first_chunk, second_chunk]
    conversation = service.create_conversation(session, workspace.id)

    result = asyncio.run(
        service.chat_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "question",
        )
    )

    messages = service.list_messages(session, workspace.id, conversation.id)
    assistant_message = messages[1]
    assert [source.text for source in result.sources] == ["first workspace context"]
    assert [source["text"] for source in assistant_message.sources] == [
        "first workspace context"
    ]
    assert "first workspace context" in chat.calls[0]["system_prompt"]
    assert "second workspace context" not in chat.calls[0]["system_prompt"]


def test_query_mode_without_budgeted_context_skips_llm_and_saves_refusal(session):
    chat = FakeChatService()
    rag = FakeRAGService(context_chunk_limit=0)
    service = WorkspaceService(rag=rag, chat=chat)
    workspace = service.create_workspace(
        session,
        name="Budgeted query workspace",
        chat_mode="query",
    )
    rag.chunks = [make_chunk(workspace.id)]
    conversation = service.create_conversation(session, workspace.id)

    result = asyncio.run(
        service.chat_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "question",
        )
    )

    messages = service.list_messages(session, workspace.id, conversation.id)
    assert result.answer == NO_CONTEXT_ANSWER
    assert result.sources == []
    assert result.provider is None
    assert chat.calls == []
    assert [message.content for message in messages] == [
        "question",
        NO_CONTEXT_ANSWER,
    ]


def test_chat_mode_without_budgeted_context_falls_back_to_workspace_prompt(session):
    chat = FakeChatService()
    rag = FakeRAGService(context_chunk_limit=0)
    service = WorkspaceService(rag=rag, chat=chat)
    workspace = service.create_workspace(
        session,
        name="Budgeted chat workspace",
        system_prompt="Answer as a study helper.",
    )
    rag.chunks = [make_chunk(workspace.id)]
    conversation = service.create_conversation(session, workspace.id)

    result = asyncio.run(
        service.chat_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "question",
        )
    )

    assert result.answer == "answer-1"
    assert result.sources == []
    assert chat.calls[0]["system_prompt"] == "Answer as a study helper."


def test_llm_failure_does_not_save_partial_exchange(session):
    class BrokenChatService(FakeChatService):
        async def chat(self, message, system_prompt, history, temperature):
            raise ChatServiceError("DeepSeek chat failed: timeout")

    service = WorkspaceService(rag=FakeRAGService(), chat=BrokenChatService())
    workspace = service.create_workspace(session, name="Failure test")
    conversation = service.create_conversation(session, workspace.id)

    with pytest.raises(ChatServiceError):
        asyncio.run(
            service.chat_in_conversation(
                session,
                workspace.id,
                conversation.id,
                "question",
            )
        )

    assert service.list_messages(session, workspace.id, conversation.id) == []


def test_database_failure_rolls_back_both_messages(session, monkeypatch):
    service = WorkspaceService(rag=FakeRAGService(), chat=FakeChatService())
    workspace = service.create_workspace(session, name="Rollback test")
    conversation = service.create_conversation(session, workspace.id)
    original_commit = session.commit

    def broken_commit():
        from sqlalchemy.exc import SQLAlchemyError

        raise SQLAlchemyError("write failed")

    monkeypatch.setattr(session, "commit", broken_commit)
    with pytest.raises(WorkspacePersistenceError):
        asyncio.run(
            service.chat_in_conversation(
                session,
                workspace.id,
                conversation.id,
                "question",
            )
        )

    monkeypatch.setattr(session, "commit", original_commit)
    assert service.list_messages(session, workspace.id, conversation.id) == []


def test_conversation_must_belong_to_workspace(session):
    service = WorkspaceService(rag=FakeRAGService(), chat=FakeChatService())
    first = service.create_workspace(session, name="First")
    second = service.create_workspace(session, name="Second")
    conversation = service.create_conversation(session, first.id)

    with pytest.raises(ConversationNotFoundError):
        service.list_messages(session, second.id, conversation.id)
