from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel

from app.models.workspace import utc_now

DEFAULT_CONVERSATION_TITLE = "New conversation"


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(
        foreign_key="workspaces.id",
        ondelete="CASCADE",
        index=True,
    )
    title: str = Field(default=DEFAULT_CONVERSATION_TITLE, max_length=255)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ConversationMessage(SQLModel, table=True):
    __tablename__ = "conversation_messages"

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    conversation_id: str = Field(
        foreign_key="conversations.id",
        ondelete="CASCADE",
        index=True,
    )
    role: str = Field(max_length=16)
    content: str
    sources: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    metrics: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=utc_now, index=True)
