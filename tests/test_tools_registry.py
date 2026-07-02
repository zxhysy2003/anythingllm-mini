import asyncio

import pytest
from pydantic import BaseModel, Field

from app.tools.registry import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    create_default_tool_registry,
)


class EchoInput(BaseModel):
    value: str = Field(min_length=1)


class EchoTool:
    name = "echo"
    description = "Echo input for registry tests."
    input_model = EchoInput

    async def run(self, input_data, context):
        del context
        return ToolResult(ok=True, content=input_data.value)


class BrokenTool:
    name = "broken"
    description = "Raise an exception for registry tests."
    input_model = EchoInput

    async def run(self, input_data, context):
        del input_data, context
        raise RuntimeError("boom")


class EmptyNameTool(EchoTool):
    name = " "


def test_registry_registers_gets_and_lists_tools():
    registry = ToolRegistry()
    tool = EchoTool()

    registry.register(tool)

    assert registry.get("echo") is tool
    descriptions = registry.list_tools()
    assert len(descriptions) == 1
    assert descriptions[0].name == "echo"
    assert descriptions[0].description == "Echo input for registry tests."
    assert descriptions[0].input_schema["properties"]["value"]["minLength"] == 1


def test_registry_rejects_empty_and_duplicate_tool_names():
    registry = ToolRegistry()
    registry.register(EchoTool())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoTool())

    with pytest.raises(ValueError, match="cannot be empty"):
        registry.register(EmptyNameTool())


def test_registry_run_returns_unknown_tool_result():
    result = asyncio.run(
        ToolRegistry().run(
            "missing",
            {"value": "hello"},
            ToolContext(),
        )
    )

    assert result.ok is False
    assert result.error == "unknown_tool"
    assert result.content == "Unknown tool: missing"


def test_registry_run_validates_tool_input():
    registry = ToolRegistry()
    registry.register(EchoTool())

    result = asyncio.run(
        registry.run(
            "echo",
            {"value": ""},
            ToolContext(),
        )
    )

    assert result.ok is False
    assert result.error == "invalid_tool_input"
    assert result.data["details"][0]["loc"] == ("value",)


def test_registry_run_wraps_tool_exceptions():
    registry = ToolRegistry()
    registry.register(BrokenTool())

    result = asyncio.run(
        registry.run(
            "broken",
            {"value": "hello"},
            ToolContext(),
        )
    )

    assert result.ok is False
    assert result.error == "tool_execution_failed"
    assert "boom" in result.content


def test_default_tool_registry_includes_v4_tools():
    registry = create_default_tool_registry()

    assert {description.name for description in registry.list_tools()} == {
        "calculator",
        "workspace_document_search",
    }
