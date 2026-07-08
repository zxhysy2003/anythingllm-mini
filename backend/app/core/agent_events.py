from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

AgentEventType = Literal[
    "agent_started",
    "llm_started",
    "llm_finished",
    "tool_started",
    "tool_finished",
    "parse_error",
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


async def emit_agent_event(
    event_emitter: AgentEventEmitter | None,
    event_type: AgentEventType,
    payload: dict[str, Any] | None = None,
) -> None:
    if event_emitter is None:
        return
    await event_emitter.emit(event_type, payload or {})
