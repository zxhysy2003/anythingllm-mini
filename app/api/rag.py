from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.chat_service import ChatServiceError
from app.services.rag_service import RAGQueryError, RAGQueryResult, rag_service

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


@router.post("/query", response_model=RAGQueryResult)
async def query_documents(request: RAGQueryRequest) -> RAGQueryResult:
    try:
        return await rag_service.query(request.question)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ChatServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except RAGQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
