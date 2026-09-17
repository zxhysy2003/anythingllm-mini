from fastapi import HTTPException, status

from app.services.exceptions import (
    AgentInvocationConflictError,
    AgentInvocationNotFoundError,
    ChatServiceError,
    ConversationNotFoundError,
    RAGIndexError,
    RAGQueryError,
    WorkspaceDocumentNotFoundError,
    WorkspaceNotFoundError,
    WorkspacePersistenceError,
)


def to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, AgentInvocationConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(
        exc,
        (
            WorkspaceNotFoundError,
            ConversationNotFoundError,
            WorkspaceDocumentNotFoundError,
            AgentInvocationNotFoundError,
        ),
    ):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, ChatServiceError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    if isinstance(exc, (WorkspacePersistenceError, RAGIndexError, RAGQueryError)):
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=str(exc),
    )
