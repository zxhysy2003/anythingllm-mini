import asyncio
from io import BytesIO
from pathlib import Path

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
    assert rag.index_calls[0][1] == workspace.id
    assert service.list_documents(session, workspace.id)[0].id == "d" * 32
    assert Path(document.upload_path).name == "guide.txt"


def test_workspace_document_delete_removes_index_files_and_record(session, tmp_path):
    rag = FakeRAGService()
    service = WorkspaceDocumentService(
        documents=DocumentService(
            upload_dir=tmp_path / "uploads",
            parsed_dir=tmp_path / "parsed",
        ),
        rag=rag,
    )
    workspace = create_workspace(session, "Delete documents")
    document = asyncio.run(
        service.upload_document(session, workspace.id, make_upload())
    )
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
    service = WorkspaceDocumentService(
        documents=DocumentService(
            upload_dir=tmp_path / "uploads",
            parsed_dir=tmp_path / "parsed",
        ),
        rag=rag,
    )
    workspace = create_workspace(session, "Missing files")
    document = asyncio.run(
        service.upload_document(session, workspace.id, make_upload())
    )
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
    service = WorkspaceDocumentService(
        documents=DocumentService(
            upload_dir=tmp_path / "uploads",
            parsed_dir=tmp_path / "parsed",
        ),
        rag=rag,
    )
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
    service = WorkspaceDocumentService(
        documents=DocumentService(
            upload_dir=tmp_path / "uploads",
            parsed_dir=tmp_path / "parsed",
        ),
        rag=rag,
    )
    workspace = create_workspace(session, "RAG failure")
    document = asyncio.run(
        service.upload_document(session, workspace.id, make_upload())
    )
    upload_path = Path(document.upload_path)
    parsed_path = Path(document.parsed_path)

    with pytest.raises(RAGIndexError):
        asyncio.run(service.delete_document(session, workspace.id, document.id))

    assert service.list_documents(session, workspace.id)[0].id == document.id
    assert upload_path.read_bytes() == b"hello workspace"
    assert parsed_path.read_text(encoding="utf-8") == "hello workspace"


def test_workspace_document_delete_rejects_unsafe_file_paths(session, tmp_path):
    rag = FakeRAGService()
    service = WorkspaceDocumentService(
        documents=DocumentService(
            upload_dir=tmp_path / "uploads",
            parsed_dir=tmp_path / "parsed",
        ),
        rag=rag,
    )
    workspace = create_workspace(session, "Unsafe path")
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
    service = WorkspaceDocumentService(
        documents=DocumentService(
            upload_dir=tmp_path / "uploads",
            parsed_dir=tmp_path / "parsed",
        ),
        rag=rag,
    )
    workspace = create_workspace(session, "Directory path")
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
