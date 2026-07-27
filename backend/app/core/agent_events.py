from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.core.tool_progress import ToolProgress, ToolProgressReporter

AgentEventType = Literal[
    "agent_started",
    "llm_started",
    "llm_finished",
    "tool_started",
    "tool_progress",
    "tool_finished",
    "parse_error",
    "agent_needs_input",
    "agent_finished",
    "agent_failed",
    "max_steps_reached",
]


class AgentEvent(BaseModel):
    sequence: int
    type: AgentEventType
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentEventEmitter(Protocol):
    async def emit(
        self,
        event_type: AgentEventType,
        payload: dict[str, Any] | None = None,
    ) -> None: ...


class AgentToolProgressReporter:
    def __init__(
        self,
        event_emitter: AgentEventEmitter,
        *,
        step_index: int,
        tool_name: str,
        tool_call_id: str | None = None,
    ) -> None:
        self.event_emitter = event_emitter
        self.step_index = step_index
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id

    async def report(self, progress: ToolProgress) -> None:
        payload = progress.model_dump(mode="json")
        payload.update(
            {
                "step_index": self.step_index,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
            }
        )
        await self.event_emitter.emit("tool_progress", payload)


def create_tool_progress_reporter(
    event_emitter: AgentEventEmitter | None,
    *,
    step_index: int,
    tool_name: str,
    tool_call_id: str | None = None,
) -> ToolProgressReporter | None:
    if event_emitter is None:
        return None
    return AgentToolProgressReporter(
        event_emitter,
        step_index=step_index,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
    )


async def emit_agent_event(
    event_emitter: AgentEventEmitter | None,
    event_type: AgentEventType,
    payload: dict[str, Any] | None = None,
) -> None:
    if event_emitter is None:
        return
    await event_emitter.emit(event_type, payload or {})
