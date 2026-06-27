from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlmodel import Session

from app.db.session import get_session
from app.api.errors import to_http_exception
from app.api.schemas.workspaces import (
    ConversationMessageRead,
    ConversationRead,
    WorkspaceChatRequest,
    WorkspaceCreate,
    WorkspaceDocumentDeleteResponse,
    WorkspaceDocumentRead,
    WorkspaceRead,
    WorkspaceUpdate,
)
from app.services.exceptions import (
    ChatServiceError,
    ConversationNotFoundError,
    RAGIndexError,
    RAGQueryError,
    WorkspaceDocumentNotFoundError,
    WorkspaceNotFoundError,
    WorkspacePersistenceError,
)
from app.services.workspace_document_service import (
    WorkspaceDocumentDeleteResult,
    workspace_document_service,
)
from app.services.workspace_service import (
    WorkspaceChatResult,
    workspace_service,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
SessionDependency = Annotated[Session, Depends(get_session)]


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
        raise to_http_exception(exc) from exc


@router.get("", response_model=list[WorkspaceRead])
def list_workspaces(session: SessionDependency) -> list[WorkspaceRead]:
    return workspace_service.list_workspaces(session)


@router.get("/{workspace_id}", response_model=WorkspaceRead)
def get_workspace(workspace_id: str, session: SessionDependency) -> WorkspaceRead:
    try:
        return workspace_service.get_workspace(session, workspace_id)
    except WorkspaceNotFoundError as exc:
        raise to_http_exception(exc) from exc


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
        raise to_http_exception(exc) from exc


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
        return await workspace_document_service.upload_document(
            session,
            workspace_id,
            file,
        )
    except (
        ValueError,
        WorkspaceNotFoundError,
        WorkspacePersistenceError,
        RAGIndexError,
    ) as exc:
        raise to_http_exception(exc) from exc


@router.get(
    "/{workspace_id}/documents",
    response_model=list[WorkspaceDocumentRead],
)
def list_workspace_documents(
    workspace_id: str,
    session: SessionDependency,
) -> list[WorkspaceDocumentRead]:
    try:
        return workspace_document_service.list_documents(session, workspace_id)
    except WorkspaceNotFoundError as exc:
        raise to_http_exception(exc) from exc


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
        return await workspace_document_service.delete_document(
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
        raise to_http_exception(exc) from exc


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
        raise to_http_exception(exc) from exc


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
        raise to_http_exception(exc) from exc


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
        raise to_http_exception(exc) from exc


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
        raise to_http_exception(exc) from exc
