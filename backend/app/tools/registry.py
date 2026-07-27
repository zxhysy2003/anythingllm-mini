import hashlib
import json
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
from sqlmodel import Session

from app.tools.artifacts import ToolArtifacts
from app.tools.interactions import ToolInteraction

ToolRiskLevel = Literal["low", "medium", "high"]
TOOL_RISK_LEVELS = {"low", "medium", "high"}
DEFAULT_TOOL_RISK_LEVEL: ToolRiskLevel = "low"
TOOL_CONFIRMATION_REQUIRED = "tool_confirmation_required"
TOOL_BLOCKED_BY_POLICY = "tool_blocked_by_policy"


class ToolContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    workspace_id: str | None = None
    conversation_id: str | None = None
    agent_mode: str | None = None
    approved_tool_call_ids: set[str] = Field(default_factory=set)
    clarification_count: int = Field(default=0, ge=0)
    remaining_llm_calls: int | None = Field(default=None, ge=0)
    session: Session | None = Field(default=None, exclude=True, repr=False)
    progress_reporter: Any | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    content: str
    artifacts: ToolArtifacts = Field(default_factory=ToolArtifacts)
    interaction: ToolInteraction | None = None
    error: str | None = None
    error_details: dict[str, JsonValue] = Field(default_factory=dict)


class ToolDescription(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    risk_level: ToolRiskLevel = DEFAULT_TOOL_RISK_LEVEL
    side_effects: bool = False
    requires_confirmation: bool = False
    allowed_in_agent_modes: list[str] | None = None


class BaseTool(Protocol):
    name: str
    description: str
    input_model: type[BaseModel]
    risk_level: ToolRiskLevel
    side_effects: bool
    requires_confirmation: bool
    allowed_in_agent_modes: list[str] | None

    async def run(
        self,
        input_data: BaseModel,
        context: ToolContext,
    ) -> ToolResult: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        name = tool.name.strip()
        if not name:
            raise ValueError("tool name cannot be empty")
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> BaseTool:
        return self._tools[name]

    def list_tools(self) -> list[ToolDescription]:
        return [
            ToolDescription(
                name=name,
                description=tool.description,
                input_schema=tool.input_model.model_json_schema(),
                risk_level=_tool_risk_level(tool),
                side_effects=bool(getattr(tool, "side_effects", False)),
                requires_confirmation=bool(
                    getattr(tool, "requires_confirmation", False)
                ),
                allowed_in_agent_modes=_tool_allowed_agent_modes(tool),
            )
            for name, tool in self._tools.items()
        ]

    def list_openai_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": description.name,
                    "description": description.description,
                    "parameters": description.input_schema,
                },
            }
            for description in self.list_tools()
        ]

    async def run(
        self,
        name: str,
        raw_input: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        try:
            tool = self.get(name)
        except KeyError:
            return ToolResult(
                ok=False,
                content=f"Unknown tool: {name}",
                error="unknown_tool",
            )

        try:
            input_data = tool.input_model.model_validate(raw_input)
        except ValidationError as exc:
            return ToolResult(
                ok=False,
                content="Tool input validation failed.",
                error="invalid_tool_input",
                error_details={"details": json.loads(exc.json())},
            )

        policy_result = self._evaluate_policy(
            tool=tool,
            name=name,
            input_data=input_data,
            context=context,
        )
        if policy_result is not None:
            return policy_result

        try:
            return await tool.run(input_data, context)
        except Exception as exc:
            return ToolResult(
                ok=False,
                content=f"Tool execution failed: {exc}",
                error="tool_execution_failed",
            )

    def _evaluate_policy(
        self,
        *,
        tool: BaseTool,
        name: str,
        input_data: BaseModel,
        context: ToolContext,
    ) -> ToolResult | None:
        allowed_in_agent_modes = _tool_allowed_agent_modes(tool)
        if (
            allowed_in_agent_modes is not None
            and context.agent_mode not in allowed_in_agent_modes
        ):
            return _policy_failure_result(
                tool=tool,
                name=name,
                error=TOOL_BLOCKED_BY_POLICY,
                reason="agent_mode_not_allowed",
            )

        if not bool(getattr(tool, "requires_confirmation", False)):
            return None

        approval_id = build_tool_approval_id(
            tool_name=name,
            action_input=input_data.model_dump(mode="json"),
            context=context,
        )
        if approval_id in context.approved_tool_call_ids:
            return None

        return _policy_failure_result(
            tool=tool,
            name=name,
            error=TOOL_CONFIRMATION_REQUIRED,
            reason="confirmation_required",
            approval_id=approval_id,
        )


def create_default_tool_registry() -> ToolRegistry:
    from app.tools.calculator import CalculatorTool
    from app.tools.clarifying_question import ClarifyingQuestionTool
    from app.tools.document_tools import (
        WorkspaceDocumentSearchTool,
        WorkspaceDocumentSummaryTool,
    )

    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(ClarifyingQuestionTool())
    registry.register(WorkspaceDocumentSearchTool())
    registry.register(WorkspaceDocumentSummaryTool())
    return registry


def build_tool_approval_id(
    *,
    tool_name: str,
    action_input: dict[str, Any],
    context: ToolContext,
) -> str:
    normalized_action_input = json.dumps(
        action_input,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    raw_approval_id = "|".join(
        [
            tool_name,
            normalized_action_input,
            context.workspace_id or "",
            context.conversation_id or "",
            context.agent_mode or "",
        ]
    )
    digest = hashlib.sha256(raw_approval_id.encode("utf-8")).hexdigest()[:16]
    return f"tool_approval_{digest}"


def _policy_failure_result(
    *,
    tool: BaseTool,
    name: str,
    error: str,
    reason: str,
    approval_id: str | None = None,
) -> ToolResult:
    error_details: dict[str, JsonValue] = {
        "reason": reason,
        "tool_name": name,
        "risk_level": _tool_risk_level(tool),
        "side_effects": bool(getattr(tool, "side_effects", False)),
        "requires_confirmation": bool(getattr(tool, "requires_confirmation", False)),
    }
    if approval_id is not None:
        error_details["approval_id"] = approval_id
    return ToolResult(
        ok=False,
        content=f"Tool blocked by policy: {reason}.",
        error=error,
        error_details=error_details,
    )


def _tool_risk_level(tool: BaseTool) -> ToolRiskLevel:
    risk_level = getattr(tool, "risk_level", DEFAULT_TOOL_RISK_LEVEL)
    if risk_level not in TOOL_RISK_LEVELS:
        return DEFAULT_TOOL_RISK_LEVEL
    return risk_level


def _tool_allowed_agent_modes(tool: BaseTool) -> list[str] | None:
    allowed_in_agent_modes = getattr(tool, "allowed_in_agent_modes", None)
    if allowed_in_agent_modes is None:
        return None
    return list(allowed_in_agent_modes)
