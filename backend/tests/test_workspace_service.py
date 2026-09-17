import asyncio
import logging
from io import BytesIO
import pytest
from fastapi import UploadFile
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

from app.core.rag import RetrievedChunk
from app.models.agent import (
    AGENT_INVOCATION_STATUS_COMPLETED,
    AGENT_MODE_REACT_TEXT,
    AgentInvocation,
    AgentStepRecord,
)
from app.models.conversation import Conversation, ConversationMessage
from app.models.document import WorkspaceDocument
from app.models.workspace import Workspace, utc_now
from app.services.chat_service import ChatServiceError
from app.services.document_service import DocumentService
from app.services.rag_service import NO_CONTEXT_ANSWER, RAGIndexError
from app.services.workspace_document_service import WorkspaceDocumentService
from app.services.workspace_service import (
    ConversationNotFoundError,
    WorkspacePersistenceError,
    WorkspaceService,
)
from tests.fakes import FakeChatService, FakeRAGService


def make_upload(content: bytes = b"hello workspace") -> UploadFile:
    return UploadFile(
        filename="guide.txt",
        file=BytesIO(content),
        headers={"content-type": "text/plain"},
    )


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
        display_filename="guide.txt",
        extension=".txt",
        chunk_index=chunk_index,
        text=text,
        character_count=len(text),
        score=score,
    )


def assert_latency_metrics(metrics) -> None:
    assert metrics.retrieval_latency_ms >= 0
    assert metrics.llm_latency_ms >= 0
    assert metrics.total_latency_ms >= 0


def test_prepare_workspace_chat_context_is_reusable_without_side_effects(session):
    rag = FakeRAGService()
    chat = FakeChatService()
    service = WorkspaceService(rag=rag, chat=chat)
    workspace = service.create_workspace(
        session,
        name="Reusable context",
        system_prompt="Answer from workspace material.",
        history_limit=1,
        top_k=4,
        similarity_threshold=0.7,
    )
    conversation = service.create_conversation(session, workspace.id)
    session.add(
        ConversationMessage(
            conversation_id=conversation.id,
            role="user",
            content="prior question",
        )
    )
    session.add(
        ConversationMessage(
            conversation_id=conversation.id,
            role="assistant",
            content="prior answer",
        )
    )
    session.commit()
    rag.chunks = [make_chunk(workspace.id, text="prepared context")]

    context = asyncio.run(
        service.prepare_workspace_chat_context(
            session,
            workspace.id,
            conversation.id,
            " next question ",
        )
    )

    assert context.workspace.id == workspace.id
    assert context.conversation.id == conversation.id
    assert context.message == "next question"
    assert context.history == [
        {"role": "user", "content": "prior question"},
        {"role": "assistant", "content": "prior answer"},
    ]
    assert context.chunks == rag.chunks
    assert [source.text for source in context.sources] == ["prepared context"]
    assert context.has_context is True
    assert context.retrieved_count == 1
    assert context.dropped_count == 0
    assert context.context_char_count == len("prepared context")
    assert context.query_refused is False
    assert context.retrieval_latency_ms >= 0
    assert "prepared context" in context.system_prompt
    assert rag.retrieve_calls == [("next question", workspace.id, 4, 0.7)]
    assert chat.calls == []
    assert [
        message.content
        for message in service.list_messages(
            session,
            workspace.id,
            conversation.id,
        )
    ] == [
        "prior question",
        "prior answer",
    ]


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
    assert result.metrics.retrieved_count == 0
    assert result.metrics.used_source_count == 0
    assert result.metrics.dropped_count == 0
    assert result.metrics.context_char_count == 0
    assert result.metrics.has_context is False
    assert result.metrics.query_refused is True
    assert result.metrics.llm_called is False
    assert_latency_metrics(result.metrics)
    assert chat.calls == []
    messages = service.list_messages(session, workspace.id, conversation.id)
    assert [message.content for message in messages] == [
        "unknown question",
        NO_CONTEXT_ANSWER,
    ]
    assert messages[0].metrics == {}
    assert messages[1].metrics["query_refused"] is True
    assert messages[1].metrics["llm_called"] is False


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
    assert result.metrics.retrieved_count == 1
    assert result.metrics.used_source_count == 1
    assert result.metrics.dropped_count == 0
    assert result.metrics.context_char_count == len("Workspace-scoped context.")
    assert result.metrics.has_context is True
    assert result.metrics.query_refused is False
    assert result.metrics.llm_called is True
    assert_latency_metrics(result.metrics)
    assert assistant_message.metrics == result.metrics.model_dump(mode="json")
    assert "Workspace-scoped context." not in str(assistant_message.metrics)
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
    assert result.metrics.retrieved_count == 2
    assert result.metrics.used_source_count == 1
    assert result.metrics.dropped_count == 1
    assert result.metrics.context_char_count == len("first workspace context")
    assert assistant_message.metrics["retrieved_count"] == 2
    assert assistant_message.metrics["used_source_count"] == 1
    assert assistant_message.metrics["dropped_count"] == 1
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
    assert result.metrics.retrieved_count == 1
    assert result.metrics.used_source_count == 0
    assert result.metrics.dropped_count == 1
    assert result.metrics.context_char_count == 0
    assert result.metrics.has_context is False
    assert result.metrics.query_refused is True
    assert result.metrics.llm_called is False
    assert_latency_metrics(result.metrics)
    assert [message.content for message in messages] == [
        "question",
        NO_CONTEXT_ANSWER,
    ]
    assert messages[0].metrics == {}
    assert messages[1].metrics["query_refused"] is True
    assert messages[1].metrics["llm_called"] is False


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
    assert result.metrics.retrieved_count == 1
    assert result.metrics.used_source_count == 0
    assert result.metrics.dropped_count == 1
    assert result.metrics.context_char_count == 0
    assert result.metrics.has_context is False
    assert result.metrics.query_refused is False
    assert result.metrics.llm_called is True
    assert_latency_metrics(result.metrics)
    assert chat.calls[0]["system_prompt"] == "Answer as a study helper."


