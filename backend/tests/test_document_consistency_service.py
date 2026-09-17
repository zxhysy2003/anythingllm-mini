import asyncio
from pathlib import Path

from app.core.vectorstore import VectorDocumentKey
from app.models.document import WorkspaceDocument
from app.models.workspace import Workspace
from app.services.document_consistency_service import DocumentConsistencyService
from app.services.document_service import DocumentService


class FakeVectorStore:
    def __init__(self, keys=None):
        self.keys = list(keys or [])
        self.deleted_documents = []

    async def list_document_keys(self, workspace_id=None):
        if workspace_id is None:
            return list(self.keys)
        return [key for key in self.keys if key.workspace_id == workspace_id]

    async def delete_document(self, document_id, workspace_id):
        deleted_count = 0
        remaining = []
        for key in self.keys:
            if key.document_id == document_id and key.workspace_id == workspace_id:
                deleted_count += key.chunk_count
                continue
            remaining.append(key)
        self.keys = remaining
        self.deleted_documents.append((document_id, workspace_id))
        return deleted_count


def create_workspace(session, workspace_id: str = "1" * 32) -> Workspace:
    workspace = Workspace(id=workspace_id, name="Study")
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return workspace


def create_document_files(
    documents: DocumentService,
    document_id: str,
) -> tuple[Path, Path]:
    paths = documents.build_storage_paths(document_id, ".txt")
    upload_path = paths.upload_file
    parsed_path = paths.parsed_file
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    parsed_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_text("uploaded", encoding="utf-8")
    parsed_path.write_text("parsed", encoding="utf-8")
    return upload_path, parsed_path


