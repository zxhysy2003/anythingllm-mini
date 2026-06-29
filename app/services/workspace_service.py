import logging
from typing import Any

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
    DocumentFileDeletionPlan,
    DocumentService,
    document_service,
)
from app.services.exceptions import (
    ConversationNotFoundError,
    RAGIndexError,
    WorkspaceNotFoundError,
    WorkspacePersistenceError,
)
from app.services.rag_service import (
    NO_CONTEXT_ANSWER,
    RAGService,
    RAGSource,
    rag_service,
)

AUTO_TITLE_LENGTH = 50
logger = logging.getLogger(__name__)


class WorkspaceChatResult(BaseModel):
    conversation_id: str
    message: str
    answer: str
    sources: list[RAGSource]
    provider: str | None
    model: str | None


class WorkspaceDeleteResult(BaseModel):
    id: str
    deleted_documents: int
    deleted_conversations: int
    deleted_messages: int
    deleted_chunks: int
    upload_files_deleted: int
    parsed_files_deleted: int


class WorkspaceService:
    def __init__(
        self,
        rag: RAGService | None = None,
        chat: ChatService | None = None,
        documents: DocumentService | None = None,
    ):
        self.rag = rag or rag_service
        self.chat = chat or chat_service
        self.documents = documents or document_service

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

    async def delete_workspace(
        self,
        session: Session,
        workspace_id: str,
    ) -> WorkspaceDeleteResult:
        workspace = self.get_workspace(session, workspace_id)
        documents = self._load_workspace_documents(session, workspace.id)
        conversations = self._load_workspace_conversations(session, workspace.id)
        messages = self._load_workspace_messages(
            session,
            [conversation.id for conversation in conversations],
        )

        logger.info(
            "workspace.delete.start",
            extra={
                "event": "workspace.delete.start",
                "workspace_id": workspace.id,
                "document_count": len(documents),
                "conversation_count": len(conversations),
                "message_count": len(messages),
            },
        )

        try:
            deletion_plans = await self._build_workspace_deletion_plans(documents)
        except Exception as exc:
            self._log_workspace_delete_failed(workspace.id, "validate_paths")
            raise WorkspacePersistenceError(
                "failed to delete workspace document files"
            ) from exc

        try:
            deleted_chunks = 0
            for document in documents:
                deleted_chunks += await self.rag.delete_document(
                    document.id,
                    workspace_id=workspace.id,
                )
        except RAGIndexError:
            self._log_workspace_delete_failed(workspace.id, "delete_index")
            raise

        try:
            upload_files_deleted = 0
            parsed_files_deleted = 0
            for deletion_plan in deletion_plans:
                deleted_files = await self.documents.delete_document_files(
                    deletion_plan,
                )
                upload_files_deleted += int(deleted_files.upload_file_deleted)
                parsed_files_deleted += int(deleted_files.parsed_file_deleted)
        except Exception as exc:
            self._log_workspace_delete_failed(workspace.id, "delete_files")
            raise WorkspacePersistenceError(
                "failed to delete workspace document files"
            ) from exc

        result = WorkspaceDeleteResult(
            id=workspace.id,
            deleted_documents=len(documents),
            deleted_conversations=len(conversations),
            deleted_messages=len(messages),
            deleted_chunks=deleted_chunks,
            upload_files_deleted=upload_files_deleted,
            parsed_files_deleted=parsed_files_deleted,
        )
        self._delete_workspace_records(
            session,
            workspace,
            documents,
            conversations,
            messages,
        )
        try:
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            self._log_workspace_delete_failed(workspace.id, "delete_database")
            raise WorkspacePersistenceError("failed to delete workspace") from exc

        logger.info(
            "workspace.delete.completed",
            extra={
                "event": "workspace.delete.completed",
                "workspace_id": result.id,
                "deleted_documents": result.deleted_documents,
                "deleted_conversations": result.deleted_conversations,
                "deleted_messages": result.deleted_messages,
                "deleted_chunks": result.deleted_chunks,
            },
        )
        return result

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
        context_prompt = None
        if chunks:
            context_prompt = self.rag.build_context_prompt(
                chunks,
                base_prompt=workspace.system_prompt,
            )
        sources = [] if context_prompt is None else context_prompt.sources
        has_context = context_prompt is not None and bool(context_prompt.chunks)

        if not has_context and workspace.chat_mode == "query":
            answer = NO_CONTEXT_ANSWER
            provider = None
            model = None
        else:
            system_prompt = workspace.system_prompt
            if has_context:
                system_prompt = context_prompt.system_prompt
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
        logger.info(
            "workspace.chat.completed",
            extra={
                "event": "workspace.chat.completed",
                "workspace_id": workspace.id,
                "conversation_id": conversation.id,
                "chat_mode": workspace.chat_mode,
                "has_context": has_context,
                "source_count": len(sources),
            },
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

    def _load_workspace_documents(
        self,
        session: Session,
        workspace_id: str,
    ) -> list[WorkspaceDocument]:
        statement = (
            select(WorkspaceDocument)
            .where(WorkspaceDocument.workspace_id == workspace_id)
            .order_by(WorkspaceDocument.created_at.asc())
        )
        return list(session.exec(statement).all())

    def _load_workspace_conversations(
        self,
        session: Session,
        workspace_id: str,
    ) -> list[Conversation]:
        statement = (
            select(Conversation)
            .where(Conversation.workspace_id == workspace_id)
            .order_by(Conversation.created_at.asc())
        )
        return list(session.exec(statement).all())

    def _load_workspace_messages(
        self,
        session: Session,
        conversation_ids: list[str],
    ) -> list[ConversationMessage]:
        if not conversation_ids:
            return []
        statement = (
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id.in_(conversation_ids))
            .order_by(ConversationMessage.created_at.asc())
        )
        return list(session.exec(statement).all())

    async def _build_workspace_deletion_plans(
        self,
        documents: list[WorkspaceDocument],
    ) -> list[DocumentFileDeletionPlan]:
        deletion_plans = []
        for document in documents:
            deletion_plans.append(
                await self.documents.build_document_file_deletion_plan(
                    document.id,
                    document.upload_path,
                    document.parsed_path,
                )
            )
        return deletion_plans

    def _delete_workspace_records(
        self,
        session: Session,
        workspace: Workspace,
        documents: list[WorkspaceDocument],
        conversations: list[Conversation],
        messages: list[ConversationMessage],
    ) -> None:
        for message in messages:
            session.delete(message)
        for conversation in conversations:
            session.delete(conversation)
        for document in documents:
            session.delete(document)
        session.delete(workspace)

    def _log_workspace_delete_failed(self, workspace_id: str, stage: str) -> None:
        logger.warning(
            "workspace.delete.failed",
            extra={
                "event": "workspace.delete.failed",
                "workspace_id": workspace_id,
                "stage": stage,
            },
        )

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
