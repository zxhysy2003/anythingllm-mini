from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.errors import to_http_exception
from app.api.schemas.agents import (
    AgentInvocationRead,
    WorkspaceAgentRequest,
    WorkspaceAgentResponse,
)
from app.db.session import get_session
from app.services.agent_service import agent_service
from app.services.exceptions import (
    AgentInvocationNotFoundError,
    ChatServiceError,
    ConversationNotFoundError,
    RAGQueryError,
    WorkspaceNotFoundError,
    WorkspacePersistenceError,
)

router = APIRouter(prefix="/workspaces", tags=["agents"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post(
    "/{workspace_id}/conversations/{conversation_id}/agent",
    response_model=WorkspaceAgentResponse,
    summary="Workspace agent loop",
    description=(
        "Run the minimal ReAct text agent loop inside one workspace "
        "conversation. The agent can call registered tools such as calculator "
        "and workspace_document_search, saves the final user/assistant "
        "messages, and stores intermediate agent steps on a separate agent "
        "invocation record."
    ),
)
async def run_agent_in_conversation(
    workspace_id: str,
    conversation_id: str,
    request: WorkspaceAgentRequest,
    session: SessionDependency,
) -> WorkspaceAgentResponse:
    try:
        result = await agent_service.run_in_conversation(
            session,
            workspace_id,
            conversation_id,
            request.message,
            max_steps=request.max_steps,
        )
        return WorkspaceAgentResponse.model_validate(result)
    except (
        ValueError,
        WorkspaceNotFoundError,
        ConversationNotFoundError,
        ChatServiceError,
        RAGQueryError,
        WorkspacePersistenceError,
    ) as exc:
        raise to_http_exception(exc) from exc


@router.get(
    "/{workspace_id}/conversations/{conversation_id}/agent-invocations/{invocation_id}",
    response_model=AgentInvocationRead,
    summary="Read workspace agent invocation",
    description=(
        "Read one persisted agent invocation and its ordered intermediate "
        "steps. Assistant message metrics only link to this record through "
        "agent_invocation_id."
    ),
)
def get_agent_invocation(
    workspace_id: str,
    conversation_id: str,
    invocation_id: str,
    session: SessionDependency,
) -> AgentInvocationRead:
    try:
        result = agent_service.get_invocation(
            session,
            workspace_id,
            conversation_id,
            invocation_id,
        )
        return AgentInvocationRead.model_validate(result)
    except (
        WorkspaceNotFoundError,
        AgentInvocationNotFoundError,
    ) as exc:
        raise to_http_exception(exc) from exc
