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


def append_partial_document_summary_disclosures(
    answer: str | None,
    steps: Sequence[AgentStep],
) -> str | None:
    notices = []
    for step in steps:
        if step.action != "workspace_document_summary" or step.tool_result is None:
            continue
        outputs = step.tool_result.artifacts.outputs
        if (
            outputs.get("action") != "summarize"
            or outputs.get("completion_status") != "partial"
        ):
            continue
        processed_chunks = outputs.get("processed_chunks")
        total_chunks = outputs.get("total_chunks")
        if (
            type(processed_chunks) is not int
            or type(total_chunks) is not int
            or processed_chunks < 1
            or total_chunks < processed_chunks
        ):
            continue
        raw_reasons = outputs.get("stop_reasons")
        reasons = (
            [reason for reason in raw_reasons if isinstance(reason, str) and reason]
            if isinstance(raw_reasons, list)
            else []
        )
        reason_text = f"; reasons: {', '.join(reasons)}" if reasons else ""
        notice = (
            "Coverage notice: this document summary is partial and covers only "
            f"the first {processed_chunks} of {total_chunks} sections"
            f"{reason_text}."
        )
        if notice not in notices:
            notices.append(notice)

    if not notices:
        return answer
    normalized_answer = (answer or "").strip()
    disclosure = "\n".join(notices)
    if not normalized_answer:
        return disclosure
    missing_notices = [notice for notice in notices if notice not in normalized_answer]
    if not missing_notices:
        return normalized_answer
    missing_disclosure = "\n".join(missing_notices)
    return f"{normalized_answer}\n\n{missing_disclosure}"


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
