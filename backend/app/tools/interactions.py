from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ClarificationInputType = Literal["text", "choice"]
ClarificationResolutionKind = Literal["answered", "skipped", "timed_out"]


class ClarificationRequest(BaseModel):
    """A structured question an Agent must resolve before it can continue."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    input_type: ClarificationInputType
    choices: list[str] = Field(default_factory=list)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        question = value.strip()
        if not question:
            raise ValueError("question cannot be empty")
        return question

    @field_validator("choices")
    @classmethod
    def normalize_choices(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("choices cannot contain empty values")
        if any(len(value) > 120 for value in normalized):
            raise ValueError("choices must be at most 120 characters")
        if len(set(normalized)) != len(normalized):
            raise ValueError("choices must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_input_type(self) -> "ClarificationRequest":
        if self.input_type == "choice" and not 2 <= len(self.choices) <= 8:
            raise ValueError("choice questions require 2-8 choices")
        if self.input_type == "text" and self.choices:
            raise ValueError("text questions cannot define choices")
        return self


class PendingClarification(ClarificationRequest):
    """A clarification request plus the service-owned expiry timestamp."""

    expires_at: datetime


class ClarificationResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ClarificationResolutionKind
    answer: str | None = Field(default=None, max_length=2_000)

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
    def validate_resolution(self) -> "ClarificationResolution":
        if self.kind == "answered" and self.answer is None:
            raise ValueError("answered clarifications require an answer")
        if self.kind != "answered" and self.answer is not None:
            raise ValueError("only answered clarifications can include an answer")
        return self


class ToolInteraction(BaseModel):
    """A non-artifact tool result that changes the Agent control flow."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["clarification"]
    request: ClarificationRequest
    resolution: ClarificationResolution | None = None