def test_delete_workspace_removes_documents_conversations_messages_and_files(
    session,
    tmp_path,
    caplog,
):
    caplog.set_level(logging.INFO)
    rag = FakeRAGService(deleted_chunk_count=3)
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    service = WorkspaceService(
        rag=rag,
        chat=FakeChatService(),
        documents=documents,
    )
    workspace_document_service = WorkspaceDocumentService(
        documents=documents,
        rag=rag,
    )
    workspace = service.create_workspace(session, name="Delete workspace")
    document = asyncio.run(
        workspace_document_service.upload_document(
            session,
            workspace.id,
            make_upload(),
        )
    )
    paths = documents.build_storage_paths(document.id, document.extension)
    conversation = service.create_conversation(session, workspace.id)
    asyncio.run(
        service.chat_in_conversation(
            session,
            workspace.id,
            conversation.id,
            "please answer from my document",
        )
    )
    messages = service.list_messages(session, workspace.id, conversation.id)
    invocation = AgentInvocation(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        user_message_id=messages[0].id,
        assistant_message_id=messages[1].id,
        input_message="please answer from my document",
        agent_mode=AGENT_MODE_REACT_TEXT,
        status=AGENT_INVOCATION_STATUS_COMPLETED,
        provider="fake",
        model="fake-model",
        max_steps=5,
        llm_call_count=2,
        step_count=1,
        tool_call_count=1,
        failed_step_count=0,
        source_count=0,
        max_steps_reached=False,
        total_latency_ms=12,
        started_at=utc_now(),
        ended_at=utc_now(),
    )
    step = AgentStepRecord(
        invocation_id=invocation.id,
        step_index=1,
        llm_output='Action: calculator\nAction Input: {"expression": "1 + 1"}',
        action="calculator",
        action_input={"expression": "1 + 1"},
        observation="2",
        ok=True,
        tool_result={
            "ok": True,
            "content": "2",
            "artifacts": {"sources": [], "outputs": {"result": 2}},
            "error": None,
            "error_details": {},
        },
    )
    session.add(invocation)
    session.add(step)
    session.commit()

    result = asyncio.run(service.delete_workspace(session, workspace.id))

    assert result.id == workspace.id
    assert result.deleted_documents == 1
    assert result.deleted_conversations == 1
    assert result.deleted_messages == 2
    assert result.deleted_chunks == 3
    assert result.upload_files_deleted == 1
    assert result.parsed_files_deleted == 1
    assert rag.delete_calls == [(document.id, workspace.id)]
    assert session.get(Workspace, workspace.id) is None
    assert session.exec(select(WorkspaceDocument)).all() == []
    assert session.exec(select(Conversation)).all() == []
    assert session.exec(select(ConversationMessage)).all() == []
    assert session.exec(select(AgentInvocation)).all() == []
    assert session.exec(select(AgentStepRecord)).all() == []
    assert not paths.upload_file.exists()
    assert not paths.parsed_file.exists()
    assert not paths.upload_file.parent.exists()
    assert not paths.parsed_file.parent.exists()

    events = [record.message for record in caplog.records]
    assert "document.upload.completed" in events
    assert "workspace.chat.completed" in events
    assert "workspace.delete.start" in events
    assert "workspace.delete.completed" in events
    assert "please answer from my document" not in caplog.text
    assert "hello workspace" not in caplog.text
    assert str(tmp_path) not in caplog.text


