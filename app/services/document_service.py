import asyncio
import re
from pathlib import Path
from uuid import uuid4

from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from fastapi import UploadFile
from pypdf import PdfReader
from pydantic import BaseModel

from app.core.config import PROJECT_ROOT, settings

SUPPORTED_EXTENSIONS = {".txt", ".pdf", ".docx"}
CHUNK_SIZE_BYTES = 1024 * 1024


class SavedDocumentFile(BaseModel):
    id: str
    original_filename: str
    stored_filename: str
    content_type: str | None
    size_bytes: int
    upload_path: str
    extension: str


class ParsedDocumentFile(BaseModel):
    id: str
    original_filename: str
    stored_filename: str
    extension: str
    text: str
    character_count: int
    parsed_path: str


class DeletedDocumentFiles(BaseModel):
    upload_path: str
    parsed_path: str
    upload_file_deleted: bool
    parsed_file_deleted: bool


class DocumentService:
    def __init__(
        self,
        upload_dir: str | Path | None = None,
        parsed_dir: str | Path | None = None,
    ):
        self.upload_dir = self._resolve_storage_dir(upload_dir or settings.upload_dir)
        self.parsed_dir = self._resolve_storage_dir(parsed_dir or settings.parsed_dir)

    async def save_upload_file(self, file: UploadFile) -> SavedDocumentFile:
        original_filename = file.filename or ""
        # Normalize the uploaded name before using it on disk.
        stored_filename = self._safe_filename(original_filename)
        extension = Path(stored_filename).suffix.lower()
        # Reject file types we do not support yet.
        if extension not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported file extension: {extension or 'none'}")

        document_id = uuid4().hex
        document_dir = self.upload_dir / document_id
        upload_path = document_dir / stored_filename

        # Create a dedicated folder for this upload.
        document_dir.mkdir(parents=True, exist_ok=False)
        size_bytes = 0

        try:
            # Stream the file to disk in fixed-size chunks.
            with upload_path.open("wb") as output:
                # := is the "walrus operator" introduced in Python 3.8, which allows assignment and evaluation in a single expression.
                while chunk := await file.read(CHUNK_SIZE_BYTES):
                    size_bytes += len(chunk)
                    output.write(chunk)

            # Empty uploads are treated as invalid input.
            if size_bytes == 0:
                raise ValueError("file cannot be empty")
        except Exception:
            # Remove partial files if any step fails.
            self._cleanup_failed_save(upload_path, document_dir)
            raise

        return SavedDocumentFile(
            id=document_id,
            original_filename=original_filename,
            stored_filename=stored_filename,
            content_type=file.content_type,
            size_bytes=size_bytes,
            upload_path=str(upload_path),
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
            raise ValueError(
                f"failed to parse document: {saved_file.original_filename}"
            ) from exc

        text = text.strip()
        if not text:
            raise ValueError(
                f"document contains no parseable text: "
                f"{saved_file.original_filename}"
            )

        parsed_document_dir = self.parsed_dir / saved_file.id
        parsed_filename = f"{Path(saved_file.stored_filename).stem}.txt"
        parsed_path = parsed_document_dir / parsed_filename

        await asyncio.to_thread(self._write_parsed_text, parsed_path, text)

        return ParsedDocumentFile(
            id=saved_file.id,
            original_filename=saved_file.original_filename,
            stored_filename=saved_file.stored_filename,
            extension=saved_file.extension,
            text=text,
            character_count=len(text),
            parsed_path=str(parsed_path),
        )

    async def delete_document_files(
        self,
        document_id: str,
        upload_path: str,
        parsed_path: str,
    ) -> DeletedDocumentFiles:
        upload_file_deleted, parsed_file_deleted = await asyncio.to_thread(
            self._delete_document_files,
            document_id,
            upload_path,
            parsed_path,
        )
        return DeletedDocumentFiles(
            upload_path=upload_path,
            parsed_path=parsed_path,
            upload_file_deleted=upload_file_deleted,
            parsed_file_deleted=parsed_file_deleted,
        )

    async def validate_document_file_paths(
        self,
        document_id: str,
        upload_path: str,
        parsed_path: str,
    ) -> None:
        await asyncio.to_thread(
            self._validated_delete_paths,
            document_id,
            upload_path,
            parsed_path,
        )

    def _resolve_storage_dir(self, storage_dir: str | Path) -> Path:
        path = Path(storage_dir)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    def _validate_saved_file(self, saved_file: SavedDocumentFile) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", saved_file.id):
            raise ValueError("invalid document id")

        if saved_file.extension not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"unsupported file extension: " f"{saved_file.extension or 'none'}"
            )

        if (
            self._safe_filename(saved_file.stored_filename)
            != saved_file.stored_filename
        ):
            raise ValueError("invalid stored filename")

        source_path = Path(saved_file.upload_path).resolve()
        expected_path = (
            self.upload_dir / saved_file.id / saved_file.stored_filename
        ).resolve()

        if source_path != expected_path or not source_path.is_relative_to(
            self.upload_dir
        ):
            raise ValueError("invalid upload path")

        if not source_path.is_file():
            raise ValueError("uploaded file does not exist")

        source_extension = source_path.suffix.lower()
        if source_extension != saved_file.extension:
            raise ValueError("file extension does not match saved metadata")

        return source_path

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
            try:
                parsed_path.parent.rmdir()
            except OSError:
                pass
            raise

    def _delete_document_files(
        self,
        document_id: str,
        upload_path: str,
        parsed_path: str,
    ) -> tuple[bool, bool]:
        upload_file, parsed_file = self._validated_delete_paths(
            document_id,
            upload_path,
            parsed_path,
        )

        upload_file_deleted = self._unlink_if_exists(upload_file)
        parsed_file_deleted = self._unlink_if_exists(parsed_file)
        self._remove_empty_dir(self.upload_dir / document_id)
        self._remove_empty_dir(self.parsed_dir / document_id)
        return upload_file_deleted, parsed_file_deleted

    def _validated_delete_paths(
        self,
        document_id: str,
        upload_path: str,
        parsed_path: str,
    ) -> tuple[Path, Path]:
        if not re.fullmatch(r"[0-9a-f]{32}", document_id):
            raise ValueError("invalid document id")

        upload_file = self._validate_delete_path(
            document_id,
            upload_path,
            self.upload_dir,
            "upload",
        )
        parsed_file = self._validate_delete_path(
            document_id,
            parsed_path,
            self.parsed_dir,
            "parsed",
        )
        return upload_file, parsed_file

    def _validate_delete_path(
        self,
        document_id: str,
        file_path: str,
        storage_dir: Path,
        path_label: str,
    ) -> Path:
        path = Path(file_path).resolve()
        document_dir = (storage_dir / document_id).resolve()
        if path == document_dir or not path.is_relative_to(document_dir):
            raise ValueError(f"invalid {path_label} path")
        if path.exists() and not path.is_file():
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

    def _safe_filename(self, filename: str) -> str:
        # Keep only the basename so path separators cannot escape the upload dir.
        basename = filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1].strip()
        if not basename or basename in {".", ".."}:
            raise ValueError("filename is required")

        # Replace uncommon characters with underscores to keep the name filesystem-safe.
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", basename)
        safe_name = safe_name.strip("._")
        if not safe_name:
            raise ValueError("filename is required")
        return safe_name

    def _cleanup_failed_save(self, upload_path: Path, document_dir: Path) -> None:
        if upload_path.exists():
            upload_path.unlink()

        try:
            document_dir.rmdir()
        except OSError:
            pass


document_service = DocumentService()
