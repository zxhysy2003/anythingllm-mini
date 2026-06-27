from typing import Annotated

from fastapi import APIRouter, File, UploadFile, status
from pydantic import BaseModel

from app.api.errors import to_http_exception
from app.services.document_service import document_service
from app.services.exceptions import RAGIndexError
from app.services.rag_service import rag_service

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentUploadResult(BaseModel):
    id: str
    original_filename: str
    extension: str
    size_bytes: int
    character_count: int
    upload_path: str
    parsed_path: str
    chunk_count: int


@router.post(
    "/upload",
    response_model=DocumentUploadResult,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    file: Annotated[
        UploadFile,
        File(description="TXT, PDF, or DOCX document to upload and parse."),
    ],
) -> DocumentUploadResult:
    try:
        saved_file = await document_service.save_upload_file(file)
        parsed_file = await document_service.parse_saved_file(saved_file)
    except ValueError as exc:
        raise to_http_exception(exc) from exc

    try:
        indexed_document = await rag_service.index_document(parsed_file)
    except RAGIndexError as exc:
        raise to_http_exception(exc) from exc

    return DocumentUploadResult(
        id=saved_file.id,
        original_filename=saved_file.original_filename,
        extension=saved_file.extension,
        size_bytes=saved_file.size_bytes,
        character_count=parsed_file.character_count,
        upload_path=saved_file.upload_path,
        parsed_path=parsed_file.parsed_path,
        chunk_count=indexed_document.chunk_count,
    )
