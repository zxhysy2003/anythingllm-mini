import asyncio
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.api.errors import to_http_exception
from app.api.schemas.agents import (
    AgentInvocationRead,
    WorkspaceAgentRequest,
    WorkspaceAgentResponse,
)
from app.core.agent_events import AgentEvent, AgentEventType
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


class QueueAgentEventEmitter:
    def __init__(self, queue: asyncio.Queue[AgentEvent | None]) -> None:
        self.queue = queue
        self.sequence = 0
        self.has_failed = False

    async def emit(
        self,
        event_type: AgentEventType,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.sequence += 1
        if event_type == "agent_failed":
            self.has_failed = True
        await self.queue.put(
            AgentEvent(
                sequence=self.sequence,
                type=event_type,
                payload=payload or {},
            )
        )


@router.post(
    "/{workspace_id}/conversations/{conversation_id}/agent",
    response_model=WorkspaceAgentResponse,
    summary="Workspace agent loop",
    description=(
        "Run a minimal workspace agent executor inside one conversation. "
        "The default mode is ReAct text, and requests may opt into DeepSeek "
        "native tool calling. The agent can call registered tools such as "
        "calculator and workspace_document_search, saves the final "
        "user/assistant messages, and stores intermediate agent steps on a "
        "separate agent invocation record."
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
            agent_mode=request.agent_mode,
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


@router.post(
    "/{workspace_id}/conversations/{conversation_id}/agent/stream",
    response_class=StreamingResponse,
    summary="Stream workspace agent events",
    description=(
        "Run the workspace agent and stream SSE-format execution events. "
        "This streams agent/tool status events, not token-by-token answer text. "
        "The final agent_finished event contains the same completed run payload "
        "as the non-streaming agent endpoint."
    ),
)
async def stream_agent_in_conversation(
    workspace_id: str,
    conversation_id: str,
    request: WorkspaceAgentRequest,
    session: SessionDependency,
) -> StreamingResponse:
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
    emitter = QueueAgentEventEmitter(queue)

    async def produce_events() -> None:
        try:
            await agent_service.run_in_conversation(
                session,
                workspace_id,
                conversation_id,
                request.message,
                max_steps=request.max_steps,
                agent_mode=request.agent_mode,
                event_emitter=emitter,
            )
        except Exception as exc:
            if not emitter.has_failed:
                await emitter.emit(
                    "agent_failed",
                    {
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
        finally:
            await queue.put(None)

    async def event_stream():
        producer = asyncio.create_task(produce_events())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                data = json.dumps(
                    event.model_dump(mode="json"),
                    ensure_ascii=False,
                )
                yield f"event: {event.type}\ndata: {data}\n\n"
        finally:
            await producer

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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
