import re
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
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


class DocumentService:
    def __init__(self, upload_dir: str | Path | None = None):
        self.upload_dir = self._resolve_upload_dir(upload_dir or settings.upload_dir)

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

    def _resolve_upload_dir(self, upload_dir: str | Path) -> Path:
        path = Path(upload_dir)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

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