def test_delete_workspace_rejects_unsafe_document_paths_before_side_effects(
    session,
    tmp_path,
    caplog,
):
    caplog.set_level(logging.WARNING)
    rag = FakeRAGService()
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    service = WorkspaceService(rag=rag, chat=FakeChatService(), documents=documents)
    workspace = service.create_workspace(session, name="Unsafe delete")
    document_id = "e" * 32
    unsafe_upload_path = tmp_path / "outside.txt"
    unsafe_upload_path.write_text("do not delete", encoding="utf-8")
    paths = documents.build_storage_paths(document_id, ".txt")
    paths.upload_file.parent.mkdir(parents=True)
    paths.upload_file.symlink_to(unsafe_upload_path)
    paths.parsed_file.parent.mkdir(parents=True)
    paths.parsed_file.write_text("parsed", encoding="utf-8")
    session.add(
        WorkspaceDocument(
            id=document_id,
            workspace_id=workspace.id,
            display_filename="guide.txt",
            content_type="text/plain",
            extension=".txt",
            size_bytes=13,
            character_count=6,
            chunk_count=1,
        )
    )
    session.commit()

    with pytest.raises(WorkspacePersistenceError):
        asyncio.run(service.delete_workspace(session, workspace.id))

    assert rag.delete_calls == []
    assert session.get(Workspace, workspace.id) is not None
    assert session.get(WorkspaceDocument, document_id) is not None
    assert unsafe_upload_path.read_text(encoding="utf-8") == "do not delete"
    assert paths.parsed_file.read_text(encoding="utf-8") == "parsed"
    assert "workspace.delete.failed" in caplog.text
    assert str(unsafe_upload_path) not in caplog.text


def test_delete_workspace_keeps_files_and_database_when_index_delete_fails(
    session,
    tmp_path,
):
    rag = FakeRAGService()
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    service = WorkspaceService(rag=rag, chat=FakeChatService(), documents=documents)
    workspace_document_service = WorkspaceDocumentService(documents=documents, rag=rag)
    workspace = service.create_workspace(session, name="Index failure")
    document = asyncio.run(
        workspace_document_service.upload_document(
            session,
            workspace.id,
            make_upload(),
        )
    )
    paths = documents.build_storage_paths(document.id, document.extension)
    rag.delete_error = RAGIndexError("failed to delete indexed document")

    with pytest.raises(RAGIndexError):
        asyncio.run(service.delete_workspace(session, workspace.id))

    assert session.get(Workspace, workspace.id) is not None
    assert session.get(WorkspaceDocument, document.id) is not None
    assert paths.upload_file.read_bytes() == b"hello workspace"
    assert paths.parsed_file.read_text(encoding="utf-8") == "hello workspace"


def test_delete_workspace_keeps_database_when_file_delete_fails(
    session,
    tmp_path,
    monkeypatch,
):
    rag = FakeRAGService()
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    service = WorkspaceService(rag=rag, chat=FakeChatService(), documents=documents)
    workspace_document_service = WorkspaceDocumentService(documents=documents, rag=rag)
    workspace = service.create_workspace(session, name="File failure")
    document = asyncio.run(
        workspace_document_service.upload_document(
            session,
            workspace.id,
            make_upload(),
        )
    )
    paths = documents.build_storage_paths(document.id, document.extension)

    async def broken_file_delete(deletion_plan):
        raise ValueError("disk unavailable")

    monkeypatch.setattr(documents, "delete_document_files", broken_file_delete)

    with pytest.raises(WorkspacePersistenceError):
        asyncio.run(service.delete_workspace(session, workspace.id))

    assert rag.delete_calls == [(document.id, workspace.id)]
    assert session.get(Workspace, workspace.id) is not None
    assert session.get(WorkspaceDocument, document.id) is not None
    assert paths.upload_file.exists()
    assert paths.parsed_file.exists()


def test_delete_workspace_database_failure_does_not_restore_files_or_index(
    session,
    tmp_path,
    monkeypatch,
):
    rag = FakeRAGService()
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    service = WorkspaceService(rag=rag, chat=FakeChatService(), documents=documents)
    workspace_document_service = WorkspaceDocumentService(documents=documents, rag=rag)
    workspace = service.create_workspace(session, name="Database failure")
    document = asyncio.run(
        workspace_document_service.upload_document(
            session,
            workspace.id,
            make_upload(),
        )
    )
    paths = documents.build_storage_paths(document.id, document.extension)
    original_commit = session.commit

    def broken_commit():
        raise SQLAlchemyError("write failed")

    monkeypatch.setattr(session, "commit", broken_commit)

    with pytest.raises(WorkspacePersistenceError):
        asyncio.run(service.delete_workspace(session, workspace.id))

    monkeypatch.setattr(session, "commit", original_commit)
    assert rag.delete_calls == [(document.id, workspace.id)]
    assert not paths.upload_file.exists()
    assert not paths.parsed_file.exists()
    assert session.get(Workspace, workspace.id) is not None
    assert session.get(WorkspaceDocument, document.id) is not None


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
