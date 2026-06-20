import asyncio
from io import BytesIO
from pathlib import Path

import pytest

from app.services.document_service import DocumentService


class FakeUploadFile:
    def __init__(
        self,
        filename: str,
        content: bytes,
        content_type: str = "text/plain",
    ):
        self.filename = filename
        self.content_type = content_type
        self._file = BytesIO(content)

    async def read(self, size: int = -1) -> bytes:
        return self._file.read(size)


def save_file(service: DocumentService, file: FakeUploadFile):
    return asyncio.run(service.save_upload_file(file))


def test_save_txt_file(tmp_path):
    service = DocumentService(upload_dir=tmp_path)
    file = FakeUploadFile("hello.txt", b"hello world")

    result = save_file(service, file)

    assert result.original_filename == "hello.txt"
    assert result.stored_filename == "hello.txt"
    assert result.extension == ".txt"
    assert result.size_bytes == len(b"hello world")
    assert result.content_type == "text/plain"
    assert Path(result.upload_path).read_bytes() == b"hello world"


def test_saved_file_exists_under_document_id_directory(tmp_path):
    service = DocumentService(upload_dir=tmp_path)
    file = FakeUploadFile("notes.txt", b"notes")

    result = save_file(service, file)
    upload_path = Path(result.upload_path)

    assert upload_path.exists()
    assert upload_path.parent == tmp_path / result.id
    assert upload_path.name == "notes.txt"


def test_filename_is_sanitized(tmp_path):
    service = DocumentService(upload_dir=tmp_path)
    file = FakeUploadFile("../unsafe path/..\\my report!!.txt", b"safe")

    result = save_file(service, file)

    assert result.original_filename == "../unsafe path/..\\my report!!.txt"
    assert result.stored_filename == "my_report_.txt"
    assert "/" not in result.stored_filename
    assert "\\" not in result.stored_filename
    assert ".." not in result.stored_filename
    assert Path(result.upload_path).read_bytes() == b"safe"


def test_unsupported_extension_is_rejected(tmp_path):
    service = DocumentService(upload_dir=tmp_path)
    file = FakeUploadFile("image.png", b"not supported", "image/png")

    with pytest.raises(ValueError, match="unsupported file extension"):
        save_file(service, file)

    assert list(tmp_path.iterdir()) == []


def test_empty_file_is_rejected_and_cleaned_up(tmp_path):
    service = DocumentService(upload_dir=tmp_path)
    file = FakeUploadFile("empty.txt", b"")

    with pytest.raises(ValueError, match="file cannot be empty"):
        save_file(service, file)

    assert list(tmp_path.iterdir()) == []