def add_workspace_document(
    session,
    workspace_id: str,
    documents: DocumentService,
    document_id: str,
    *,
    chunk_count: int = 2,
) -> WorkspaceDocument:
    create_document_files(documents, document_id)

    document = WorkspaceDocument(
        id=document_id,
        workspace_id=workspace_id,
        display_filename="guide.txt",
        content_type="text/plain",
        extension=".txt",
        size_bytes=8,
        character_count=6,
        chunk_count=chunk_count,
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def vector_key(workspace_id: str, document_id: str, chunk_count: int = 2):
    return VectorDocumentKey(
        workspace_id=workspace_id,
        document_id=document_id,
        chunk_count=chunk_count,
    )


def reconcile(service, session, *, repair=False):
    return asyncio.run(service.reconcile(session, repair=repair))


def issue_kinds(report):
    return [issue.kind for issue in report.issues]


def test_document_consistency_report_has_no_issues_for_consistent_document(
    session,
    tmp_path,
):
    workspace = create_workspace(session)
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    document_id = "a" * 32
    add_workspace_document(session, workspace.id, documents, document_id)
    store = FakeVectorStore([vector_key(workspace.id, document_id)])
    service = DocumentConsistencyService(documents=documents, store=store)

    report = reconcile(service, session)

    assert report.issues == []
    assert report.repaired_actions == []
    assert report.counts == {
        "database_documents": 1,
        "vector_documents": 1,
        "upload_document_dirs": 1,
        "parsed_document_dirs": 1,
        "issues": 0,
        "repaired_actions": 0,
    }


def test_document_consistency_reports_missing_upload_and_parsed_files(
    session,
    tmp_path,
):
    workspace = create_workspace(session)
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    missing_upload_id = "b" * 32
    missing_parsed_id = "c" * 32
    first = add_workspace_document(session, workspace.id, documents, missing_upload_id)
    second = add_workspace_document(session, workspace.id, documents, missing_parsed_id)
    documents.build_storage_paths(first.id, first.extension).upload_file.unlink()
    documents.build_storage_paths(second.id, second.extension).parsed_file.unlink()
    store = FakeVectorStore(
        [
            vector_key(workspace.id, missing_upload_id),
            vector_key(workspace.id, missing_parsed_id),
        ]
    )
    service = DocumentConsistencyService(documents=documents, store=store)

    report = reconcile(service, session)

    assert issue_kinds(report) == ["missing_upload_file", "missing_parsed_file"]
    assert all(not issue.repairable for issue in report.issues)


def test_document_consistency_reports_missing_vectors_and_chunk_mismatch(
    session,
    tmp_path,
):
    workspace = create_workspace(session)
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    missing_vectors_id = "d" * 32
    mismatch_id = "e" * 32
    add_workspace_document(session, workspace.id, documents, missing_vectors_id)
    add_workspace_document(session, workspace.id, documents, mismatch_id, chunk_count=2)
    store = FakeVectorStore([vector_key(workspace.id, mismatch_id, chunk_count=1)])
    service = DocumentConsistencyService(documents=documents, store=store)

    report = reconcile(service, session)

    assert issue_kinds(report) == ["missing_vectors", "chunk_count_mismatch"]
    assert report.issues[1].message.endswith("2 != 1")


def test_document_consistency_reports_orphan_storage_without_repair(
    session,
    tmp_path,
):
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    orphan_upload_id = "a" * 32
    orphan_parsed_id = "b" * 32
    orphan_upload_dir = documents.upload_dir / orphan_upload_id
    orphan_parsed_dir = documents.parsed_dir / orphan_parsed_id
    orphan_upload_dir.mkdir(parents=True)
    orphan_parsed_dir.mkdir(parents=True)
    (orphan_upload_dir / "guide.txt").write_text("upload", encoding="utf-8")
    (orphan_parsed_dir / "guide.txt").write_text("parsed", encoding="utf-8")
    service = DocumentConsistencyService(documents=documents, store=FakeVectorStore())

    report = reconcile(service, session)

    assert issue_kinds(report) == ["orphan_upload_dir", "orphan_parsed_dir"]
    assert orphan_upload_dir.exists()
    assert orphan_parsed_dir.exists()
    assert all(issue.repairable for issue in report.issues)


def test_document_consistency_reports_orphan_vectors(session, tmp_path):
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    workspace_id = "1" * 32
    document_id = "c" * 32
    store = FakeVectorStore([vector_key(workspace_id, document_id, chunk_count=3)])
    service = DocumentConsistencyService(documents=documents, store=store)

    report = reconcile(service, session)

    assert issue_kinds(report) == ["orphan_vectors"]
    assert report.issues[0].workspace_id == workspace_id
    assert report.issues[0].document_id == document_id
    assert report.issues[0].repairable is True


def test_document_consistency_repair_deletes_orphan_storage_and_vectors(
    session,
    tmp_path,
):
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    orphan_upload_id = "d" * 32
    orphan_parsed_id = "e" * 32
    orphan_upload_dir = documents.upload_dir / orphan_upload_id
    orphan_parsed_dir = documents.parsed_dir / orphan_parsed_id
    orphan_upload_dir.mkdir(parents=True)
    orphan_parsed_dir.mkdir(parents=True)
    (orphan_upload_dir / "guide.txt").write_text("upload", encoding="utf-8")
    (orphan_parsed_dir / "guide.txt").write_text("parsed", encoding="utf-8")
    workspace_id = "1" * 32
    orphan_vector_id = "f" * 32
    store = FakeVectorStore([vector_key(workspace_id, orphan_vector_id, chunk_count=4)])
    service = DocumentConsistencyService(documents=documents, store=store)

    report = reconcile(service, session, repair=True)

    assert issue_kinds(report) == [
        "orphan_upload_dir",
        "orphan_parsed_dir",
        "orphan_vectors",
    ]
    assert not orphan_upload_dir.exists()
    assert not orphan_parsed_dir.exists()
    assert store.deleted_documents == [(orphan_vector_id, workspace_id)]
    assert report.repaired_actions == [
        f"deleted_orphan_upload_dir:{orphan_upload_id}",
        f"deleted_orphan_parsed_dir:{orphan_parsed_id}",
        f"deleted_orphan_vectors:{workspace_id}:{orphan_vector_id}:4",
    ]


def test_document_consistency_never_repairs_unsafe_paths(
    session,
    tmp_path,
):
    workspace = create_workspace(session)
    documents = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    document_id = "f" * 32
    document = add_workspace_document(session, workspace.id, documents, document_id)
    paths = documents.build_storage_paths(document.id, document.extension)
    paths.upload_file.unlink()
    outside_path = tmp_path / "outside.txt"
    outside_path.write_text("outside", encoding="utf-8")
    paths.upload_file.symlink_to(outside_path)
    store = FakeVectorStore([vector_key(workspace.id, document_id)])
    service = DocumentConsistencyService(documents=documents, store=store)

    report = reconcile(service, session, repair=True)

    assert issue_kinds(report) == ["unsafe_document_path"]
    assert report.repaired_actions == []
    assert outside_path.read_text(encoding="utf-8") == "outside"
    assert store.deleted_documents == []
