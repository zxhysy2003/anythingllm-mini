import asyncio
import logging
from io import BytesIO
import pytest
from fastapi import UploadFile
from sqlalchemy.exc import SQLAlchemyError

from app.models.document import WorkspaceDocument
from app.services.document_service import DocumentService
from app.services.rag_service import RAGIndexError
from app.services.workspace_document_service import (
    WorkspaceDocumentNotFoundError,
    WorkspaceDocumentService,
)
from app.services.workspace_service import WorkspacePersistenceError, WorkspaceService
from tests.fakes import FakeChatService, FakeDocumentService, FakeRAGService


def create_workspace(session, name: str = "Documents"):
    service = WorkspaceService(rag=FakeRAGService(), chat=FakeChatService())
    return service.create_workspace(session, name=name)


def make_upload(content: bytes = b"hello workspace") -> UploadFile:
    return UploadFile(
        filename="guide.txt",
        file=BytesIO(content),
        headers={"content-type": "text/plain"},
    )


def test_workspace_document_is_indexed_and_registered(session):
    rag = FakeRAGService()
    service = WorkspaceDocumentService(
        documents=FakeDocumentService(),
        rag=rag,
    )
    workspace = create_workspace(session)

    document = asyncio.run(
        service.upload_document(session, workspace.id, make_upload())
    )

    assert document.workspace_id == workspace.id
    assert document.chunk_count == 2
    assert document.display_filename == "guide.txt"
    assert rag.index_calls[0][1] == workspace.id
    assert service.list_documents(session, workspace.id)[0].id == "d" * 32
    assert "upload_path" not in document.model_dump()


def test_workspace_document_delete_removes_index_files_and_record(
    session,
    tmp_path,
    caplog,
):
    caplog.set_level(logging.INFO)
    rag = FakeRAGService()
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    service = WorkspaceDocumentService(documents=documents, rag=rag)
    workspace = create_workspace(session, "Delete documents")
    document = asyncio.run(
        service.upload_document(session, workspace.id, make_upload())
    )
    paths = documents.build_storage_paths(document.id, document.extension)

    result = asyncio.run(service.delete_document(session, workspace.id, document.id))

    assert result.id == document.id
    assert result.workspace_id == workspace.id
    assert result.display_filename == "guide.txt"
    assert result.deleted_chunks == 2
    assert result.upload_file_deleted is True
    assert result.parsed_file_deleted is True
    assert rag.delete_calls == [(document.id, workspace.id)]
    assert service.list_documents(session, workspace.id) == []
    assert not paths.upload_file.exists()
    assert not paths.parsed_file.exists()
    assert not paths.upload_file.parent.exists()
    assert not paths.parsed_file.parent.exists()
    events = [record.message for record in caplog.records]
    assert "document.upload.completed" in events
    assert "document.delete.completed" in events
    assert str(tmp_path) not in caplog.text


def test_workspace_document_delete_allows_missing_local_files(session, tmp_path):
    rag = FakeRAGService()
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    service = WorkspaceDocumentService(documents=documents, rag=rag)
    workspace = create_workspace(session, "Missing files")
    document = asyncio.run(
        service.upload_document(session, workspace.id, make_upload())
    )
    paths = documents.build_storage_paths(document.id, document.extension)
    paths.upload_file.unlink()
    paths.parsed_file.unlink()

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
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    service = WorkspaceDocumentService(documents=documents, rag=rag)
    first_workspace = create_workspace(session, "First")
    second_workspace = create_workspace(session, "Second")
    document = asyncio.run(
        service.upload_document(session, first_workspace.id, make_upload())
    )

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
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    service = WorkspaceDocumentService(documents=documents, rag=rag)
    workspace = create_workspace(session, "RAG failure")
    document = asyncio.run(
        service.upload_document(session, workspace.id, make_upload())
    )
    paths = documents.build_storage_paths(document.id, document.extension)

    with pytest.raises(RAGIndexError):
        asyncio.run(service.delete_document(session, workspace.id, document.id))

    assert service.list_documents(session, workspace.id)[0].id == document.id
    assert paths.upload_file.read_bytes() == b"hello workspace"
    assert paths.parsed_file.read_text(encoding="utf-8") == "hello workspace"


