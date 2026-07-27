import asyncio

import pytest
from pydantic import BaseModel, Field

from app.tools.registry import (
    TOOL_BLOCKED_BY_POLICY,
    TOOL_CONFIRMATION_REQUIRED,
    ToolContext,
    ToolRegistry,
    ToolResult,
    build_tool_approval_id,
    create_default_tool_registry,
)
from tests.fakes import ConfirmationRequiredTool


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


class RestrictedAgentModeTool(EchoTool):
    name = "restricted_agent_mode"
    allowed_in_agent_modes = ["native_tool_calling"]


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

    openai_tools = registry.list_openai_tools()
    assert openai_tools == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo input for registry tests.",
                "parameters": descriptions[0].input_schema,
            },
        }
    ]


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
    assert result.error_details["details"][0]["loc"] == ["value"]


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


def test_registry_returns_default_tool_policy_metadata():
    registry = create_default_tool_registry()

    descriptions = {
        description.name: description for description in registry.list_tools()
    }

    assert descriptions["calculator"].risk_level == "low"
    assert descriptions["calculator"].side_effects is False
    assert descriptions["calculator"].requires_confirmation is False
    assert descriptions["calculator"].allowed_in_agent_modes is None
    assert descriptions["workspace_document_search"].risk_level == "low"
    assert descriptions["workspace_document_search"].side_effects is False
    assert descriptions["workspace_document_search"].requires_confirmation is False
    assert descriptions["workspace_document_search"].allowed_in_agent_modes is None
    assert descriptions["request_user_input"].risk_level == "low"
    assert descriptions["request_user_input"].side_effects is False
    assert descriptions["request_user_input"].requires_confirmation is False
    assert descriptions["request_user_input"].allowed_in_agent_modes is None


def test_registry_blocks_confirmation_required_tool_without_approval():
    tool = ConfirmationRequiredTool()
    registry = ToolRegistry()
    registry.register(tool)
    context = ToolContext(
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        agent_mode="react_text",
    )

    result = asyncio.run(registry.run(tool.name, {"value": "hello"}, context))

    assert result.ok is False
    assert result.error == TOOL_CONFIRMATION_REQUIRED
    assert result.error_details["reason"] == "confirmation_required"
    assert result.error_details["tool_name"] == tool.name
    assert result.error_details["risk_level"] == "high"
    assert result.error_details["side_effects"] is True
    assert result.error_details["requires_confirmation"] is True
    assert result.error_details["approval_id"] == build_tool_approval_id(
        tool_name=tool.name,
        action_input={"value": "hello"},
        context=context,
    )
    assert tool.executed is False


def test_registry_runs_confirmation_required_tool_with_matching_approval():
    tool = ConfirmationRequiredTool()
    registry = ToolRegistry()
    registry.register(tool)
    context = ToolContext(
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        agent_mode="react_text",
    )
    approval_id = build_tool_approval_id(
        tool_name=tool.name,
        action_input={"value": "hello"},
        context=context,
    )
    context.approved_tool_call_ids.add(approval_id)

    result = asyncio.run(registry.run(tool.name, {"value": "hello"}, context))

    assert result.ok is True
    assert result.content == "approved hello"
    assert tool.executed is True


def test_registry_blocks_tool_when_agent_mode_is_not_allowed():
    registry = ToolRegistry()
    registry.register(RestrictedAgentModeTool())

    result = asyncio.run(
        registry.run(
            "restricted_agent_mode",
            {"value": "hello"},
            ToolContext(agent_mode="react_text"),
        )
    )

    assert result.ok is False
    assert result.error == TOOL_BLOCKED_BY_POLICY
    assert result.error_details["reason"] == "agent_mode_not_allowed"


def test_registry_validates_tool_input_before_policy_check():
    tool = ConfirmationRequiredTool()
    registry = ToolRegistry()
    registry.register(tool)

    result = asyncio.run(
        registry.run(
            tool.name,
            {"value": ""},
            ToolContext(agent_mode="react_text"),
        )
    )

    assert result.ok is False
    assert result.error == "invalid_tool_input"
    assert "approval_id" not in result.error_details
    assert tool.executed is False


def test_default_tool_registry_includes_v4_tools():
    registry = create_default_tool_registry()

    assert {description.name for description in registry.list_tools()} == {
        "calculator",
        "request_user_input",
        "workspace_document_search",
        "workspace_document_summary",
    }


def test_document_summary_openai_schema_encodes_action_selector_contract():
    registry = create_default_tool_registry()

    summary_tool = next(
        tool
        for tool in registry.list_openai_tools()
        if tool["function"]["name"] == "workspace_document_summary"
    )
    schema = summary_tool["function"]["parameters"]

    assert schema["required"] == ["action"]
    assert [variant["required"] for variant in schema["oneOf"]] == [
        ["action"],
        ["action", "document_id"],
        ["action", "filename"],
    ]
    assert [
        variant["properties"]["action"]["const"] for variant in schema["oneOf"]
    ] == [
        "list",
        "summarize",
        "summarize",
    ]
    assert schema["oneOf"][1]["properties"]["document_id"]["type"] == "string"
    assert schema["oneOf"][2]["properties"]["filename"]["type"] == "string"
    assert "exactly one" in summary_tool["function"]["description"]
