import asyncio

import pytest
from pydantic import BaseModel, Field

from app.core.agent_executor import MAX_STEPS_ANSWER
from app.core.agent_modes import AGENT_MODE_REACT_TEXT
from app.core.agent_loop import (
    ReactTextAgentExecutor,
    build_agent_system_prompt,
    parse_agent_output,
)
from app.tools.calculator import CalculatorTool
from app.tools.registry import ToolContext, ToolRegistry, ToolResult


class FakeLLMResult:
    def __init__(
        self,
        answer: str,
        provider: str = "fake",
        model: str = "fake-agent-model",
    ):
        self.answer = answer
        self.provider = provider
        self.model = model


class FakeLLM:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = []

    async def chat(self, message, system_prompt, history, temperature):
        self.calls.append(
            {
                "message": message,
                "system_prompt": system_prompt,
                "history": history,
                "temperature": temperature,
            }
        )
        response_index = len(self.calls) - 1
        return FakeLLMResult(self.responses[response_index])


class CaptureInput(BaseModel):
    value: str = Field(min_length=1)


class CaptureContextTool:
    name = "capture_context"
    description = "Capture the tool context for agent loop tests."
    input_model = CaptureInput

    def __init__(self):
        self.seen_workspace_id = None
        self.seen_conversation_id = None

    async def run(self, input_data, context):
        self.seen_workspace_id = context.workspace_id
        self.seen_conversation_id = context.conversation_id
        return ToolResult(ok=True, content=f"captured {input_data.value}")


def make_registry(*tools):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def run_agent(
    responses,
    registry,
    *,
    context=None,
    max_steps=5,
):
    executor = ReactTextAgentExecutor(
        llm=FakeLLM(responses),
        tool_registry=registry,
    )
    return asyncio.run(
        executor.run(
            "What should I do?",
            context or ToolContext(workspace_id="workspace-1"),
            system_prompt="You are a workspace assistant.",
            history=[{"role": "user", "content": "previous question"}],
            temperature=0,
            max_steps=max_steps,
        )
    )


def test_parser_parses_final_answer():
    parsed = parse_agent_output("Final Answer: 结果是 3")

    assert parsed.is_final is True
    assert parsed.answer == "结果是 3"
    assert parsed.error is None


def test_parser_parses_action_with_json_object_input():
    parsed = parse_agent_output(
        'Action: calculator\nAction Input: {"expression": "1 + 2"}'
    )

    assert parsed.is_final is False
    assert parsed.action == "calculator"
    assert parsed.action_input == {"expression": "1 + 2"}
    assert parsed.error is None


@pytest.mark.parametrize(
    ("output", "error"),
    [
        ("Action: calculator", "missing_action_input"),
        ("Action: calculator\nAction Input: {bad json}", "invalid_action_input_json"),
        ("Action: calculator\nAction Input: [1, 2]", "invalid_action_input_type"),
        (
            'Final Answer: done\nAction: calculator\nAction Input: {"x": 1}',
            "mixed_final_answer_and_action",
        ),
    ],
)
def test_parser_rejects_invalid_outputs(output, error):
    parsed = parse_agent_output(output)

    assert parsed.is_final is False
    assert parsed.error == error


def test_agent_returns_final_answer_without_tool_call():
    registry = make_registry(CalculatorTool())
    result = run_agent(["Final Answer: no tool needed"], registry)

    assert result.answer == "no tool needed"
    assert result.steps == []
    assert result.llm_call_count == 1
    assert result.max_steps_reached is False
    assert result.provider == "fake"
    assert result.model == "fake-agent-model"
    assert result.agent_mode == AGENT_MODE_REACT_TEXT


def test_agent_calls_calculator_then_returns_final_answer():
    registry = make_registry(CalculatorTool())
    executor = ReactTextAgentExecutor(
        llm=FakeLLM(
            [
                'Action: calculator\nAction Input: {"expression": "1 + 2 * 3"}',
                "Final Answer: the result is 7",
            ]
        ),
        tool_registry=registry,
    )

    result = asyncio.run(
        executor.run(
            "calculate",
            ToolContext(),
            system_prompt="You are a calculator agent.",
            history=None,
            temperature=0,
        )
    )

    assert result.answer == "the result is 7"
    assert result.llm_call_count == 2
    assert len(result.steps) == 1
    assert result.steps[0].ok is True
    assert result.steps[0].action == "calculator"
    assert result.steps[0].action_input == {"expression": "1 + 2 * 3"}
    assert result.steps[0].observation == "7"
    assert result.steps[0].tool_result is not None
    assert result.steps[0].tool_result.data == {"result": 7}
    assert result.agent_mode == AGENT_MODE_REACT_TEXT


