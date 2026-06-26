from contextlib import suppress
from typing import Any

from fastapi import UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.core.config import settings
from app.core.llm import ChatMessage, DEFAULT_SYSTEM_PROMPT
from app.models.conversation import (
    DEFAULT_CONVERSATION_TITLE,
    Conversation,
    ConversationMessage,
)
from app.models.document import WorkspaceDocument
from app.models.workspace import Workspace, utc_now
from app.services.chat_service import ChatService, chat_service
from app.services.document_service import (
    DocumentService,
    document_service,
)
from app.services.rag_service import (
    NO_CONTEXT_ANSWER,
    RAGService,
    RAGSource,
    rag_service,
)

AUTO_TITLE_LENGTH = 50


class WorkspaceNotFoundError(LookupError):
    """Raised when a workspace id does not exist."""


class ConversationNotFoundError(LookupError):
    """Raised when a conversation does not belong to the workspace."""


class WorkspaceDocumentNotFoundError(LookupError):
    """Raised when a document does not belong to the workspace."""


class WorkspacePersistenceError(RuntimeError):
    """Raised when V3 metadata or messages cannot be persisted."""


class WorkspaceChatResult(BaseModel):
    conversation_id: str
    message: str
    answer: str
    sources: list[RAGSource]
    provider: str | None
    model: str | None


class WorkspaceDocumentDeleteResult(BaseModel):
    id: str
    workspace_id: str
    original_filename: str
    deleted_chunks: int
    upload_path: str
    parsed_path: str
    upload_file_deleted: bool
    parsed_file_deleted: bool


