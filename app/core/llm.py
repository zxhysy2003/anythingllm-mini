from functools import lru_cache
from typing import Any, Literal, Mapping, Sequence, TypedDict

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.core.config import settings

DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class DeepSeekToolCall(BaseModel):
    id: str
    name: str
    arguments: str
    type: str = "function"


class DeepSeekToolCallResult(BaseModel):
    content: str
    tool_calls: list[DeepSeekToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    provider: str
    model: str


class DeepSeekLLM:
    """Small DeepSeek-only wrapper used by chat and agent flows."""

    def __init__(self, model_name: str | None = None, temperature: float = 0.7):
        self.client = AsyncOpenAI(**settings.deepseek_client_options())
        self.model_name = model_name or settings.model_name
        self.default_temperature = temperature

    def build_messages(
        self,
        message: str,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        history: Sequence[ChatMessage] | None = None,
    ) -> list[ChatMessage]:
        user_message = message.strip()
        if not user_message:
            raise ValueError("message cannot be empty")

        messages: list[ChatMessage] = [
            {
                "role": "system",
                "content": system_prompt.strip() or DEFAULT_SYSTEM_PROMPT,
            }
        ]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        return messages

    async def chat(
        self,
        message: str,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        history: Sequence[ChatMessage] | None = None,
        temperature: float | None = None,
    ) -> str:
        messages = self.build_messages(
            message=message,
            system_prompt=system_prompt,
            history=history,
        )
        response = await self.create_chat_completion(
            messages=messages,
            temperature=temperature,
        )
        return self.extract_text(response)

    async def create_chat_completion(
        self,
        messages: Sequence[Mapping[str, Any]],
        temperature: float | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | Mapping[str, Any] | None = None,
    ) -> Any:
        request: dict[str, Any] = {
            "model": self.model_name,
            "messages": list(messages),
            "temperature": (
                self.default_temperature if temperature is None else temperature
            ),
            "stream": False,
        }
        if tools is not None:
            request["tools"] = list(tools)
        if tool_choice is not None:
            request["tool_choice"] = tool_choice
        return await self.client.chat.completions.create(**request)

    def extract_text(self, response: Any) -> str:
        if not getattr(response, "choices", None):
            raise ValueError("DeepSeek returned no choices.")

        message = response.choices[0].message
        content = message.content or ""
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            return f"<think>{reasoning}</think>{content}"
        return content

    async def chat_with_tools(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        temperature: float | None = None,
        tool_choice: str | Mapping[str, Any] = "auto",
    ) -> DeepSeekToolCallResult:
        response = await self.create_chat_completion(
            messages=messages,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
        )
        return self.extract_tool_call_result(response)

    def extract_tool_call_result(self, response: Any) -> DeepSeekToolCallResult:
        if not getattr(response, "choices", None):
            raise ValueError("DeepSeek returned no choices.")

        choice = response.choices[0]
        message = choice.message
        tool_calls = []
        for tool_call in getattr(message, "tool_calls", None) or []:
            function = getattr(tool_call, "function", None)
            tool_calls.append(
                DeepSeekToolCall(
                    id=str(getattr(tool_call, "id", "")),
                    type=str(getattr(tool_call, "type", "function")),
                    name=str(getattr(function, "name", "")),
                    arguments=str(getattr(function, "arguments", "")),
                )
            )

        return DeepSeekToolCallResult(
            content=message.content or "",
            tool_calls=tool_calls,
            finish_reason=getattr(choice, "finish_reason", None),
            provider=settings.llm_provider,
            model=str(getattr(response, "model", self.model_name)),
        )


@lru_cache
def get_llm() -> DeepSeekLLM:
    return DeepSeekLLM()
