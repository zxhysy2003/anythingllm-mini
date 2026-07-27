import logging
from contextlib import suppress

from fastapi import UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.core.safe_strings import select_safe_basename
from app.models.document import WorkspaceDocument
from app.models.workspace import Workspace
from app.services.document_service import DocumentService, document_service
from app.services.exceptions import (
    WorkspaceDocumentAmbiguousError,
    WorkspaceDocumentNotFoundError,
    WorkspaceNotFoundError,
    WorkspacePersistenceError,
)
from app.services.rag_service import RAGService, rag_service

logger = logging.getLogger(__name__)


def document_display_filename(
    original_filename: str,
    stored_filename: str,
) -> str:
    return select_safe_basename(
        (original_filename, stored_filename),
        field_name="original_filename",
    )


class WorkspaceDocumentDeleteResult(BaseModel):
    id: str
    workspace_id: str
    original_filename: str
    deleted_chunks: int
    upload_path: str
    parsed_path: str
    upload_file_deleted: bool
    parsed_file_deleted: bool


class WorkspaceDocumentService:
    def __init__(
        self,
        documents: DocumentService | None = None,
        rag: RAGService | None = None,
    ):
        self.documents = documents or document_service
        self.rag = rag or rag_service

    def list_documents(
        self,
        session: Session,
        workspace_id: str,
    ) -> list[WorkspaceDocument]:
        self._get_workspace(session, workspace_id)
        statement = (
            select(WorkspaceDocument)
            .where(WorkspaceDocument.workspace_id == workspace_id)
            .order_by(WorkspaceDocument.created_at.desc())
        )
        return list(session.exec(statement).all())

    def resolve_document(
        self,
        session: Session,
        workspace_id: str,
        *,
        document_id: str | None = None,
        filename: str | None = None,
    ) -> WorkspaceDocument:
        self._get_workspace(session, workspace_id)
        if (document_id is None) == (filename is None):
            raise ValueError("exactly one document selector is required")
        if document_id is not None:
            return self._get_workspace_document(session, workspace_id, document_id)

        candidates = list(
            session.exec(
                select(WorkspaceDocument).where(
                    WorkspaceDocument.workspace_id == workspace_id,
                )
            ).all()
        )
        documents = [
            document
            for document in candidates
            if document_display_filename(
                document.original_filename,
                document.stored_filename,
            )
            == filename
        ]
        if not documents:
            raise WorkspaceDocumentNotFoundError(
                f"document not found in workspace: {filename}"
            )
        if len(documents) > 1:
            raise WorkspaceDocumentAmbiguousError(
                filename or "",
                sorted(document.id for document in documents),
            )
        return documents[0]

    async def read_document_text(self, document: WorkspaceDocument) -> str:
        return await self.documents.read_parsed_text(
            document.id,
            document.parsed_path,
            expected_character_count=document.character_count,
        )

    async def upload_document(
        self,
        session: Session,
        workspace_id: str,
        file: UploadFile,
    ) -> WorkspaceDocument:
        self._get_workspace(session, workspace_id)
        saved_file = await self.documents.save_upload_file(file)
        parsed_file = await self.documents.parse_saved_file(saved_file)
        indexed = await self.rag.index_document(
            parsed_file,
            workspace_id=workspace_id,
        )

        document = WorkspaceDocument(
            id=saved_file.id,
            workspace_id=workspace_id,
            original_filename=saved_file.original_filename,
            stored_filename=saved_file.stored_filename,
            content_type=saved_file.content_type,
            extension=saved_file.extension,
            size_bytes=saved_file.size_bytes,
            character_count=parsed_file.character_count,
            upload_path=saved_file.upload_path,
            parsed_path=parsed_file.parsed_path,
            chunk_count=indexed.chunk_count,
        )
        session.add(document)
        try:
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            with suppress(Exception):
                await self.rag.delete_document(saved_file.id, workspace_id=workspace_id)
            raise WorkspacePersistenceError("failed to save workspace data") from exc

        try:
            session.refresh(document)
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError(
                "failed to refresh workspace document"
            ) from exc
        logger.info(
            "document.upload.completed",
            extra={
                "event": "document.upload.completed",
                "workspace_id": workspace_id,
                "document_id": document.id,
                "chunk_count": document.chunk_count,
            },
        )
        return document

    async def delete_document(
        self,
        session: Session,
        workspace_id: str,
        document_id: str,
    ) -> WorkspaceDocumentDeleteResult:
        self._get_workspace(session, workspace_id)
        document = self._get_workspace_document(session, workspace_id, document_id)

        try:
            deletion_plan = await self.documents.build_document_file_deletion_plan(
                document.id,
                document.upload_path,
                document.parsed_path,
            )
        except Exception as exc:
            raise WorkspacePersistenceError(
                "failed to delete workspace document files"
            ) from exc

        deleted_chunks = await self.rag.delete_document(
            document_id,
            workspace_id=workspace_id,
        )
        try:
            deleted_files = await self.documents.delete_document_files(deletion_plan)
        except Exception as exc:
            raise WorkspacePersistenceError(
                "failed to delete workspace document files"
            ) from exc

        result = WorkspaceDocumentDeleteResult(
            id=document.id,
            workspace_id=document.workspace_id,
            original_filename=document.original_filename,
            deleted_chunks=deleted_chunks,
            upload_path=deleted_files.upload_path,
            parsed_path=deleted_files.parsed_path,
            upload_file_deleted=deleted_files.upload_file_deleted,
            parsed_file_deleted=deleted_files.parsed_file_deleted,
        )
        session.delete(document)
        try:
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError(
                "failed to delete workspace document"
            ) from exc
        logger.info(
            "document.delete.completed",
            extra={
                "event": "document.delete.completed",
                "workspace_id": workspace_id,
                "document_id": document_id,
                "deleted_chunks": deleted_chunks,
                "upload_file_deleted": result.upload_file_deleted,
                "parsed_file_deleted": result.parsed_file_deleted,
            },
        )
        return result

    def _get_workspace(self, session: Session, workspace_id: str) -> Workspace:
        workspace = session.get(Workspace, workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"workspace not found: {workspace_id}")
        return workspace

    def _get_workspace_document(
        self,
        session: Session,
        workspace_id: str,
        document_id: str,
    ) -> WorkspaceDocument:
        statement = select(WorkspaceDocument).where(
            WorkspaceDocument.id == document_id,
            WorkspaceDocument.workspace_id == workspace_id,
        )
        document = session.exec(statement).first()
        if document is None:
            raise WorkspaceDocumentNotFoundError(
                f"document not found in workspace: {document_id}"
            )
        return document


workspace_document_service = WorkspaceDocumentService()
