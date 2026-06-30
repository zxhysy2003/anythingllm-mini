from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.errors import to_http_exception
from app.services.exceptions import ChatServiceError, RAGQueryError
from app.services.rag_service import RAGQueryResult, rag_service

router = APIRouter(prefix="/rag", tags=["rag"])


class RAGQueryRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"question": "What does the uploaded document explain?"}]
        }
    )

    question: str = Field(
        ...,
        min_length=1,
        description="Question to answer from indexed document context.",
    )

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        question = value.strip()
        if not question:
            raise ValueError("question cannot be empty")
        return question


@router.post(
    "/query",
    response_model=RAGQueryResult,
    deprecated=True,
    summary="Legacy V2 global RAG query",
    description=(
        "Legacy V2 learning endpoint that queries the global RAG scope. "
        "For V4 and workspace-aware chat, use "
        "`/workspaces/{workspace_id}/conversations/{conversation_id}/chat`."
    ),
)
async def query_documents(request: RAGQueryRequest) -> RAGQueryResult:
    try:
        return await rag_service.query(request.question)
    except ValueError as exc:
        raise to_http_exception(exc) from exc
    except ChatServiceError as exc:
        raise to_http_exception(exc) from exc
    except RAGQueryError as exc:
        raise to_http_exception(exc) from exc
