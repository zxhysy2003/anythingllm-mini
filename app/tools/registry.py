from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError


class ToolContext(BaseModel):
    workspace_id: str | None = None
    conversation_id: str | None = None


class ToolResult(BaseModel):
    ok: bool
    content: str
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class ToolDescription(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class BaseTool(Protocol):
    name: str
    description: str
    input_model: type[BaseModel]

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
            )
            for name, tool in self._tools.items()
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
                data={"details": exc.errors()},
                error="invalid_tool_input",
            )

        try:
            return await tool.run(input_data, context)
        except Exception as exc:
            return ToolResult(
                ok=False,
                content=f"Tool execution failed: {exc}",
                error="tool_execution_failed",
            )


def create_default_tool_registry() -> ToolRegistry:
    from app.tools.calculator import CalculatorTool
    from app.tools.document_tools import WorkspaceDocumentSearchTool

    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(WorkspaceDocumentSearchTool())
    return registry
