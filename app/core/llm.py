from functools import lru_cache
from typing import Any, Literal, Sequence, TypedDict

from openai import AsyncOpenAI

from app.core.config import settings


DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class DeepSeekLLM:
    """Small DeepSeek-only wrapper for the V0 chat flow."""

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
        messages: Sequence[ChatMessage],
        temperature: float | None = None,
    ) -> Any:
        return await self.client.chat.completions.create(
            model=self.model_name,
            messages=list(messages),
            temperature=(
                self.default_temperature if temperature is None else temperature
            ),
            stream=False,
        )

    def extract_text(self, response: Any) -> str:
        if not getattr(response, "choices", None):
            raise ValueError("DeepSeek returned no choices.")

        message = response.choices[0].message
        content = message.content or ""
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            return f"<think>{reasoning}</think>{content}"
        return content


@lru_cache
def get_llm() -> DeepSeekLLM:
    return DeepSeekLLM()
