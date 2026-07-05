import json
from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.core.agent_executor import (
    DEFAULT_AGENT_STEPS,
    MAX_AGENT_STEPS,
    MAX_STEPS_ANSWER,
    AgentRunResult,
    AgentStep,
)
from app.core.agent_modes import AGENT_MODE_REACT_TEXT
from app.core.llm import DEFAULT_SYSTEM_PROMPT, ChatMessage
from app.tools.registry import ToolContext, ToolRegistry

FINAL_ANSWER_PREFIX = "Final Answer:"
ACTION_PREFIX = "Action:"
ACTION_INPUT_PREFIX = "Action Input:"


class AgentChatClient(Protocol):
    async def chat(
        self,
        message: str,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        history: Sequence[ChatMessage] | None = None,
        temperature: float | None = None,
    ) -> Any: ...


class ParsedAgentOutput(BaseModel):
    is_final: bool
    answer: str | None = None
    action: str | None = None
    action_input: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


def parse_agent_output(output: str) -> ParsedAgentOutput:
    has_final_answer = FINAL_ANSWER_PREFIX in output
    has_action = ACTION_PREFIX in output

    if has_final_answer and has_action:
        return _parse_error("mixed_final_answer_and_action")

    if has_final_answer:
        answer = output.split(FINAL_ANSWER_PREFIX, maxsplit=1)[1].strip()
        return ParsedAgentOutput(is_final=True, answer=answer)

    if ACTION_INPUT_PREFIX in output and not has_action:
        return _parse_error("missing_action")

    if not has_action:
        return _parse_error("missing_action")

    action = _extract_action(output)
    if action is None:
        return _parse_error("missing_action")

    if ACTION_INPUT_PREFIX not in output:
        return _parse_error("missing_action_input")

    raw_input = output.split(ACTION_INPUT_PREFIX, maxsplit=1)[1].strip()
    if not raw_input:
        return _parse_error("missing_action_input")

    try:
        action_input = json.loads(raw_input)
    except json.JSONDecodeError:
        return _parse_error("invalid_action_input_json")

    if not isinstance(action_input, dict):
        return _parse_error("invalid_action_input_type")

    return ParsedAgentOutput(
        is_final=False,
        action=action,
        action_input=action_input,
    )


def build_agent_system_prompt(
    base_system_prompt: str,
    tool_registry: ToolRegistry,
) -> str:
    base_prompt = base_system_prompt.strip() or DEFAULT_SYSTEM_PROMPT
    tool_blocks = []
    for tool in tool_registry.list_tools():
        input_schema = json.dumps(
            tool.input_schema,
            ensure_ascii=False,
            sort_keys=True,
        )
        tool_blocks.append(
            f"- name: {tool.name}\n"
            f"  description: {tool.description}\n"
            f"  input_schema: {input_schema}"
        )
    tools_text = "\n".join(tool_blocks) if tool_blocks else "- no tools available"

    return (
        f"{base_prompt}\n\n"
        "You are a minimal tool-using agent.\n"
        "Use only the tools listed below. Do not call tools that are not listed.\n\n"
        f"Available tools:\n{tools_text}\n\n"
        "Output exactly one of these formats:\n\n"
        "Final Answer: <answer to the user>\n\n"
        "Action: <tool name>\n"
        "Action Input: <JSON object>\n\n"
        "Do not output both Final Answer and Action in the same response. "
        "Do not output Thought as part of the protocol."
    )


class ReactTextAgentExecutor:
    agent_mode = AGENT_MODE_REACT_TEXT

    def __init__(
        self,
        llm: AgentChatClient,
        tool_registry: ToolRegistry,
    ):
        self.llm = llm
        self.tool_registry = tool_registry

    async def run(
        self,
        message: str,
        context: ToolContext,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        history: Sequence[ChatMessage] | None = None,
        temperature: float | None = None,
        max_steps: int = DEFAULT_AGENT_STEPS,
    ) -> AgentRunResult:
        normalized_message = message.strip()
        if not normalized_message:
            raise ValueError("message cannot be empty")
        if not 1 <= max_steps <= MAX_AGENT_STEPS:
            raise ValueError(f"max_steps must be between 1 and {MAX_AGENT_STEPS}")

        agent_system_prompt = build_agent_system_prompt(
            system_prompt,
            self.tool_registry,
        )
        steps: list[AgentStep] = []
        provider = None
        model = None

        for step_index in range(1, max_steps + 1):
            llm_result = await self.llm.chat(
                message=self._build_agent_message(normalized_message, steps),
                system_prompt=agent_system_prompt,
                history=history,
                temperature=temperature,
            )
            llm_output = self._extract_answer(llm_result)
            provider = self._extract_optional_text(llm_result, "provider")
            model = self._extract_optional_text(llm_result, "model")

            parsed = parse_agent_output(llm_output)
            if parsed.is_final:
                return AgentRunResult(
                    message=normalized_message,
                    answer=parsed.answer or "",
                    steps=steps,
                    provider=provider,
                    model=model,
                    llm_call_count=step_index,
                    max_steps_reached=False,
                    agent_mode=self.agent_mode,
                )

            if parsed.error is not None:
                steps.append(
                    AgentStep(
                        step_index=step_index,
                        llm_output=llm_output,
                        observation=f"Agent output parse error: {parsed.error}",
                        ok=False,
                        error=parsed.error,
                    )
                )
                continue

            tool_result = await self.tool_registry.run(
                parsed.action or "",
                parsed.action_input,
                context,
            )
            steps.append(
                AgentStep(
                    step_index=step_index,
                    llm_output=llm_output,
                    action=parsed.action,
                    action_input=parsed.action_input,
                    observation=tool_result.content,
                    ok=tool_result.ok,
                    error=tool_result.error,
                    tool_result=tool_result,
                )
            )

        return AgentRunResult(
            message=normalized_message,
            answer=MAX_STEPS_ANSWER,
            steps=steps,
            provider=provider,
            model=model,
            llm_call_count=max_steps,
            max_steps_reached=True,
            agent_mode=self.agent_mode,
        )

    def _build_agent_message(
        self,
        message: str,
        steps: list[AgentStep],
    ) -> str:
        parts = [f"User question:\n{message}"]
        if steps:
            parts.append("Previous observations:")
            for step in steps:
                action = step.action or "parse_error"
                action_input = json.dumps(
                    step.action_input,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                parts.append(
                    f"Step {step.step_index}\n"
                    f"Action: {action}\n"
                    f"Action Input: {action_input}\n"
                    f"Observation: {step.observation}"
                )
        parts.append(
            "Respond with exactly one Final Answer or one Action/Action Input."
        )
        return "\n\n".join(parts)

    def _extract_answer(self, llm_result: Any) -> str:
        if isinstance(llm_result, str):
            return llm_result
        return str(getattr(llm_result, "answer", ""))

    def _extract_optional_text(self, llm_result: Any, field_name: str) -> str | None:
        value = getattr(llm_result, field_name, None)
        if value is None:
            return None
        return str(value)


def _parse_error(error: str) -> ParsedAgentOutput:
    return ParsedAgentOutput(is_final=False, error=error)


def _extract_action(output: str) -> str | None:
    for line in output.splitlines():
        if line.startswith(ACTION_PREFIX):
            action = line.removeprefix(ACTION_PREFIX).strip()
            return action or None
    return None