def test_agent_records_invalid_tool_input_and_allows_final_answer():
    registry = make_registry(CalculatorTool())
    result = run_agent(
        [
            'Action: calculator\nAction Input: {"expression": ""}',
            "Final Answer: I could not calculate that expression.",
        ],
        registry,
    )

    assert result.answer == "I could not calculate that expression."
    assert len(result.steps) == 1
    assert result.steps[0].ok is False
    assert result.steps[0].error == "invalid_tool_input"
    assert result.steps[0].tool_result is not None
    assert result.steps[0].tool_result.error == "invalid_tool_input"
    assert result.agent_mode == AGENT_MODE_REACT_TEXT


def test_agent_records_unknown_tool_and_allows_final_answer():
    result = run_agent(
        [
            'Action: missing_tool\nAction Input: {"value": "x"}',
            "Final Answer: I cannot use that tool.",
        ],
        make_registry(),
    )

    assert result.answer == "I cannot use that tool."
    assert len(result.steps) == 1
    assert result.steps[0].ok is False
    assert result.steps[0].error == "unknown_tool"
    assert result.agent_mode == AGENT_MODE_REACT_TEXT


def test_agent_records_parse_error_and_allows_final_answer():
    registry = make_registry(CalculatorTool())
    executor = ReactTextAgentExecutor(
        llm=FakeLLM(
            [
                "Action: calculator",
                "Final Answer: fixed the output format.",
            ]
        ),
        tool_registry=registry,
    )

    result = asyncio.run(
        executor.run(
            "calculate",
            ToolContext(),
            system_prompt="system",
            max_steps=2,
        )
    )

    assert result.answer == "fixed the output format."
    assert result.llm_call_count == 2
    assert len(result.steps) == 1
    assert result.steps[0].ok is False
    assert result.steps[0].error == "missing_action_input"
    assert result.steps[0].observation == (
        "Agent output parse error: missing_action_input"
    )
    assert result.agent_mode == AGENT_MODE_REACT_TEXT


def test_agent_stops_after_max_steps():
    registry = make_registry(CalculatorTool())
    result = run_agent(
        [
            'Action: calculator\nAction Input: {"expression": "1 + 1"}',
            'Action: calculator\nAction Input: {"expression": "2 + 2"}',
        ],
        registry,
        max_steps=2,
    )

    assert result.answer == MAX_STEPS_ANSWER
    assert result.max_steps_reached is True
    assert result.llm_call_count == 2
    assert len(result.steps) == 2
    assert result.agent_mode == AGENT_MODE_REACT_TEXT


def test_agent_passes_tool_context_to_tools():
    tool = CaptureContextTool()
    registry = make_registry(tool)
    context = ToolContext(
        workspace_id="workspace-abc",
        conversation_id="conversation-abc",
    )

    result = run_agent(
        [
            'Action: capture_context\nAction Input: {"value": "hello"}',
            "Final Answer: captured",
        ],
        registry,
        context=context,
    )

    assert result.answer == "captured"
    assert tool.seen_workspace_id == "workspace-abc"
    assert tool.seen_conversation_id == "conversation-abc"
    assert result.agent_mode == AGENT_MODE_REACT_TEXT


@pytest.mark.parametrize("max_steps", [0, 11])
def test_agent_rejects_invalid_max_steps(max_steps):
    executor = ReactTextAgentExecutor(
        llm=FakeLLM(["Final Answer: done"]),
        tool_registry=make_registry(),
    )

    with pytest.raises(ValueError, match="max_steps must be between"):
        asyncio.run(
            executor.run(
                "hello",
                ToolContext(),
                system_prompt="system",
                max_steps=max_steps,
            )
        )


def test_agent_prompt_builder_lists_tools_and_protocol():
    registry = make_registry(CalculatorTool())

    prompt = build_agent_system_prompt("Base prompt.", registry)

    assert "Base prompt." in prompt
    assert "calculator" in prompt
    assert "Action Input: <JSON object>" in prompt
    assert "Final Answer: <answer to the user>" in prompt
