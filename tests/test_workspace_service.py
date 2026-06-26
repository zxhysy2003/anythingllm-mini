import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.rag import RetrievedChunk
from app.models.document import WorkspaceDocument
from app.services.chat_service import ChatResult, ChatServiceError
from app.services.document_service import (
    DeletedDocumentFiles,
    DocumentService,
    ParsedDocumentFile,
    SavedDocumentFile,
)
from app.services.rag_service import (
    IndexedDocument,
    NO_CONTEXT_ANSWER,
    RAGIndexError,
    RAGSource,
)
from app.services.workspace_service import (
    ConversationNotFoundError,
    WorkspaceDocumentNotFoundError,
    WorkspacePersistenceError,
    WorkspaceService,
)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session


class FakeRAGService:
    def __init__(self, chunks=None):
        self.chunks = chunks or []
        self.retrieve_calls = []
        self.index_calls = []
        self.delete_calls = []
        self.delete_error = None

    async def retrieve(
        self,
        question,
        workspace_id,
        top_k,
        similarity_threshold,
    ):
        self.retrieve_calls.append(
            (question, workspace_id, top_k, similarity_threshold)
        )
        return self.chunks

    async def index_document(self, parsed_file, workspace_id):
        self.index_calls.append((parsed_file, workspace_id))
        return IndexedDocument(
            document_id=parsed_file.id,
            chunk_count=2,
        )

    async def delete_document(self, document_id, workspace_id):
        if self.delete_error is not None:
            raise self.delete_error
        self.delete_calls.append((document_id, workspace_id))
        return 2

    def to_source(self, chunk):
        return RAGSource(
            document_id=chunk.document_id,
            original_filename=chunk.original_filename,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            score=chunk.score,
        )

    def build_system_prompt(self, chunks, base_prompt):
        return f"{base_prompt}\n\nContext: {chunks[0].text}"


class FakeChatService:
    def __init__(self):
        self.calls = []

    async def chat(
        self,
        message,
        system_prompt,
        history,
        temperature,
    ):
        self.calls.append(
            {
                "message": message,
                "system_prompt": system_prompt,
                "history": history,
                "temperature": temperature,
            }
        )
        return ChatResult(
            message=message,
            answer=f"answer-{len(self.calls)}",
            provider="deepseek",
            model="deepseek-v4-flash",
        )


class FakeDocumentService:
    async def save_upload_file(self, file):
        return SavedDocumentFile(
            id="d" * 32,
            original_filename=file.filename,
            stored_filename="guide.txt",
            content_type=file.content_type,
            size_bytes=5,
            upload_path="/tmp/uploads/guide.txt",
            extension=".txt",
        )

    async def parse_saved_file(self, saved_file):
        return ParsedDocumentFile(
            id=saved_file.id,
            original_filename=saved_file.original_filename,
            stored_filename=saved_file.stored_filename,
            extension=saved_file.extension,
            text="hello",
            character_count=5,
            parsed_path="/tmp/parsed/guide.txt",
        )

    async def delete_document_files(self, document_id, upload_path, parsed_path):
        return DeletedDocumentFiles(
            upload_path=upload_path,
            parsed_path=parsed_path,
            upload_file_deleted=True,
            parsed_file_deleted=True,
        )

    async def validate_document_file_paths(self, document_id, upload_path, parsed_path):
        return None


