from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from app.core.agent_executor import DEFAULT_AGENT_STEPS, MAX_AGENT_STEPS
from app.core.agent_modes import AGENT_MODE_REACT_TEXT
from app.services.rag_service import RAGSource
from app.tools.artifacts import ToolSourceArtifact
from app.tools.interactions import PendingClarification, ToolInteraction

AgentModeRequest = Literal["react_text", "native_tool_calling"]


class WorkspaceAgentRequest(BaseModel):
    message: str = Field(min_length=1)
    max_steps: int = Field(default=DEFAULT_AGENT_STEPS, ge=1, le=MAX_AGENT_STEPS)
    agent_mode: AgentModeRequest = AGENT_MODE_REACT_TEXT
    approved_tool_call_ids: list[str] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("message cannot be empty")
        return message


class AgentToolArtifactsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sources: list[ToolSourceArtifact]
    outputs: dict[str, JsonValue]


class AgentToolResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ok: bool
    content: str
    artifacts: AgentToolArtifactsRead
    interaction: ToolInteraction | None
    error: str | None
    error_details: dict[str, JsonValue]


class AgentStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step_index: int
    llm_output: str
    action: str | None
    action_input: dict[str, Any]
    observation: str | None
    ok: bool
    error: str | None
    tool_result: AgentToolResultRead | None


class WorkspaceAgentMetricsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    max_steps: int
    llm_call_count: int
    step_count: int
    tool_call_count: int
    failed_step_count: int
    source_count: int
    max_steps_reached: bool
    total_latency_ms: int


class WorkspaceAgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    conversation_id: str
    agent_invocation_id: str
    message: str
    status: str
    answer: str | None
    pending_input: PendingClarification | None = None
    steps: list[AgentStepRead]
    sources: list[RAGSource]
    provider: str | None
    model: str | None
    metrics: WorkspaceAgentMetricsRead


class AgentInvocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    conversation_id: str
    user_message_id: str
    assistant_message_id: str | None
    answer: str | None
    input_message: str
    agent_mode: str
    status: str
    provider: str | None
    model: str | None
    max_steps: int
    llm_call_count: int
    step_count: int
    tool_call_count: int
    failed_step_count: int
    source_count: int
    max_steps_reached: bool
    total_latency_ms: int
    started_at: datetime
    ended_at: datetime | None
    created_at: datetime
    pending_input: PendingClarification | None = None
    steps: list[AgentStepRead]


class AgentContinuationRequest(BaseModel):
    answer: str | None = Field(default=None, max_length=2_000)
    skip: bool = False

    @field_validator("answer")
    @classmethod
    def strip_answer(cls, value: str | None) -> str | None:
        if value is None:
            return None
        answer = value.strip()
        if not answer:
            raise ValueError("answer cannot be empty")
        return answer

    @model_validator(mode="after")
    def validate_continuation(self) -> "AgentContinuationRequest":
        if self.skip and self.answer is not None:
            raise ValueError("answer and skip cannot be sent together")
        if not self.skip and self.answer is None:
            raise ValueError("answer is required unless skip is true")
        return self
