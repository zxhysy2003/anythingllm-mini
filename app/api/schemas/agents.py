from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.agent_executor import DEFAULT_AGENT_STEPS, MAX_AGENT_STEPS
from app.core.agent_modes import AGENT_MODE_REACT_TEXT
from app.services.rag_service import RAGSource

AgentModeRequest = Literal["react_text", "native_tool_calling"]


class WorkspaceAgentRequest(BaseModel):
    message: str = Field(min_length=1)
    max_steps: int = Field(default=DEFAULT_AGENT_STEPS, ge=1, le=MAX_AGENT_STEPS)
    agent_mode: AgentModeRequest = AGENT_MODE_REACT_TEXT

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("message cannot be empty")
        return message


class AgentToolResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ok: bool
    content: str
    data: dict[str, Any]
    error: str | None


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
    answer: str
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
    assistant_message_id: str
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
    ended_at: datetime
    created_at: datetime
    steps: list[AgentStepRead]