def test_workspace_document_delete_rejects_unsafe_file_paths(session, tmp_path):
    rag = FakeRAGService()
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    service = WorkspaceDocumentService(documents=documents, rag=rag)
    workspace = create_workspace(session, "Unsafe path")
    document_id = "e" * 32
    unsafe_upload_path = tmp_path / "outside.txt"
    unsafe_upload_path.write_text("do not delete", encoding="utf-8")
    paths = documents.build_storage_paths(document_id, ".txt")
    paths.upload_file.parent.mkdir(parents=True)
    paths.upload_file.symlink_to(unsafe_upload_path)
    paths.parsed_file.parent.mkdir(parents=True)
    paths.parsed_file.write_text("parsed", encoding="utf-8")
    document = WorkspaceDocument(
        id=document_id,
        workspace_id=workspace.id,
        display_filename="guide.txt",
        content_type="text/plain",
        extension=".txt",
        size_bytes=13,
        character_count=6,
        chunk_count=1,
    )
    session.add(document)
    session.commit()

    with pytest.raises(WorkspacePersistenceError):
        asyncio.run(service.delete_document(session, workspace.id, document_id))

    assert rag.delete_calls == []
    assert unsafe_upload_path.read_text(encoding="utf-8") == "do not delete"
    assert paths.parsed_file.read_text(encoding="utf-8") == "parsed"
    assert service.list_documents(session, workspace.id)[0].id == document_id


def test_workspace_document_delete_rejects_directory_paths_before_index_delete(
    session,
    tmp_path,
):
    rag = FakeRAGService()
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    service = WorkspaceDocumentService(documents=documents, rag=rag)
    workspace = create_workspace(session, "Directory path")
    document_id = "f" * 32
    paths = documents.build_storage_paths(document_id, ".txt")
    paths.upload_file.mkdir(parents=True)
    paths.parsed_file.parent.mkdir(parents=True)
    paths.parsed_file.write_text("parsed", encoding="utf-8")
    document = WorkspaceDocument(
        id=document_id,
        workspace_id=workspace.id,
        display_filename="guide.txt",
        content_type="text/plain",
        extension=".txt",
        size_bytes=13,
        character_count=6,
        chunk_count=1,
    )
    session.add(document)
    session.commit()

    with pytest.raises(WorkspacePersistenceError):
        asyncio.run(service.delete_document(session, workspace.id, document_id))

    assert rag.delete_calls == []
    assert paths.upload_file.is_dir()
    assert paths.parsed_file.read_text(encoding="utf-8") == "parsed"
    assert service.list_documents(session, workspace.id)[0].id == document_id


def test_workspace_document_upload_cleans_index_when_database_save_fails(
    session,
    monkeypatch,
):
    rag = FakeRAGService()
    service = WorkspaceDocumentService(
        documents=FakeDocumentService(),
        rag=rag,
    )
    workspace = create_workspace(session, "Compensation")
    original_commit = session.commit

    def broken_commit():
        raise SQLAlchemyError("write failed")

    monkeypatch.setattr(session, "commit", broken_commit)
    with pytest.raises(WorkspacePersistenceError):
        asyncio.run(service.upload_document(session, workspace.id, make_upload()))

    monkeypatch.setattr(session, "commit", original_commit)
    assert rag.index_calls[0][1] == workspace.id
    assert rag.delete_calls == [("d" * 32, workspace.id)]
    assert service.list_documents(session, workspace.id) == []


def test_workspace_document_upload_keeps_index_when_refresh_fails(
    session,
    monkeypatch,
):
    rag = FakeRAGService()
    service = WorkspaceDocumentService(
        documents=FakeDocumentService(),
        rag=rag,
    )
    workspace = create_workspace(session, "Refresh failure")
    original_refresh = session.refresh

    def broken_refresh(record):
        raise SQLAlchemyError("refresh failed")

    monkeypatch.setattr(session, "refresh", broken_refresh)
    with pytest.raises(WorkspacePersistenceError, match="refresh workspace document"):
        asyncio.run(service.upload_document(session, workspace.id, make_upload()))

    monkeypatch.setattr(session, "refresh", original_refresh)
    assert rag.index_calls[0][1] == workspace.id
    assert rag.delete_calls == []
    assert service.list_documents(session, workspace.id)[0].id == "d" * 32
