from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.errors import to_http_exception
from app.core.llm import DEFAULT_SYSTEM_PROMPT
from app.services.chat_service import ChatResult, chat_service
from app.services.exceptions import ChatServiceError

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "message": "Summarize what AnythingLLM Mini can do in V0.",
                    "system_prompt": DEFAULT_SYSTEM_PROMPT,
                    "temperature": 0.7,
                }
            ]
        }
    )

    message: str = Field(
        ...,
        min_length=1,
        description="User message to send to DeepSeek.",
    )
    system_prompt: str = Field(
        default=DEFAULT_SYSTEM_PROMPT,
        description="System instruction prepended to this single chat turn.",
    )
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Optional sampling temperature. Defaults to the LLM wrapper setting.",
    )

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("message cannot be empty")
        return message


@router.post("", response_model=ChatResult)
async def create_chat(request: ChatRequest) -> ChatResult:
    try:
        return await chat_service.chat(
            message=request.message,
            system_prompt=request.system_prompt,
            temperature=request.temperature,
        )
    except ValueError as exc:
        raise to_http_exception(exc) from exc
    except ChatServiceError as exc:
        raise to_http_exception(exc) from exc
