import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from fastapi import UploadFile
from pypdf import PdfReader
from pydantic import BaseModel

from app.core.config import PROJECT_ROOT, settings
from app.core.document_filename import (
    SUPPORTED_DOCUMENT_EXTENSIONS,
    extract_supported_extension,
    normalize_upload_filename,
    validate_document_id,
    validate_display_filename,
)

CHUNK_SIZE_BYTES = 1024 * 1024
PARSED_STORAGE_FILENAME = "content.txt"


class SavedDocumentFile(BaseModel):
    id: str
    display_filename: str
    content_type: str | None
    size_bytes: int
    extension: str


class ParsedDocumentFile(BaseModel):
    id: str
    display_filename: str
    extension: str
    text: str
    character_count: int


class DeletedDocumentFiles(BaseModel):
    upload_file_deleted: bool
    parsed_file_deleted: bool


@dataclass(frozen=True)
class DocumentStoragePaths:
    document_id: str
    extension: str
    upload_file: Path
    parsed_file: Path


class DocumentService:
    def __init__(
        self,
        upload_dir: str | Path | None = None,
        parsed_dir: str | Path | None = None,
    ):
        self.upload_dir = self._resolve_storage_dir(upload_dir or settings.upload_dir)
        self.parsed_dir = self._resolve_storage_dir(parsed_dir or settings.parsed_dir)

    async def save_upload_file(self, file: UploadFile) -> SavedDocumentFile:
        display_filename = normalize_upload_filename(file.filename or "")
        extension = extract_supported_extension(display_filename)

        document_id = uuid4().hex
        paths = self.build_storage_paths(document_id, extension)
        document_dir = paths.upload_file.parent

        document_dir.mkdir(parents=True, exist_ok=False)
        size_bytes = 0

        try:
            with paths.upload_file.open("wb") as output:
                while chunk := await file.read(CHUNK_SIZE_BYTES):
                    size_bytes += len(chunk)
                    output.write(chunk)

            if size_bytes == 0:
                raise ValueError("file cannot be empty")
        except Exception:
            self._cleanup_failed_save(paths.upload_file, document_dir)
            raise

        return SavedDocumentFile(
            id=document_id,
            display_filename=display_filename,
            content_type=file.content_type,
            size_bytes=size_bytes,
            extension=extension,
        )

    async def parse_saved_file(
        self, saved_file: SavedDocumentFile
    ) -> ParsedDocumentFile:
        source_path = self._validate_saved_file(saved_file)

        try:
            text = await asyncio.to_thread(
                self._extract_text, source_path, saved_file.extension
            )
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("failed to parse document") from exc

        text = text.strip()
        if not text:
            raise ValueError("document contains no parseable text")

        paths = self.build_storage_paths(saved_file.id, saved_file.extension)
        await asyncio.to_thread(self._write_parsed_text, paths.parsed_file, text)

        return ParsedDocumentFile(
            id=saved_file.id,
            display_filename=saved_file.display_filename,
            extension=saved_file.extension,
            text=text,
            character_count=len(text),
        )

    async def delete_document_files(
        self,
        deletion_plan: DocumentStoragePaths,
    ) -> DeletedDocumentFiles:
        upload_file_deleted, parsed_file_deleted = await asyncio.to_thread(
            self._delete_document_files,
            deletion_plan,
        )
        return DeletedDocumentFiles(
            upload_file_deleted=upload_file_deleted,
            parsed_file_deleted=parsed_file_deleted,
        )

    async def build_document_file_deletion_plan(
        self,
        document_id: str,
        extension: str,
    ) -> DocumentStoragePaths:
        return await asyncio.to_thread(
            self._validated_storage_paths, document_id, extension
        )

    async def validate_document_file_paths(
        self,
        document_id: str,
        extension: str,
    ) -> None:
        await self.build_document_file_deletion_plan(document_id, extension)

    async def read_parsed_text(
        self,
        document_id: str,
        *,
        expected_character_count: int,
    ) -> str:
        try:
            parsed_file = self._validated_storage_paths(
                document_id,
                ".txt",
                validate_upload=False,
            ).parsed_file
            if not parsed_file.is_file():
                raise ValueError("parsed document does not exist")
            content = await asyncio.to_thread(parsed_file.read_text, encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("parsed document must be UTF-8 encoded") from exc
        except (OSError, RuntimeError) as exc:
            raise ValueError("parsed document could not be read") from exc
        content = content.strip()
        if not content:
            raise ValueError("parsed document content is empty")
        if len(content) != expected_character_count:
            raise ValueError("parsed document character count does not match metadata")
        return content

    def _resolve_storage_dir(self, storage_dir: str | Path) -> Path:
        path = Path(storage_dir)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    def build_storage_paths(
        self,
        document_id: str,
        extension: str,
    ) -> DocumentStoragePaths:
        validate_document_id(document_id)
        if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
            raise ValueError(f"unsupported file extension: {extension or 'none'}")
        upload_file = self.upload_dir / document_id / f"source{extension}"
        parsed_file = self.parsed_dir / document_id / PARSED_STORAGE_FILENAME
        return DocumentStoragePaths(
            document_id=document_id,
            extension=extension,
            upload_file=upload_file,
            parsed_file=parsed_file,
        )

    def _validate_saved_file(self, saved_file: SavedDocumentFile) -> Path:
        validate_display_filename(saved_file.display_filename)
        if (
            extract_supported_extension(saved_file.display_filename)
            != saved_file.extension
        ):
            raise ValueError("file extension does not match saved metadata")
        paths = self._validated_storage_paths(saved_file.id, saved_file.extension)
        if not paths.upload_file.is_file():
            raise ValueError("uploaded file does not exist")
        return paths.upload_file

    def _extract_text(self, source_path: Path, extension: str) -> str:
        if extension == ".txt":
            return self._extract_txt_text(source_path)
        if extension == ".pdf":
            return self._extract_pdf_text(source_path)
        if extension == ".docx":
            return self._extract_docx_text(source_path)
        raise ValueError(f"unsupported file extension: {extension or 'none'}")

    def _extract_txt_text(self, source_path: Path) -> str:
        try:
            return source_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("text file must be UTF-8 encoded") from exc

    def _extract_pdf_text(self, source_path: Path) -> str:
        reader = PdfReader(source_path)
        pages = []
        for page in reader.pages:
            page_text = (page.extract_text() or "").strip()
            if page_text:
                pages.append(page_text)
        return "\n\n".join(pages)

    def _extract_docx_text(self, source_path: Path) -> str:
        document = DocxDocument(source_path)
        blocks = []

        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                paragraph_text = block.text.strip()
                if paragraph_text:
                    blocks.append(paragraph_text)
                continue

            if isinstance(block, Table):
                for row in block.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    if any(cells):
                        blocks.append("\t".join(cells))

        return "\n".join(blocks)

    def _write_parsed_text(self, parsed_path: Path, text: str) -> None:
        parsed_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = parsed_path.with_name(f".{parsed_path.name}.{uuid4().hex}.tmp")

        try:
            temporary_path.write_text(text, encoding="utf-8")
            temporary_path.replace(parsed_path)
        except Exception:
            if temporary_path.exists():
                temporary_path.unlink()
            self._remove_empty_dir(parsed_path.parent)
            raise

    def _delete_document_files(
        self,
        deletion_plan: DocumentStoragePaths,
    ) -> tuple[bool, bool]:
        upload_file_deleted = self._unlink_if_exists(deletion_plan.upload_file)
        parsed_file_deleted = self._unlink_if_exists(deletion_plan.parsed_file)
        self._remove_empty_dir(self.upload_dir / deletion_plan.document_id)
        self._remove_empty_dir(self.parsed_dir / deletion_plan.document_id)
        return upload_file_deleted, parsed_file_deleted

    def _validated_storage_paths(
        self,
        document_id: str,
        extension: str,
        *,
        validate_upload: bool = True,
    ) -> DocumentStoragePaths:
        paths = self.build_storage_paths(document_id, extension)
        upload_file = self._validate_storage_file(
            paths.upload_file,
            self.upload_dir,
            document_id,
            "upload",
            validate_type=validate_upload,
        )
        parsed_file = self._validate_storage_file(
            paths.parsed_file,
            self.parsed_dir,
            document_id,
            "parsed",
            validate_type=True,
        )
        return DocumentStoragePaths(
            document_id=document_id,
            extension=extension,
            upload_file=upload_file,
            parsed_file=parsed_file,
        )

    def _validate_storage_file(
        self,
        expected_file: Path,
        storage_dir: Path,
        document_id: str,
        path_label: str,
        *,
        validate_type: bool,
    ) -> Path:
        storage_root = storage_dir.resolve()
        document_dir = (storage_dir / document_id).resolve()
        path = expected_file.resolve()
        if (
            not document_dir.is_relative_to(storage_root)
            or path == document_dir
            or not path.is_relative_to(document_dir)
        ):
            raise ValueError(f"invalid {path_label} path")
        if validate_type and path.exists() and not path.is_file():
            raise ValueError(f"{path_label} path is not a file")
        return path

    def _unlink_if_exists(self, path: Path) -> bool:
        if not path.exists():
            return False
        if not path.is_file():
            raise ValueError("document path is not a file")
        path.unlink()
        return True

    def _remove_empty_dir(self, path: Path) -> None:
        try:
            path.rmdir()
        except OSError:
            pass

    def _cleanup_failed_save(self, upload_path: Path, document_dir: Path) -> None:
        if upload_path.exists():
            upload_path.unlink()

        self._remove_empty_dir(document_dir)


document_service = DocumentService()
