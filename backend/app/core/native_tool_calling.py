import json
from collections.abc import Sequence
from typing import Any, Mapping, Protocol

from app.core.agent_events import (
    AgentEventEmitter,
    create_tool_progress_reporter,
    emit_agent_event,
)
from app.core.agent_executor import (
    DEFAULT_AGENT_STEPS,
    MAX_AGENT_STEPS,
    MAX_STEPS_ANSWER,
    AgentRunResult,
    AgentStep,
    append_partial_document_summary_disclosures,
)
from app.core.agent_modes import AGENT_MODE_NATIVE_TOOL_CALLING
from app.core.llm import (
    DEFAULT_SYSTEM_PROMPT,
    ChatMessage,
    DeepSeekToolCall,
    DeepSeekToolCallResult,
)
from app.tools.clarifying_question import REQUEST_USER_INPUT_TOOL_NAME
from app.tools.registry import ToolContext, ToolRegistry, ToolResult


class NativeToolCallingClient(Protocol):
    async def chat_with_tools(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        temperature: float | None = None,
        tool_choice: str | Mapping[str, Any] = "auto",
    ) -> DeepSeekToolCallResult: ...


class DeepSeekNativeToolCallingExecutor:
    agent_mode = AGENT_MODE_NATIVE_TOOL_CALLING

    def __init__(
        self,
        llm: NativeToolCallingClient,
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
        event_emitter: AgentEventEmitter | None = None,
        initial_steps: Sequence[AgentStep] | None = None,
        initial_llm_call_count: int = 0,
        continuation_observation: str | None = None,
        resume_state: dict[str, Any] | None = None,
    ) -> AgentRunResult:
        normalized_message = message.strip()
        if not normalized_message:
            raise ValueError("message cannot be empty")
        if not 1 <= max_steps <= MAX_AGENT_STEPS:
            raise ValueError(f"max_steps must be between 1 and {MAX_AGENT_STEPS}")
        if not 0 <= initial_llm_call_count < max_steps:
            raise ValueError("initial_llm_call_count must leave one Agent step")

        messages = self._messages_for_run(
            message=normalized_message,
            system_prompt=system_prompt,
            history=history,
            continuation_observation=continuation_observation,
            resume_state=resume_state,
        )
        tools = self.tool_registry.list_openai_tools()
        steps = list(initial_steps or [])
        provider = None
        model = None

        for call_index in range(initial_llm_call_count + 1, max_steps + 1):
            await emit_agent_event(
                event_emitter,
                "llm_started",
                {"call_index": call_index, "agent_mode": self.agent_mode},
            )
            llm_result = await self.llm.chat_with_tools(
                messages=messages,
                tools=tools,
                temperature=temperature,
                tool_choice="auto",
            )
            provider = llm_result.provider
            model = llm_result.model
            await emit_agent_event(
                event_emitter,
                "llm_finished",
                {
                    "call_index": call_index,
                    "agent_mode": self.agent_mode,
                    "provider": provider,
                    "model": model,
                    "tool_call_count": len(llm_result.tool_calls),
                },
            )

            if not llm_result.tool_calls:
                return AgentRunResult(
                    message=normalized_message,
                    answer=append_partial_document_summary_disclosures(
                        llm_result.content,
                        steps,
                    ),
                    steps=steps,
                    provider=provider,
                    model=model,
                    llm_call_count=call_index,
                    max_steps_reached=False,
                    agent_mode=self.agent_mode,
                )

            messages.append(self._assistant_tool_call_message(llm_result))
            if self._has_mixed_clarification_call(llm_result.tool_calls):
                for tool_call in llm_result.tool_calls:
                    step, observation = self._blocked_mixed_clarification_step(
                        step_index=len(steps) + 1,
                        tool_call=tool_call,
                    )
                    steps.append(step)
                    await emit_agent_event(
                        event_emitter,
                        "tool_finished",
                        {"step": step.model_dump(mode="json")},
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": observation,
                        }
                    )
                continue

            for tool_call in llm_result.tool_calls:
                step_index = len(steps) + 1
                await emit_agent_event(
                    event_emitter,
                    "tool_started",
                    {
                        "step_index": step_index,
                        "tool_call_id": tool_call.id,
                        "tool_name": tool_call.name,
                        "arguments": tool_call.arguments,
                    },
                )
                step, observation = await self._run_tool_call(
                    step_index=step_index,
                    tool_call=tool_call,
                    event_emitter=event_emitter,
                    context=context.model_copy(
                        update={
                            "agent_mode": context.agent_mode or self.agent_mode,
                            "clarification_count": sum(
                                1
                                for prior_step in steps
                                if (
                                    prior_step.tool_result is not None
                                    and prior_step.tool_result.interaction is not None
                                    and prior_step.tool_result.interaction.kind
                                    == "clarification"
                                )
                            ),
                            "remaining_llm_calls": max_steps - call_index,
                        }
                    ),
                )
                steps.append(step)
                await emit_agent_event(
                    event_emitter,
                    "tool_finished",
                    {"step": step.model_dump(mode="json")},
                )
                if (
                    step.tool_result is not None
                    and step.tool_result.interaction is not None
                    and step.tool_result.interaction.resolution is None
                ):
                    return AgentRunResult(
                        message=normalized_message,
                        answer=None,
                        steps=steps,
                        provider=provider,
                        model=model,
                        llm_call_count=call_index,
                        max_steps_reached=False,
                        agent_mode=self.agent_mode,
                        pending_interaction=step.tool_result.interaction,
                        resume_state={
                            "messages": messages,
                            "pending_tool_call_id": tool_call.id,
                        },
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": observation,
                    }
                )

        await emit_agent_event(
            event_emitter,
            "max_steps_reached",
            {
                "agent_mode": self.agent_mode,
                "max_steps": max_steps,
                "step_count": len(steps),
            },
        )
        return AgentRunResult(
            message=normalized_message,
            answer=append_partial_document_summary_disclosures(
                MAX_STEPS_ANSWER,
                steps,
            ),
            steps=steps,
            provider=provider,
            model=model,
            llm_call_count=max_steps,
            max_steps_reached=True,
            agent_mode=self.agent_mode,
        )

    def _messages_for_run(
        self,
        *,
        message: str,
        system_prompt: str,
        history: Sequence[ChatMessage] | None,
        continuation_observation: str | None,
        resume_state: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if resume_state is None:
            return self._initial_messages(message, system_prompt, history)

        raw_messages = resume_state.get("messages")
        pending_tool_call_id = resume_state.get("pending_tool_call_id")
        if (
            not isinstance(raw_messages, list)
            or not isinstance(pending_tool_call_id, str)
            or not pending_tool_call_id
            or continuation_observation is None
        ):
            raise ValueError("native continuation state is invalid")
        messages = [dict(item) for item in raw_messages if isinstance(item, dict)]
        if len(messages) != len(raw_messages):
            raise ValueError("native continuation messages are invalid")
        messages.append(
            {
                "role": "tool",
                "tool_call_id": pending_tool_call_id,
                "content": continuation_observation,
            }
        )
        return messages

    def _initial_messages(
        self,
        message: str,
        system_prompt: str,
        history: Sequence[ChatMessage] | None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": system_prompt.strip() or DEFAULT_SYSTEM_PROMPT,
            }
        ]
        if history:
            messages.extend(dict(item) for item in history)
        messages.append({"role": "user", "content": message})
        return messages

    def _assistant_tool_call_message(
        self,
        llm_result: DeepSeekToolCallResult,
    ) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": llm_result.content or None,
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "type": tool_call.type,
                    "function": {
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                    },
                }
                for tool_call in llm_result.tool_calls
            ],
        }

    def _has_mixed_clarification_call(
        self,
        tool_calls: Sequence[DeepSeekToolCall],
    ) -> bool:
        return len(tool_calls) > 1 and any(
            tool_call.name == REQUEST_USER_INPUT_TOOL_NAME for tool_call in tool_calls
        )

    def _blocked_mixed_clarification_step(
        self,
        *,
        step_index: int,
        tool_call: DeepSeekToolCall,
    ) -> tuple[AgentStep, str]:
        error = "clarification_must_be_single_tool_call"
        observation = (
            "Tool call was not executed because request_user_input must be the "
            "only native tool call in its LLM turn."
        )
        action_input = self._parse_action_input_or_empty(tool_call.arguments)
        tool_result = ToolResult(
            ok=False,
            content=observation,
            error=error,
        )
        return (
            AgentStep(
                step_index=step_index,
                llm_output=self._tool_call_output(tool_call),
                action=tool_call.name,
                action_input=action_input,
                observation=observation,
                ok=False,
                error=error,
                tool_result=tool_result,
            ),
            observation,
        )

    async def _run_tool_call(
        self,
        *,
        step_index: int,
        tool_call: DeepSeekToolCall,
        event_emitter: AgentEventEmitter | None,
        context: ToolContext,
    ) -> tuple[AgentStep, str]:
        llm_output = self._tool_call_output(tool_call)
        if tool_call.type != "function":
            error = "unsupported_tool_call_type"
            observation = f"Tool call failed: {error}"
            return (
                AgentStep(
                    step_index=step_index,
                    llm_output=llm_output,
                    action=tool_call.name,
                    observation=observation,
                    ok=False,
                    error=error,
                ),
                observation,
            )

        try:
            action_input = json.loads(tool_call.arguments or "{}")
        except json.JSONDecodeError:
            error = "invalid_tool_arguments_json"
            observation = f"Tool call argument parse error: {error}"
            return (
                AgentStep(
                    step_index=step_index,
                    llm_output=llm_output,
                    action=tool_call.name,
                    observation=observation,
                    ok=False,
                    error=error,
                ),
                observation,
            )

        if not isinstance(action_input, dict):
            error = "invalid_tool_arguments_type"
            observation = f"Tool call argument parse error: {error}"
            return (
                AgentStep(
                    step_index=step_index,
                    llm_output=llm_output,
                    action=tool_call.name,
                    observation=observation,
                    ok=False,
                    error=error,
                ),
                observation,
            )

        tool_result = await self.tool_registry.run(
            tool_call.name,
            action_input,
            context.model_copy(
                update={
                    "progress_reporter": create_tool_progress_reporter(
                        event_emitter,
                        step_index=step_index,
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                    )
                }
            ),
        )
        return (
            AgentStep(
                step_index=step_index,
                llm_output=llm_output,
                action=tool_call.name,
                action_input=action_input,
                observation=tool_result.content,
                ok=tool_result.ok,
                error=tool_result.error,
                tool_result=tool_result,
            ),
            tool_result.content,
        )

    def _parse_action_input_or_empty(self, arguments: str) -> dict[str, Any]:
        try:
            value = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _tool_call_output(self, tool_call: DeepSeekToolCall) -> str:
        return json.dumps(
            {
                "id": tool_call.id,
                "type": tool_call.type,
                "function": {
                    "name": tool_call.name,
                    "arguments": tool_call.arguments,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
