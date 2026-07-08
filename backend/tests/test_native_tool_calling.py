import asyncio

from pydantic import BaseModel, Field

from app.core.agent_executor import MAX_STEPS_ANSWER
from app.core.agent_modes import AGENT_MODE_NATIVE_TOOL_CALLING
from app.core.llm import DeepSeekToolCall, DeepSeekToolCallResult
from app.core.native_tool_calling import DeepSeekNativeToolCallingExecutor
from app.tools.calculator import CalculatorTool
from app.tools.registry import (
    TOOL_CONFIRMATION_REQUIRED,
    ToolContext,
    ToolRegistry,
    ToolResult,
)
from tests.fakes import CollectingEventEmitter, ConfirmationRequiredTool


class ScriptedToolCallingClient:
    def __init__(self, responses: list[DeepSeekToolCallResult]):
        self.responses = responses
        self.calls = []

    async def chat_with_tools(self, messages, tools, temperature, tool_choice):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "temperature": temperature,
                "tool_choice": tool_choice,
            }
        )
        return self.responses[len(self.calls) - 1]


class CaptureInput(BaseModel):
    value: str = Field(min_length=1)


class CaptureContextTool:
    name = "capture_context"
    description = "Capture tool context for native tool calling tests."
    input_model = CaptureInput

    def __init__(self):
        self.workspace_id = None
        self.conversation_id = None

    async def run(self, input_data, context):
        self.workspace_id = context.workspace_id
        self.conversation_id = context.conversation_id
        return ToolResult(ok=True, content=f"captured {input_data.value}")


def make_registry(*tools):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def tool_result(content, *, tool_calls=None, finish_reason="stop"):
    return DeepSeekToolCallResult(
        content=content,
        tool_calls=tool_calls or [],
        finish_reason=finish_reason,
        provider="fake",
        model="fake-native-model",
    )


def tool_call(name, arguments, *, call_id="call-1"):
    return DeepSeekToolCall(
        id=call_id,
        name=name,
        arguments=arguments,
    )


def test_native_executor_returns_final_answer_without_tool_call():
    client = ScriptedToolCallingClient([tool_result("no tool needed")])
    executor = DeepSeekNativeToolCallingExecutor(
        llm=client,
        tool_registry=make_registry(CalculatorTool()),
    )

    result = asyncio.run(
        executor.run(
            " hello ",
            ToolContext(workspace_id="workspace-1"),
            system_prompt="You are concise.",
            history=[{"role": "user", "content": "previous"}],
            temperature=0.1,
        )
    )

    assert result.answer == "no tool needed"
    assert result.steps == []
    assert result.provider == "fake"
    assert result.model == "fake-native-model"
    assert result.llm_call_count == 1
    assert result.agent_mode == AGENT_MODE_NATIVE_TOOL_CALLING
    assert client.calls[0]["tool_choice"] == "auto"
    assert client.calls[0]["tools"][0]["function"]["name"] == "calculator"
    assert client.calls[0]["messages"][-1] == {"role": "user", "content": "hello"}


def test_native_executor_calls_calculator_then_returns_final_answer():
    client = ScriptedToolCallingClient(
        [
            tool_result(
                "",
                tool_calls=[tool_call("calculator", '{"expression": "1 + 2 * 3"}')],
                finish_reason="tool_calls",
            ),
            tool_result("the result is 7"),
        ]
    )
    executor = DeepSeekNativeToolCallingExecutor(
        llm=client,
        tool_registry=make_registry(CalculatorTool()),
    )

    result = asyncio.run(executor.run("calculate", ToolContext()))

    assert result.answer == "the result is 7"
    assert result.llm_call_count == 2
    assert len(result.steps) == 1
    assert result.steps[0].action == "calculator"
    assert result.steps[0].action_input == {"expression": "1 + 2 * 3"}
    assert result.steps[0].observation == "7"
    assert result.steps[0].ok is True
    assert result.steps[0].tool_result is not None
    assert result.steps[0].tool_result.data == {"result": 7}
    assert client.calls[1]["messages"][-2]["role"] == "assistant"
    assert client.calls[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "7",
    }


def test_native_executor_emits_tool_events():
    emitter = CollectingEventEmitter()
    client = ScriptedToolCallingClient(
        [
            tool_result(
                "",
                tool_calls=[tool_call("calculator", '{"expression": "1 + 2"}')],
                finish_reason="tool_calls",
            ),
            tool_result("the result is 3"),
        ]
    )
    executor = DeepSeekNativeToolCallingExecutor(
        llm=client,
        tool_registry=make_registry(CalculatorTool()),
    )

    result = asyncio.run(
        executor.run("calculate", ToolContext(), event_emitter=emitter)
    )

    assert result.answer == "the result is 3"
    assert [event["type"] for event in emitter.events] == [
        "llm_started",
        "llm_finished",
        "tool_started",
        "tool_finished",
        "llm_started",
        "llm_finished",
    ]
    assert emitter.events[2]["payload"]["tool_name"] == "calculator"
    assert emitter.events[3]["payload"]["step"]["observation"] == "3"


