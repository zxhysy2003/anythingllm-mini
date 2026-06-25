from datetime import UTC, datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel

from app.core.config import settings
from app.core.llm import DEFAULT_SYSTEM_PROMPT


def utc_now() -> datetime:
    return datetime.now(UTC)


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    name: str = Field(index=True, max_length=255)
    system_prompt: str = Field(default=DEFAULT_SYSTEM_PROMPT)
    temperature: float = Field(default=0.7)
    history_limit: int = Field(default=20)
    chat_mode: str = Field(default="chat", max_length=16)
    top_k: int = Field(default_factory=lambda: settings.top_k)
    similarity_threshold: float = Field(
        default_factory=lambda: settings.similarity_threshold
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
