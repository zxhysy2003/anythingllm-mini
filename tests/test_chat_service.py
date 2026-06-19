import asyncio

import pytest

from app.services import chat_service as chat_service_module


class FakeLLM:
    model_name = "deepseek-v4-flash"

    async def chat(self, message, system_prompt, history, temperature):
        self.message = message
        self.system_prompt = system_prompt
        self.history = history
        self.temperature = temperature
        return f"echo: {message}"


def test_chat_service_returns_chat_result(monkeypatch):
    fake_llm = FakeLLM()
    monkeypatch.setattr(chat_service_module, "get_llm", lambda: fake_llm)

    result = asyncio.run(
        chat_service_module.ChatService().chat(
            message="  hello  ",
            system_prompt="You are concise.",
            temperature=0.3,
        )
    )

    assert result.message == "hello"
    assert result.answer == "echo: hello"
    assert result.provider == "deepseek"
    assert result.model == "deepseek-v4-flash"
    assert fake_llm.system_prompt == "You are concise."
    assert fake_llm.temperature == 0.3


def test_chat_service_rejects_empty_message():
    with pytest.raises(ValueError, match="message cannot be empty"):
        asyncio.run(chat_service_module.ChatService().chat(message="   "))


def test_chat_service_rejects_invalid_temperature():
    with pytest.raises(ValueError, match="temperature must be between 0 and 2"):
        asyncio.run(
            chat_service_module.ChatService().chat(
                message="hello",
                temperature=3,
            )
        )
