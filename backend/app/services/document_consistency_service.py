from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.vectorstore import ChromaVectorStore, VectorDocumentKey, vector_store
from app.models.document import WorkspaceDocument
from app.services.document_service import DocumentService, document_service

IssueKind = Literal[
    "missing_upload_file",
    "missing_parsed_file",
    "missing_vectors",
    "orphan_upload_dir",
    "orphan_parsed_dir",
    "orphan_vectors",
    "chunk_count_mismatch",
    "unsafe_document_path",
]


class DocumentConsistencyIssue(BaseModel):
    kind: IssueKind
    workspace_id: str | None
    document_id: str | None
    message: str
    repairable: bool


class DocumentConsistencyReport(BaseModel):
    repair: bool
    counts: dict[str, int]
    issues: list[DocumentConsistencyIssue]
    repaired_actions: list[str]


class DocumentConsistencyService:
    def __init__(
        self,
        documents: DocumentService | None = None,
        store: ChromaVectorStore | None = None,
    ):
        self.documents = documents or document_service
        self.store = store or vector_store

    async def reconcile(
        self,
        session: Session,
        *,
        repair: bool = False,
    ) -> DocumentConsistencyReport:
        db_documents = self._list_database_documents(session)
        db_keys = {(document.workspace_id, document.id) for document in db_documents}
        db_document_ids = {document.id for document in db_documents}
        vector_keys = await self.store.list_document_keys()
        vector_counts = self._vector_counts(vector_keys)
        upload_document_ids = self._scan_document_dirs(self.documents.upload_dir)
        parsed_document_ids = self._scan_document_dirs(self.documents.parsed_dir)

        issues: list[DocumentConsistencyIssue] = []
        repaired_actions: list[str] = []

        for document in db_documents:
            await self._check_database_document(
                document,
                vector_counts,
                issues,
            )

        self._check_orphan_storage_dirs(
            storage_label="upload",
            storage_dir=self.documents.upload_dir,
            document_ids=upload_document_ids,
            known_document_ids=db_document_ids,
            repair=repair,
            issues=issues,
            repaired_actions=repaired_actions,
        )
        self._check_orphan_storage_dirs(
            storage_label="parsed",
            storage_dir=self.documents.parsed_dir,
            document_ids=parsed_document_ids,
            known_document_ids=db_document_ids,
            repair=repair,
            issues=issues,
            repaired_actions=repaired_actions,
        )
        await self._check_orphan_vectors(
            vector_keys,
            db_keys,
            repair,
            issues,
            repaired_actions,
        )

        counts = {
            "database_documents": len(db_documents),
            "vector_documents": len(vector_keys),
            "upload_document_dirs": len(upload_document_ids),
            "parsed_document_dirs": len(parsed_document_ids),
            "issues": len(issues),
            "repaired_actions": len(repaired_actions),
        }
        return DocumentConsistencyReport(
            repair=repair,
            counts=counts,
            issues=issues,
            repaired_actions=repaired_actions,
        )

    def _list_database_documents(self, session: Session) -> list[WorkspaceDocument]:
        statement = select(WorkspaceDocument).order_by(
            WorkspaceDocument.workspace_id.asc(),
            WorkspaceDocument.id.asc(),
        )
        return list(session.exec(statement).all())

    async def _check_database_document(
        self,
        document: WorkspaceDocument,
        vector_counts: dict[tuple[str, str], int],
        issues: list[DocumentConsistencyIssue],
    ) -> None:
        try:
            deletion_plan = await self.documents.build_document_file_deletion_plan(
                document.id,
                document.extension,
            )
        except Exception as exc:
            issues.append(
                DocumentConsistencyIssue(
                    kind="unsafe_document_path",
                    workspace_id=document.workspace_id,
                    document_id=document.id,
                    message=f"document paths are unsafe: {exc}",
                    repairable=False,
                )
            )
            deletion_plan = None

        if deletion_plan is not None:
            if not deletion_plan.upload_file.exists():
                issues.append(
                    DocumentConsistencyIssue(
                        kind="missing_upload_file",
                        workspace_id=document.workspace_id,
                        document_id=document.id,
                        message="upload file is missing",
                        repairable=False,
                    )
                )
            if not deletion_plan.parsed_file.exists():
                issues.append(
                    DocumentConsistencyIssue(
                        kind="missing_parsed_file",
                        workspace_id=document.workspace_id,
                        document_id=document.id,
                        message="parsed file is missing",
                        repairable=False,
                    )
                )

        vector_count = vector_counts.get((document.workspace_id, document.id), 0)
        if vector_count == 0:
            issues.append(
                DocumentConsistencyIssue(
                    kind="missing_vectors",
                    workspace_id=document.workspace_id,
                    document_id=document.id,
                    message="document has no indexed Chroma chunks",
                    repairable=False,
                )
            )
        elif vector_count != document.chunk_count:
            issues.append(
                DocumentConsistencyIssue(
                    kind="chunk_count_mismatch",
                    workspace_id=document.workspace_id,
                    document_id=document.id,
                    message=(
                        "database chunk_count does not match Chroma chunk count: "
                        f"{document.chunk_count} != {vector_count}"
                    ),
                    repairable=False,
                )
            )

    def _check_orphan_storage_dirs(
        self,
        *,
        storage_label: Literal["upload", "parsed"],
        storage_dir: Path,
        document_ids: set[str],
        known_document_ids: set[str],
        repair: bool,
        issues: list[DocumentConsistencyIssue],
        repaired_actions: list[str],
    ) -> None:
        for document_id in sorted(document_ids - known_document_ids):
            kind: IssueKind = (
                "orphan_upload_dir"
                if storage_label == "upload"
                else "orphan_parsed_dir"
            )
            issues.append(
                DocumentConsistencyIssue(
                    kind=kind,
                    workspace_id=None,
                    document_id=document_id,
                    message=f"{storage_label} directory has no database document",
                    repairable=True,
                )
            )
            if repair:
                self._delete_orphan_document_dir(storage_dir, document_id)
                repaired_actions.append(
                    f"deleted_orphan_{storage_label}_dir:{document_id}"
                )

    async def _check_orphan_vectors(
        self,
        vector_keys: list[VectorDocumentKey],
        db_keys: set[tuple[str, str]],
        repair: bool,
        issues: list[DocumentConsistencyIssue],
        repaired_actions: list[str],
    ) -> None:
        for key in vector_keys:
            if (key.workspace_id, key.document_id) in db_keys:
                continue

            issues.append(
                DocumentConsistencyIssue(
                    kind="orphan_vectors",
                    workspace_id=key.workspace_id,
                    document_id=key.document_id,
                    message="Chroma chunks have no database document",
                    repairable=True,
                )
            )
            if repair:
                deleted_count = await self.store.delete_document(
                    key.document_id,
                    workspace_id=key.workspace_id,
                )
                repaired_actions.append(
                    "deleted_orphan_vectors:"
                    f"{key.workspace_id}:{key.document_id}:{deleted_count}"
                )

    def _scan_document_dirs(self, storage_dir: Path) -> set[str]:
        if not storage_dir.exists():
            return set()
        return {
            path.name
            for path in storage_dir.iterdir()
            if path.is_dir() and self._is_document_id(path.name)
        }

    def _delete_orphan_document_dir(self, storage_dir: Path, document_id: str) -> None:
        if not self._is_document_id(document_id):
            raise ValueError("invalid document id")
        root = storage_dir.resolve()
        target = (storage_dir / document_id).resolve()
        if target == root or not target.is_relative_to(root):
            raise ValueError("invalid orphan document directory")
        if target.exists():
            if not target.is_dir():
                raise ValueError("orphan document path is not a directory")
            shutil.rmtree(target)

    def _vector_counts(
        self,
        vector_keys: list[VectorDocumentKey],
    ) -> dict[tuple[str, str], int]:
        return {
            (key.workspace_id, key.document_id): key.chunk_count for key in vector_keys
        }

    def _is_document_id(self, value: str) -> bool:
        return re.fullmatch(r"[0-9a-f]{32}", value) is not None


document_consistency_service = DocumentConsistencyService()
