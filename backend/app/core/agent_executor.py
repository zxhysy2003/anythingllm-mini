from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.core.agent_events import AgentEventEmitter
from app.core.llm import DEFAULT_SYSTEM_PROMPT, ChatMessage
from app.tools.interactions import ToolInteraction
from app.tools.registry import ToolContext, ToolResult

MAX_AGENT_STEPS = 10
DEFAULT_AGENT_STEPS = 5
MAX_STEPS_ANSWER = "Agent stopped after reaching the maximum number of steps."


class AgentStep(BaseModel):
    step_index: int
    llm_output: str
    action: str | None = None
    action_input: dict[str, Any] = Field(default_factory=dict)
    observation: str | None = None
    ok: bool
    error: str | None = None
    tool_result: ToolResult | None = None


class AgentRunResult(BaseModel):
    message: str
    answer: str | None
    steps: list[AgentStep]
    agent_mode: str
    provider: str | None = None
    model: str | None = None
    llm_call_count: int
    max_steps_reached: bool
    pending_interaction: ToolInteraction | None = None
    resume_state: dict[str, Any] | None = None


class AgentExecutor(Protocol):
    agent_mode: str

    async def run(
        self,
        message: str,
        context: ToolContext,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        history: Sequence[ChatMessage] | None = None,
        temperature: float | None = None,
        max_steps: int = DEFAULT_AGENT_STEPS,
        event_emitter: AgentEventEmitter | None = None,
        initial_steps: Sequence[AgentStep] | None = None,
        initial_llm_call_count: int = 0,
        continuation_observation: str | None = None,
        resume_state: dict[str, Any] | None = None,
    ) -> AgentRunResult: ...
