import asyncio
from types import SimpleNamespace

import pytest

from app.core.llm import DeepSeekLLM, DeepSeekToolCallResult
from app.services import chat_service as chat_service_module


class FakeLLM:
    model_name = "deepseek-v4-flash"

    async def chat(self, message, system_prompt, history, temperature):
        self.message = message
        self.system_prompt = system_prompt
        self.history = history
        self.temperature = temperature
        return f"echo: {message}"


class FakeToolCallingLLM:
    async def chat_with_tools(self, messages, tools, temperature, tool_choice):
        self.messages = messages
        self.tools = tools
        self.temperature = temperature
        self.tool_choice = tool_choice
        return DeepSeekToolCallResult(
            content="done",
            provider="deepseek",
            model="deepseek-v4-flash",
            finish_reason="stop",
        )


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


def test_tool_calling_chat_service_wraps_native_llm(monkeypatch):
    fake_llm = FakeToolCallingLLM()
    monkeypatch.setattr(chat_service_module, "get_llm", lambda: fake_llm)
    tools = [{"type": "function", "function": {"name": "echo"}}]

    result = asyncio.run(
        chat_service_module.ToolCallingChatService().chat_with_tools(
            messages=[{"role": "user", "content": "hello"}],
            tools=tools,
            temperature=0.2,
            tool_choice="auto",
        )
    )

    assert result.content == "done"
    assert fake_llm.tools == tools
    assert fake_llm.tool_choice == "auto"
    assert fake_llm.temperature == 0.2


def test_deepseek_llm_chat_with_tools_passes_native_parameters():
    llm = DeepSeekLLM.__new__(DeepSeekLLM)
    llm.model_name = "deepseek-v4-flash"
    llm.default_temperature = 0.7
    response = SimpleNamespace(
        model="deepseek-v4-flash",
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            type="function",
                            function=SimpleNamespace(
                                name="calculator",
                                arguments='{"expression": "1 + 1"}',
                            ),
                        )
                    ],
                ),
            )
        ],
    )

    class FakeCompletions:
        async def create(self, **kwargs):
            self.kwargs = kwargs
            return response

    completions = FakeCompletions()
    llm.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    tools = [{"type": "function", "function": {"name": "calculator"}}]

    result = asyncio.run(
        llm.chat_with_tools(
            messages=[{"role": "user", "content": "hello"}],
            tools=tools,
            temperature=0.1,
        )
    )

    assert completions.kwargs["tools"] == tools
    assert completions.kwargs["tool_choice"] == "auto"
    assert completions.kwargs["temperature"] == 0.1
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls[0].id == "call-1"
    assert result.tool_calls[0].name == "calculator"
    assert result.tool_calls[0].arguments == '{"expression": "1 + 1"}'


def test_deepseek_llm_extract_text_ignores_reasoning_content():
    llm = DeepSeekLLM.__new__(DeepSeekLLM)
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='Action: calculator\nAction Input: {"expression": "1 + 1"}',
                    reasoning_content=(
                        'Action: calculator\nAction Input: {"expression": "bad"}'
                    ),
                )
            )
        ]
    )

    assert llm.extract_text(response) == (
        'Action: calculator\nAction Input: {"expression": "1 + 1"}'
    )


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
