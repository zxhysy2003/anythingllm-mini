from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import Session

from app.core.config import settings
from app.core.llm import DEFAULT_SYSTEM_PROMPT
from app.db.session import get_session
from app.services.chat_service import ChatServiceError
from app.services.rag_service import RAGIndexError, RAGQueryError, RAGSource
from app.services.workspace_service import (
    ConversationNotFoundError,
    WorkspaceDocumentDeleteResult,
    WorkspaceDocumentNotFoundError,
    WorkspaceChatResult,
    WorkspaceNotFoundError,
    WorkspacePersistenceError,
    workspace_service,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
SessionDependency = Annotated[Session, Depends(get_session)]


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    system_prompt: str = Field(default=DEFAULT_SYSTEM_PROMPT, min_length=1)
    temperature: float = Field(default=0.7, ge=0, le=2)
    history_limit: int = Field(default=20, ge=0)
    chat_mode: Literal["chat", "query"] = "chat"
    top_k: int = Field(default=settings.top_k, gt=0)
    similarity_threshold: float = Field(
        default=settings.similarity_threshold,
        ge=0,
        le=1,
    )

    @field_validator("name", "system_prompt")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be empty")
        return normalized


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    system_prompt: str | None = Field(default=None, min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    history_limit: int | None = Field(default=None, ge=0)
    chat_mode: Literal["chat", "query"] | None = None
    top_k: int | None = Field(default=None, gt=0)
    similarity_threshold: float | None = Field(default=None, ge=0, le=1)

    @field_validator("name", "system_prompt")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be empty")
        return normalized


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    system_prompt: str
    temperature: float
    history_limit: int
    chat_mode: str
    top_k: int
    similarity_threshold: float
    created_at: datetime
    updated_at: datetime


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    role: str
    content: str
    sources: list[RAGSource]
    provider: str | None
    model: str | None
    created_at: datetime


class WorkspaceDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    original_filename: str
    stored_filename: str
    content_type: str | None
    extension: str
    size_bytes: int
    character_count: int
    upload_path: str
    parsed_path: str
    chunk_count: int
    created_at: datetime


class WorkspaceDocumentDeleteResponse(BaseModel):
    id: str
    workspace_id: str
    original_filename: str
    deleted_chunks: int
    upload_path: str
    parsed_path: str
    upload_file_deleted: bool
    parsed_file_deleted: bool


class WorkspaceChatRequest(BaseModel):
    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("message cannot be empty")
        return message


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
def create_workspace(
    request: WorkspaceCreate,
    session: SessionDependency,
) -> WorkspaceRead:
    try:
        return workspace_service.create_workspace(
            session,
            **request.model_dump(),
        )
    except (ValueError, WorkspacePersistenceError) as exc:
        raise _workspace_http_error(exc) from exc


@router.get("", response_model=list[WorkspaceRead])
def list_workspaces(session: SessionDependency) -> list[WorkspaceRead]:
    return workspace_service.list_workspaces(session)


@router.get("/{workspace_id}", response_model=WorkspaceRead)
def get_workspace(workspace_id: str, session: SessionDependency) -> WorkspaceRead:
    try:
        return workspace_service.get_workspace(session, workspace_id)
    except WorkspaceNotFoundError as exc:
        raise _workspace_http_error(exc) from exc


@router.patch("/{workspace_id}", response_model=WorkspaceRead)
def update_workspace(
    workspace_id: str,
    request: WorkspaceUpdate,
    session: SessionDependency,
) -> WorkspaceRead:
    try:
        return workspace_service.update_workspace(
            session,
            workspace_id,
            request.model_dump(exclude_unset=True, exclude_none=True),
        )
    except (ValueError, WorkspaceNotFoundError, WorkspacePersistenceError) as exc:
        raise _workspace_http_error(exc) from exc


@router.post(
    "/{workspace_id}/documents/upload",
    response_model=WorkspaceDocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_workspace_document(
    workspace_id: str,
    file: Annotated[
        UploadFile,
        File(description="TXT, PDF, or DOCX document to upload and index."),
    ],
    session: SessionDependency,
) -> WorkspaceDocumentRead:
    try:
        return await workspace_service.upload_document(session, workspace_id, file)
    except (
        ValueError,
        WorkspaceNotFoundError,
        WorkspacePersistenceError,
        RAGIndexError,
    ) as exc:
        raise _workspace_http_error(exc) from exc


@router.get(
    "/{workspace_id}/documents",
    response_model=list[WorkspaceDocumentRead],
)
def list_workspace_documents(
    workspace_id: str,
    session: SessionDependency,
) -> list[WorkspaceDocumentRead]:
    try:
        return workspace_service.list_documents(session, workspace_id)
    except WorkspaceNotFoundError as exc:
        raise _workspace_http_error(exc) from exc


@router.delete(
    "/{workspace_id}/documents/{document_id}",
    response_model=WorkspaceDocumentDeleteResponse,
)
async def delete_workspace_document(
    workspace_id: str,
    document_id: str,
    session: SessionDependency,
) -> WorkspaceDocumentDeleteResult:
    try:
        return await workspace_service.delete_document(
            session,
            workspace_id,
            document_id,
        )
    except (
        WorkspaceNotFoundError,
        WorkspaceDocumentNotFoundError,
        WorkspacePersistenceError,
        RAGIndexError,
    ) as exc:
        raise _workspace_http_error(exc) from exc


@router.post(
    "/{workspace_id}/conversations",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    workspace_id: str,
    session: SessionDependency,
) -> ConversationRead:
    try:
        return workspace_service.create_conversation(session, workspace_id)
    except (WorkspaceNotFoundError, WorkspacePersistenceError) as exc:
        raise _workspace_http_error(exc) from exc


@router.get(
    "/{workspace_id}/conversations",
    response_model=list[ConversationRead],
)
def list_conversations(
    workspace_id: str,
    session: SessionDependency,
) -> list[ConversationRead]:
    try:
        return workspace_service.list_conversations(session, workspace_id)
    except WorkspaceNotFoundError as exc:
        raise _workspace_http_error(exc) from exc


@router.get(
    "/{workspace_id}/conversations/{conversation_id}/messages",
    response_model=list[ConversationMessageRead],
)
def list_conversation_messages(
    workspace_id: str,
    conversation_id: str,
    session: SessionDependency,
) -> list[ConversationMessageRead]:
    try:
        return workspace_service.list_messages(
            session,
            workspace_id,
            conversation_id,
        )
    except (WorkspaceNotFoundError, ConversationNotFoundError) as exc:
        raise _workspace_http_error(exc) from exc


@router.post(
    "/{workspace_id}/conversations/{conversation_id}/chat",
    response_model=WorkspaceChatResult,
)
async def chat_in_conversation(
    workspace_id: str,
    conversation_id: str,
    request: WorkspaceChatRequest,
    session: SessionDependency,
) -> WorkspaceChatResult:
    try:
        return await workspace_service.chat_in_conversation(
            session,
            workspace_id,
            conversation_id,
            request.message,
        )
    except (
        ValueError,
        WorkspaceNotFoundError,
        ConversationNotFoundError,
        WorkspacePersistenceError,
        ChatServiceError,
        RAGQueryError,
    ) as exc:
        raise _workspace_http_error(exc) from exc


def _workspace_http_error(exc: Exception) -> HTTPException:
    if isinstance(
        exc,
        (
            WorkspaceNotFoundError,
            ConversationNotFoundError,
            WorkspaceDocumentNotFoundError,
        ),
    ):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ChatServiceError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    if isinstance(
        exc,
        (WorkspacePersistenceError, RAGIndexError, RAGQueryError),
    ):
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