def test_native_executor_passes_tool_context_to_tools():
    tool = CaptureContextTool()
    client = ScriptedToolCallingClient(
        [
            tool_result(
                "",
                tool_calls=[tool_call("capture_context", '{"value": "hello"}')],
                finish_reason="tool_calls",
            ),
            tool_result("captured"),
        ]
    )
    executor = DeepSeekNativeToolCallingExecutor(
        llm=client,
        tool_registry=make_registry(tool),
    )

    result = asyncio.run(
        executor.run(
            "capture",
            ToolContext(
                workspace_id="workspace-abc",
                conversation_id="conversation-abc",
            ),
        )
    )

    assert result.answer == "captured"
    assert tool.workspace_id == "workspace-abc"
    assert tool.conversation_id == "conversation-abc"


def test_native_executor_records_argument_json_error_and_allows_final_answer():
    client = ScriptedToolCallingClient(
        [
            tool_result(
                "",
                tool_calls=[tool_call("calculator", "{bad json}")],
                finish_reason="tool_calls",
            ),
            tool_result("fixed"),
        ]
    )
    executor = DeepSeekNativeToolCallingExecutor(
        llm=client,
        tool_registry=make_registry(CalculatorTool()),
    )

    result = asyncio.run(executor.run("calculate", ToolContext()))

    assert result.answer == "fixed"
    assert len(result.steps) == 1
    assert result.steps[0].ok is False
    assert result.steps[0].error == "invalid_tool_arguments_json"
    assert client.calls[1]["messages"][-1]["content"] == (
        "Tool call argument parse error: invalid_tool_arguments_json"
    )


def test_native_executor_records_unknown_and_invalid_tool_steps():
    client = ScriptedToolCallingClient(
        [
            tool_result(
                "",
                tool_calls=[
                    tool_call("missing_tool", '{"value": "x"}', call_id="call-1"),
                    tool_call("calculator", '{"expression": ""}', call_id="call-2"),
                ],
                finish_reason="tool_calls",
            ),
            tool_result("done"),
        ]
    )
    executor = DeepSeekNativeToolCallingExecutor(
        llm=client,
        tool_registry=make_registry(CalculatorTool()),
    )

    result = asyncio.run(executor.run("use tools", ToolContext()))

    assert result.answer == "done"
    assert [step.step_index for step in result.steps] == [1, 2]
    assert result.steps[0].error == "unknown_tool"
    assert result.steps[1].error == "invalid_tool_input"
    assert client.calls[1]["messages"][-2]["tool_call_id"] == "call-1"
    assert client.calls[1]["messages"][-1]["tool_call_id"] == "call-2"


def test_native_executor_records_confirmation_required_tool_without_executing():
    tool = ConfirmationRequiredTool()
    client = ScriptedToolCallingClient(
        [
            tool_result(
                "",
                tool_calls=[tool_call("confirmation_required", '{"value": "hello"}')],
                finish_reason="tool_calls",
            ),
            tool_result("waiting for approval"),
        ]
    )
    executor = DeepSeekNativeToolCallingExecutor(
        llm=client,
        tool_registry=make_registry(tool),
    )

    result = asyncio.run(
        executor.run(
            "use tool",
            ToolContext(
                workspace_id="workspace-1",
                conversation_id="conversation-1",
                agent_mode=AGENT_MODE_NATIVE_TOOL_CALLING,
            ),
        )
    )

    assert result.answer == "waiting for approval"
    assert len(result.steps) == 1
    assert result.steps[0].ok is False
    assert result.steps[0].error == TOOL_CONFIRMATION_REQUIRED
    assert result.steps[0].tool_result is not None
    assert result.steps[0].tool_result.data["approval_id"].startswith("tool_approval_")
    assert tool.executed is False
    assert client.calls[1]["messages"][-1]["content"] == (
        "Tool blocked by policy: confirmation_required."
    )


def test_native_executor_stops_after_max_steps():
    client = ScriptedToolCallingClient(
        [
            tool_result(
                "",
                tool_calls=[tool_call("calculator", '{"expression": "1 + 1"}')],
                finish_reason="tool_calls",
            )
        ]
    )
    executor = DeepSeekNativeToolCallingExecutor(
        llm=client,
        tool_registry=make_registry(CalculatorTool()),
    )

    result = asyncio.run(executor.run("calculate", ToolContext(), max_steps=1))

    assert result.answer == MAX_STEPS_ANSWER
    assert result.max_steps_reached is True
    assert result.llm_call_count == 1
    assert len(result.steps) == 1
    assert result.agent_mode == AGENT_MODE_NATIVE_TOOL_CALLING


def test_native_executor_emits_max_steps_reached_event():
    emitter = CollectingEventEmitter()
    client = ScriptedToolCallingClient(
        [
            tool_result(
                "",
                tool_calls=[tool_call("calculator", '{"expression": "1 + 1"}')],
                finish_reason="tool_calls",
            )
        ]
    )
    executor = DeepSeekNativeToolCallingExecutor(
        llm=client,
        tool_registry=make_registry(CalculatorTool()),
    )

    result = asyncio.run(
        executor.run(
            "calculate",
            ToolContext(),
            max_steps=1,
            event_emitter=emitter,
        )
    )

    assert result.max_steps_reached is True
    assert emitter.events[-1]["type"] == "max_steps_reached"
