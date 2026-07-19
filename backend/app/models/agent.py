from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Column, Index, JSON, text
from sqlmodel import Field, SQLModel

from app.core.agent_modes import AGENT_MODE_REACT_TEXT
from app.models.workspace import utc_now

AGENT_INVOCATION_STATUS_COMPLETED = "completed"
AGENT_INVOCATION_STATUS_MAX_STEPS_REACHED = "max_steps_reached"
AGENT_INVOCATION_STATUS_NEEDS_INPUT = "needs_input"


class AgentInvocation(SQLModel, table=True):
    __tablename__ = "agent_invocations"
    __table_args__ = (
        Index(
            "uq_agent_invocations_pending_conversation",
            "conversation_id",
            unique=True,
            sqlite_where=text("status = 'needs_input'"),
        ),
    )

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(
        foreign_key="workspaces.id",
        ondelete="CASCADE",
        index=True,
    )
    conversation_id: str = Field(
        foreign_key="conversations.id",
        ondelete="CASCADE",
        index=True,
    )
    user_message_id: str = Field(
        foreign_key="conversation_messages.id",
        ondelete="CASCADE",
        index=True,
    )
    assistant_message_id: str | None = Field(
        default=None,
        foreign_key="conversation_messages.id",
        ondelete="CASCADE",
        index=True,
    )
    input_message: str
    agent_mode: str = Field(default=AGENT_MODE_REACT_TEXT, max_length=32)
    status: str = Field(max_length=32)
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=255)
    max_steps: int
    llm_call_count: int
    step_count: int
    tool_call_count: int
    failed_step_count: int
    source_count: int
    max_steps_reached: bool
    total_latency_ms: int
    started_at: datetime
    ended_at: datetime | None = None
    pending_input: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    resume_state: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    created_at: datetime = Field(default_factory=utc_now, index=True)


class AgentStepRecord(SQLModel, table=True):
    __tablename__ = "agent_steps"

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    invocation_id: str = Field(
        foreign_key="agent_invocations.id",
        ondelete="CASCADE",
        index=True,
    )
    step_index: int = Field(index=True)
    llm_output: str
    action: str | None = Field(default=None, max_length=255)
    action_input: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    observation: str | None = None
    ok: bool
    error: str | None = Field(default=None, max_length=255)
    tool_result: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    created_at: datetime = Field(default_factory=utc_now, index=True)