class WorkspaceService:
    def __init__(
        self,
        documents: DocumentService | None = None,
        rag: RAGService | None = None,
        chat: ChatService | None = None,
    ):
        self.documents = documents or document_service
        self.rag = rag or rag_service
        self.chat = chat or chat_service

    def create_workspace(
        self,
        session: Session,
        *,
        name: str,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        temperature: float = 0.7,
        history_limit: int = 20,
        chat_mode: str = "chat",
        top_k: int | None = None,
        similarity_threshold: float | None = None,
    ) -> Workspace:
        workspace = Workspace(
            name=self._require_text(name, "name"),
            system_prompt=self._require_text(system_prompt, "system_prompt"),
            temperature=temperature,
            history_limit=history_limit,
            chat_mode=chat_mode,
            top_k=settings.top_k if top_k is None else top_k,
            similarity_threshold=(
                settings.similarity_threshold
                if similarity_threshold is None
                else similarity_threshold
            ),
        )
        self._validate_workspace(workspace)
        self._commit_and_refresh(session, workspace)
        return workspace

    def list_workspaces(self, session: Session) -> list[Workspace]:
        statement = select(Workspace).order_by(Workspace.created_at.desc())
        return list(session.exec(statement).all())

    def get_workspace(self, session: Session, workspace_id: str) -> Workspace:
        workspace = session.get(Workspace, workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"workspace not found: {workspace_id}")
        return workspace

    def update_workspace(
        self,
        session: Session,
        workspace_id: str,
        updates: dict[str, Any],
    ) -> Workspace:
        workspace = self.get_workspace(session, workspace_id)
        if not updates:
            return workspace

        writable_fields = {
            "name",
            "system_prompt",
            "temperature",
            "history_limit",
            "chat_mode",
            "top_k",
            "similarity_threshold",
        }
        for field, value in updates.items():
            if field not in writable_fields:
                continue
            if field in {"name", "system_prompt"}:
                value = self._require_text(value, field)
            setattr(workspace, field, value)

        self._validate_workspace(workspace)
        workspace.updated_at = utc_now()
        self._commit_and_refresh(session, workspace)
        return workspace

    def create_conversation(
        self,
        session: Session,
        workspace_id: str,
    ) -> Conversation:
        self.get_workspace(session, workspace_id)
        conversation = Conversation(workspace_id=workspace_id)
        self._commit_and_refresh(session, conversation)
        return conversation

    def list_conversations(
        self,
        session: Session,
        workspace_id: str,
    ) -> list[Conversation]:
        self.get_workspace(session, workspace_id)
        statement = (
            select(Conversation)
            .where(Conversation.workspace_id == workspace_id)
            .order_by(Conversation.updated_at.desc())
        )
        return list(session.exec(statement).all())

    def list_messages(
        self,
        session: Session,
        workspace_id: str,
        conversation_id: str,
    ) -> list[ConversationMessage]:
        self._get_conversation(session, workspace_id, conversation_id)
        statement = (
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at.asc())
        )
        return list(session.exec(statement).all())

    def list_documents(
        self,
        session: Session,
        workspace_id: str,
    ) -> list[WorkspaceDocument]:
        self.get_workspace(session, workspace_id)
        statement = (
            select(WorkspaceDocument)
            .where(WorkspaceDocument.workspace_id == workspace_id)
            .order_by(WorkspaceDocument.created_at.desc())
        )
        return list(session.exec(statement).all())

    async def delete_document(
        self,
        session: Session,
        workspace_id: str,
        document_id: str,
    ) -> WorkspaceDocumentDeleteResult:
        self.get_workspace(session, workspace_id)
        document = self._get_workspace_document(session, workspace_id, document_id)

        try:
            await self.documents.validate_document_file_paths(
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
            deleted_files = await self.documents.delete_document_files(
                document.id,
                document.upload_path,
                document.parsed_path,
            )
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
        return result

    async def upload_document(
        self,
        session: Session,
        workspace_id: str,
        file: UploadFile,
    ) -> WorkspaceDocument:
        self.get_workspace(session, workspace_id)
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
        return document

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

    async def chat_in_conversation(
        self,
        session: Session,
        workspace_id: str,
        conversation_id: str,
        message: str,
    ) -> WorkspaceChatResult:
        normalized_message = self._require_text(message, "message")
        workspace = self.get_workspace(session, workspace_id)
        conversation = self._get_conversation(
            session,
            workspace_id,
            conversation_id,
        )
        history = self._load_history(
            session,
            conversation_id,
            workspace.history_limit,
        )
        chunks = await self.rag.retrieve(
            normalized_message,
            workspace_id=workspace.id,
            top_k=workspace.top_k,
            similarity_threshold=workspace.similarity_threshold,
        )
        sources = [self.rag.to_source(chunk) for chunk in chunks]

        if not chunks and workspace.chat_mode == "query":
            answer = NO_CONTEXT_ANSWER
            provider = None
            model = None
        else:
            system_prompt = workspace.system_prompt
            if chunks:
                system_prompt = self.rag.build_system_prompt(
                    chunks,
                    base_prompt=workspace.system_prompt,
                )
            chat_result = await self.chat.chat(
                message=normalized_message,
                system_prompt=system_prompt,
                history=history,
                temperature=workspace.temperature,
            )
            answer = chat_result.answer
            provider = chat_result.provider
            model = chat_result.model

        self._save_exchange(
            session,
            conversation,
            normalized_message,
            answer,
            sources,
            provider,
            model,
        )
        return WorkspaceChatResult(
            conversation_id=conversation.id,
            message=normalized_message,
            answer=answer,
            sources=sources,
            provider=provider,
            model=model,
        )

    def _get_conversation(
        self,
        session: Session,
        workspace_id: str,
        conversation_id: str,
    ) -> Conversation:
        statement = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace_id,
        )
        conversation = session.exec(statement).first()
        if conversation is None:
            raise ConversationNotFoundError(
                f"conversation not found in workspace: {conversation_id}"
            )
        return conversation

    def _load_history(
        self,
        session: Session,
        conversation_id: str,
        history_limit: int,
    ) -> list[ChatMessage]:
        if history_limit == 0:
            return []

        statement = (
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(history_limit * 2)
        )
        messages = list(reversed(session.exec(statement).all()))
        return [
            ChatMessage(role=message.role, content=message.content)
            for message in messages
            if message.role in {"user", "assistant"}
        ]

    def _save_exchange(
        self,
        session: Session,
        conversation: Conversation,
        user_content: str,
        assistant_content: str,
        sources: list[RAGSource],
        provider: str | None,
        model: str | None,
    ) -> None:
        source_data = [source.model_dump(mode="json") for source in sources]
        user_message = ConversationMessage(
            conversation_id=conversation.id,
            role="user",
            content=user_content,
        )
        assistant_message = ConversationMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=assistant_content,
            sources=source_data,
            provider=provider,
            model=model,
        )
        now = utc_now()
        conversation.updated_at = now
        if conversation.title == DEFAULT_CONVERSATION_TITLE:
            conversation.title = user_content[:AUTO_TITLE_LENGTH]

        session.add(user_message)
        session.add(assistant_message)
        session.add(conversation)
        try:
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError(
                "failed to save conversation messages"
            ) from exc

    def _commit_and_refresh(self, session: Session, record: Any) -> None:
        session.add(record)
        try:
            session.commit()
            session.refresh(record)
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError("failed to save workspace data") from exc

    def _validate_workspace(self, workspace: Workspace) -> None:
        if not 0 <= workspace.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if workspace.history_limit < 0:
            raise ValueError("history_limit cannot be negative")
        if workspace.chat_mode not in {"chat", "query"}:
            raise ValueError("chat_mode must be chat or query")
        if workspace.top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        if not 0 <= workspace.similarity_threshold <= 1:
            raise ValueError("similarity_threshold must be between 0 and 1")

    def _require_text(self, value: str, field: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field} cannot be empty")
        return normalized


workspace_service = WorkspaceService()
