from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.core.llm import DEFAULT_SYSTEM_PROMPT
from app.services.rag_service import RAGSource


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    system_prompt: str = Field(default=DEFAULT_SYSTEM_PROMPT, min_length=1)
    temperature: float = Field(default=0.7, ge=0, le=2)
    history_limit: int = Field(default=20, ge=0)
    chat_mode: Literal["chat", "query"] = "chat"
    top_k: int = Field(default=settings.top_k, gt=0)
    similarity_threshold: float = Field(
        default=settings.similarity_threshold,
        ge=0,
        le=1,
    )

    @field_validator("name", "system_prompt")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be empty")
        return normalized


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    system_prompt: str | None = Field(default=None, min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    history_limit: int | None = Field(default=None, ge=0)
    chat_mode: Literal["chat", "query"] | None = None
    top_k: int | None = Field(default=None, gt=0)
    similarity_threshold: float | None = Field(default=None, ge=0, le=1)

    @field_validator("name", "system_prompt")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be empty")
        return normalized


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    system_prompt: str
    temperature: float
    history_limit: int
    chat_mode: str
    top_k: int
    similarity_threshold: float
    created_at: datetime
    updated_at: datetime


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    role: str
    content: str
    sources: list[RAGSource]
    provider: str | None
    model: str | None
    created_at: datetime


class WorkspaceDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    original_filename: str
    stored_filename: str
    content_type: str | None
    extension: str
    size_bytes: int
    character_count: int
    chunk_count: int
    created_at: datetime


class WorkspaceDocumentDeleteResponse(BaseModel):
    id: str
    workspace_id: str
    original_filename: str
    deleted_chunks: int
    upload_file_deleted: bool
    parsed_file_deleted: bool


class WorkspaceChatRequest(BaseModel):
    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("message cannot be empty")
        return message
