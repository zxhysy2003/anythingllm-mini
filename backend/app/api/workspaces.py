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
    WorkspaceDeleteResponse,
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
    WorkspaceDeleteResult,
    workspace_service,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post(
    "",
    response_model=WorkspaceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create workspace",
    description=(
        "Create the workspace boundary for scoped documents, conversation "
        "history, chat settings, and agent runs."
    ),
)
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


@router.get(
    "",
    response_model=list[WorkspaceRead],
    summary="List workspaces",
    description=(
        "List available workspace records. Each workspace owns its documents, "
        "conversations, chat settings, and agent context."
    ),
)
def list_workspaces(session: SessionDependency) -> list[WorkspaceRead]:
    return workspace_service.list_workspaces(session)


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceRead,
    summary="Get workspace",
    description=(
        "Read one workspace by id, including prompt, temperature, history, "
        "chat mode, and retrieval settings."
    ),
)
def get_workspace(workspace_id: str, session: SessionDependency) -> WorkspaceRead:
    try:
        return workspace_service.get_workspace(session, workspace_id)
    except WorkspaceNotFoundError as exc:
        raise to_http_exception(exc) from exc


@router.patch(
    "/{workspace_id}",
    response_model=WorkspaceRead,
    summary="Update workspace settings",
    description=(
        "Update workspace chat and retrieval settings such as system prompt, "
        "temperature, history limit, chat mode, top_k, and similarity threshold."
    ),
)
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


@router.delete(
    "/{workspace_id}",
    response_model=WorkspaceDeleteResponse,
    summary="Delete workspace",
    description=(
        "Hard-delete a workspace and its scoped documents, conversations, "
        "messages, files, and vector chunks. It returns deletion counts instead "
        "of local file paths."
    ),
)
async def delete_workspace(
    workspace_id: str,
    session: SessionDependency,
) -> WorkspaceDeleteResult:
    try:
        return await workspace_service.delete_workspace(session, workspace_id)
    except (
        WorkspaceNotFoundError,
        WorkspacePersistenceError,
        RAGIndexError,
    ) as exc:
        raise to_http_exception(exc) from exc


@router.post(
    "/{workspace_id}/documents/upload",
    response_model=WorkspaceDocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload workspace document",
    description=(
        "Upload, parse, chunk, embed, and index a TXT, PDF, or DOCX file into "
        "the current workspace scope."
    ),
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
    summary="List workspace documents",
    description=(
        "List documents registered in one workspace. Responses expose document "
        "metadata but never expose local storage paths."
    ),
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
    summary="Delete workspace document",
    description=(
        "Remove one document from the current workspace, including its vector "
        "chunks and local upload/parsed files. The document must belong to the "
        "workspace path scope."
    ),
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
    summary="Create workspace conversation",
    description=(
        "Create an empty conversation inside a workspace. Workspace chat and "
        "agent calls both run inside this conversation boundary."
    ),
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
    summary="List workspace conversations",
    description=(
        "List conversations for one workspace, ordered by latest activity so "
        "clients can show recent threads first."
    ),
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
    summary="List conversation messages",
    description=(
        "Read saved user and assistant messages for one workspace conversation. "
        "Agent assistant message metrics contain an agent_invocation_id for "
        "loading separate persisted agent steps."
    ),
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
    summary="Workspace conversation chat",
    description=(
        "Load conversation history, retrieve workspace-scoped document context "
        "when available, apply chat/query mode, call the LLM when appropriate, "
        "and save the final user and assistant messages."
    ),
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
