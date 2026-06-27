from typing import Sequence

from pydantic import BaseModel

from app.core.config import settings
from app.core.llm import DEFAULT_SYSTEM_PROMPT, ChatMessage, get_llm
from app.services.exceptions import ChatServiceError


class ChatResult(BaseModel):
    message: str
    answer: str
    provider: str
    model: str


class ChatService:
    async def chat(
        self,
        message: str,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        history: Sequence[ChatMessage] | None = None,
        temperature: float | None = None,
    ) -> ChatResult:
        user_message = message.strip()
        if not user_message:
            raise ValueError("message cannot be empty")

        if temperature is not None and not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")

        llm = get_llm()
        try:
            answer = await llm.chat(
                message=user_message,
                system_prompt=system_prompt,
                history=history,
                temperature=temperature,
            )
        except Exception as exc:
            raise ChatServiceError(f"DeepSeek chat failed: {exc}") from exc

        return ChatResult(
            message=user_message,
            answer=answer,
            provider=settings.llm_provider,
            model=llm.model_name,
        )


chat_service = ChatService()
