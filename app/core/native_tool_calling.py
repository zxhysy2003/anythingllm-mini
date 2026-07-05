import json
from collections.abc import Sequence
from typing import Any, Mapping, Protocol

from app.core.agent_executor import (
    DEFAULT_AGENT_STEPS,
    MAX_AGENT_STEPS,
    MAX_STEPS_ANSWER,
    AgentRunResult,
    AgentStep,
)
from app.core.agent_modes import AGENT_MODE_NATIVE_TOOL_CALLING
from app.core.llm import (
    DEFAULT_SYSTEM_PROMPT,
    ChatMessage,
    DeepSeekToolCall,
    DeepSeekToolCallResult,
)
from app.tools.registry import ToolContext, ToolRegistry


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
    ) -> AgentRunResult:
        normalized_message = message.strip()
        if not normalized_message:
            raise ValueError("message cannot be empty")
        if not 1 <= max_steps <= MAX_AGENT_STEPS:
            raise ValueError(f"max_steps must be between 1 and {MAX_AGENT_STEPS}")

        messages = self._initial_messages(
            normalized_message,
            system_prompt,
            history,
        )
        tools = self.tool_registry.list_openai_tools()
        steps: list[AgentStep] = []
        provider = None
        model = None

        for call_index in range(1, max_steps + 1):
            llm_result = await self.llm.chat_with_tools(
                messages=messages,
                tools=tools,
                temperature=temperature,
                tool_choice="auto",
            )
            provider = llm_result.provider
            model = llm_result.model

            if not llm_result.tool_calls:
                return AgentRunResult(
                    message=normalized_message,
                    answer=llm_result.content,
                    steps=steps,
                    provider=provider,
                    model=model,
                    llm_call_count=call_index,
                    max_steps_reached=False,
                    agent_mode=self.agent_mode,
                )

            messages.append(self._assistant_tool_call_message(llm_result))
            for tool_call in llm_result.tool_calls:
                step, observation = await self._run_tool_call(
                    step_index=len(steps) + 1,
                    tool_call=tool_call,
                    context=context,
                )
                steps.append(step)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": observation,
                    }
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

    async def _run_tool_call(
        self,
        *,
        step_index: int,
        tool_call: DeepSeekToolCall,
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
            context,
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