def make_chunk(workspace_id: str) -> RetrievedChunk:
    return RetrievedChunk(
        id=f"{'a' * 32}:0",
        document_id="a" * 32,
        workspace_id=workspace_id,
        original_filename="guide.txt",
        stored_filename="guide.txt",
        extension=".txt",
        chunk_index=0,
        text="Workspace-scoped context.",
        character_count=25,
        score=0.93,
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


def test_workspace_document_is_indexed_and_registered(session):
    rag = FakeRAGService()
    service = WorkspaceService(
        documents=FakeDocumentService(),
        rag=rag,
        chat=FakeChatService(),
    )
    workspace = service.create_workspace(session, name="Documents")
    upload = UploadFile(
        filename="guide.txt",
        file=BytesIO(b"hello"),
        headers={"content-type": "text/plain"},
    )

    document = asyncio.run(service.upload_document(session, workspace.id, upload))

    assert document.workspace_id == workspace.id
    assert document.chunk_count == 2
    assert rag.index_calls[0][1] == workspace.id
    assert service.list_documents(session, workspace.id)[0].id == "d" * 32
    assert Path(document.upload_path).name == "guide.txt"


def test_workspace_document_delete_removes_index_files_and_record(session, tmp_path):
    rag = FakeRAGService()
    service = WorkspaceService(
        documents=DocumentService(
            upload_dir=tmp_path / "uploads",
            parsed_dir=tmp_path / "parsed",
        ),
        rag=rag,
        chat=FakeChatService(),
    )
    workspace = service.create_workspace(session, name="Delete documents")
    upload = UploadFile(
        filename="guide.txt",
        file=BytesIO(b"hello workspace"),
        headers={"content-type": "text/plain"},
    )
    document = asyncio.run(service.upload_document(session, workspace.id, upload))
    upload_path = Path(document.upload_path)
    parsed_path = Path(document.parsed_path)

    result = asyncio.run(service.delete_document(session, workspace.id, document.id))

    assert result.id == document.id
    assert result.workspace_id == workspace.id
    assert result.original_filename == "guide.txt"
    assert result.deleted_chunks == 2
    assert result.upload_file_deleted is True
    assert result.parsed_file_deleted is True
    assert rag.delete_calls == [(document.id, workspace.id)]
    assert service.list_documents(session, workspace.id) == []
    assert not upload_path.exists()
    assert not parsed_path.exists()
    assert not upload_path.parent.exists()
    assert not parsed_path.parent.exists()


def test_workspace_document_delete_allows_missing_local_files(session, tmp_path):
    rag = FakeRAGService()
    service = WorkspaceService(
        documents=DocumentService(
            upload_dir=tmp_path / "uploads",
            parsed_dir=tmp_path / "parsed",
        ),
        rag=rag,
        chat=FakeChatService(),
    )
    workspace = service.create_workspace(session, name="Missing files")
    upload = UploadFile(
        filename="guide.txt",
        file=BytesIO(b"hello workspace"),
        headers={"content-type": "text/plain"},
    )
    document = asyncio.run(service.upload_document(session, workspace.id, upload))
    Path(document.upload_path).unlink()
    Path(document.parsed_path).unlink()

    result = asyncio.run(service.delete_document(session, workspace.id, document.id))

    assert result.upload_file_deleted is False
    assert result.parsed_file_deleted is False
    assert rag.delete_calls == [(document.id, workspace.id)]
    assert service.list_documents(session, workspace.id) == []


def test_workspace_document_delete_requires_document_to_belong_to_workspace(
    session,
    tmp_path,
):
    rag = FakeRAGService()
    service = WorkspaceService(
        documents=DocumentService(
            upload_dir=tmp_path / "uploads",
            parsed_dir=tmp_path / "parsed",
        ),
        rag=rag,
        chat=FakeChatService(),
    )
    first_workspace = service.create_workspace(session, name="First")
    second_workspace = service.create_workspace(session, name="Second")
    upload = UploadFile(
        filename="guide.txt",
        file=BytesIO(b"hello workspace"),
        headers={"content-type": "text/plain"},
    )
    document = asyncio.run(service.upload_document(session, first_workspace.id, upload))

    with pytest.raises(WorkspaceDocumentNotFoundError):
        asyncio.run(service.delete_document(session, second_workspace.id, document.id))

    assert rag.delete_calls == []
    assert service.list_documents(session, first_workspace.id)[0].id == document.id


def test_workspace_document_delete_keeps_state_when_index_delete_fails(
    session,
    tmp_path,
):
    rag = FakeRAGService()
    rag.delete_error = RAGIndexError("failed to delete indexed document")
    service = WorkspaceService(
        documents=DocumentService(
            upload_dir=tmp_path / "uploads",
            parsed_dir=tmp_path / "parsed",
        ),
        rag=rag,
        chat=FakeChatService(),
    )
    workspace = service.create_workspace(session, name="RAG failure")
    upload = UploadFile(
        filename="guide.txt",
        file=BytesIO(b"hello workspace"),
        headers={"content-type": "text/plain"},
    )
    document = asyncio.run(service.upload_document(session, workspace.id, upload))
    upload_path = Path(document.upload_path)
    parsed_path = Path(document.parsed_path)

    with pytest.raises(RAGIndexError):
        asyncio.run(service.delete_document(session, workspace.id, document.id))

    assert service.list_documents(session, workspace.id)[0].id == document.id
    assert upload_path.read_bytes() == b"hello workspace"
    assert parsed_path.read_text(encoding="utf-8") == "hello workspace"


def test_workspace_document_delete_rejects_unsafe_file_paths(session, tmp_path):
    rag = FakeRAGService()
    service = WorkspaceService(
        documents=DocumentService(
            upload_dir=tmp_path / "uploads",
            parsed_dir=tmp_path / "parsed",
        ),
        rag=rag,
        chat=FakeChatService(),
    )
    workspace = service.create_workspace(session, name="Unsafe path")
    document_id = "e" * 32
    unsafe_upload_path = tmp_path / "outside.txt"
    unsafe_upload_path.write_text("do not delete", encoding="utf-8")
    parsed_dir = tmp_path / "parsed" / document_id
    parsed_dir.mkdir(parents=True)
    parsed_path = parsed_dir / "guide.txt"
    parsed_path.write_text("parsed", encoding="utf-8")
    document = WorkspaceDocument(
        id=document_id,
        workspace_id=workspace.id,
        original_filename="guide.txt",
        stored_filename="guide.txt",
        content_type="text/plain",
        extension=".txt",
        size_bytes=13,
        character_count=6,
        upload_path=str(unsafe_upload_path),
        parsed_path=str(parsed_path),
        chunk_count=1,
    )
    session.add(document)
    session.commit()

    with pytest.raises(WorkspacePersistenceError):
        asyncio.run(service.delete_document(session, workspace.id, document_id))

    assert rag.delete_calls == []
    assert unsafe_upload_path.read_text(encoding="utf-8") == "do not delete"
    assert parsed_path.read_text(encoding="utf-8") == "parsed"
    assert service.list_documents(session, workspace.id)[0].id == document_id


def test_workspace_document_delete_rejects_directory_paths_before_index_delete(
    session,
    tmp_path,
):
    rag = FakeRAGService()
    service = WorkspaceService(
        documents=DocumentService(
            upload_dir=tmp_path / "uploads",
            parsed_dir=tmp_path / "parsed",
        ),
        rag=rag,
        chat=FakeChatService(),
    )
    workspace = service.create_workspace(session, name="Directory path")
    document_id = "f" * 32
    upload_path = tmp_path / "uploads" / document_id / "not-a-file"
    parsed_path = tmp_path / "parsed" / document_id / "guide.txt"
    upload_path.mkdir(parents=True)
    parsed_path.parent.mkdir(parents=True)
    parsed_path.write_text("parsed", encoding="utf-8")
    document = WorkspaceDocument(
        id=document_id,
        workspace_id=workspace.id,
        original_filename="guide.txt",
        stored_filename="guide.txt",
        content_type="text/plain",
        extension=".txt",
        size_bytes=13,
        character_count=6,
        upload_path=str(upload_path),
        parsed_path=str(parsed_path),
        chunk_count=1,
    )
    session.add(document)
    session.commit()

    with pytest.raises(WorkspacePersistenceError):
        asyncio.run(service.delete_document(session, workspace.id, document_id))

    assert rag.delete_calls == []
    assert upload_path.is_dir()
    assert parsed_path.read_text(encoding="utf-8") == "parsed"
    assert service.list_documents(session, workspace.id)[0].id == document_id


def test_workspace_document_upload_cleans_index_when_database_save_fails(
    session,
    monkeypatch,
):
    rag = FakeRAGService()
    service = WorkspaceService(
        documents=FakeDocumentService(),
        rag=rag,
        chat=FakeChatService(),
    )
    workspace = service.create_workspace(session, name="Compensation")
    upload = UploadFile(
        filename="guide.txt",
        file=BytesIO(b"hello"),
        headers={"content-type": "text/plain"},
    )
    original_commit = session.commit

    def broken_commit():
        from sqlalchemy.exc import SQLAlchemyError

        raise SQLAlchemyError("write failed")

    monkeypatch.setattr(session, "commit", broken_commit)
    with pytest.raises(WorkspacePersistenceError):
        asyncio.run(service.upload_document(session, workspace.id, upload))

    monkeypatch.setattr(session, "commit", original_commit)
    assert rag.index_calls[0][1] == workspace.id
    assert rag.delete_calls == [("d" * 32, workspace.id)]
    assert service.list_documents(session, workspace.id) == []


def test_workspace_document_upload_keeps_index_when_refresh_fails(
    session,
    monkeypatch,
):
    rag = FakeRAGService()
    service = WorkspaceService(
        documents=FakeDocumentService(),
        rag=rag,
        chat=FakeChatService(),
    )
    workspace = service.create_workspace(session, name="Refresh failure")
    upload = UploadFile(
        filename="guide.txt",
        file=BytesIO(b"hello"),
        headers={"content-type": "text/plain"},
    )
    original_refresh = session.refresh

    def broken_refresh(record):
        from sqlalchemy.exc import SQLAlchemyError

        raise SQLAlchemyError("refresh failed")

    monkeypatch.setattr(session, "refresh", broken_refresh)
    with pytest.raises(WorkspacePersistenceError, match="refresh workspace document"):
        asyncio.run(service.upload_document(session, workspace.id, upload))

    monkeypatch.setattr(session, "refresh", original_refresh)
    assert rag.index_calls[0][1] == workspace.id
    assert rag.delete_calls == []
    assert service.list_documents(session, workspace.id)[0].id == "d" * 32
