from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.services.document_service import document_service

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentUploadResult(BaseModel):
    id: str
    original_filename: str
    extension: str
    size_bytes: int
    character_count: int
    upload_path: str
    parsed_path: str


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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return DocumentUploadResult(
        id=saved_file.id,
        original_filename=saved_file.original_filename,
        extension=saved_file.extension,
        size_bytes=saved_file.size_bytes,
        character_count=parsed_file.character_count,
        upload_path=saved_file.upload_path,
        parsed_path=parsed_file.parsed_path,
    )
