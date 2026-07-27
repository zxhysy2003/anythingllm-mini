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


def build_deletion_plan(
    service: DocumentService,
    document_id: str,
    upload_path: Path,
    parsed_path: Path,
):
    return asyncio.run(
        service.build_document_file_deletion_plan(
            document_id,
            str(upload_path),
            str(parsed_path),
        )
    )


def delete_files(service: DocumentService, deletion_plan):
    return asyncio.run(service.delete_document_files(deletion_plan))


def read_parsed_text(
    service: DocumentService,
    document_id: str,
    parsed_path: Path,
    expected_character_count: int,
):
    return asyncio.run(
        service.read_parsed_text(
            document_id,
            str(parsed_path),
            expected_character_count=expected_character_count,
        )
    )


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


def test_build_document_file_deletion_plan_returns_safe_paths(tmp_path):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    document_id = "a" * 32
    upload_path = service.upload_dir / document_id / "guide.txt"
    parsed_path = service.parsed_dir / document_id / "guide.txt"
    upload_path.parent.mkdir(parents=True)
    parsed_path.parent.mkdir(parents=True)
    upload_path.write_text("upload", encoding="utf-8")
    parsed_path.write_text("parsed", encoding="utf-8")

    plan = build_deletion_plan(service, document_id, upload_path, parsed_path)

    assert plan.document_id == document_id
    assert plan.upload_file == upload_path.resolve()
    assert plan.parsed_file == parsed_path.resolve()
    assert plan.upload_path == str(upload_path)
    assert plan.parsed_path == str(parsed_path)


def test_read_parsed_text_validates_scope_and_character_count(tmp_path):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    document_id = "a" * 32
    parsed_path = service.parsed_dir / document_id / "guide.txt"
    parsed_path.parent.mkdir(parents=True)
    parsed_path.write_text("parsed document", encoding="utf-8")

    assert read_parsed_text(service, document_id, parsed_path, 15) == (
        "parsed document"
    )

    with pytest.raises(ValueError, match="character count"):
        read_parsed_text(service, document_id, parsed_path, 14)


def test_read_parsed_text_rejects_outside_and_missing_files(tmp_path):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    document_id = "b" * 32
    outside_path = tmp_path / "outside.txt"
    outside_path.write_text("outside", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid parsed path"):
        read_parsed_text(service, document_id, outside_path, 7)

    missing_path = service.parsed_dir / document_id / "missing.txt"
    with pytest.raises(ValueError, match="does not exist"):
        read_parsed_text(service, document_id, missing_path, 7)


def test_read_parsed_text_sanitizes_file_io_errors(tmp_path, monkeypatch):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    document_id = "c" * 32
    parsed_path = service.parsed_dir / document_id / "guide.txt"
    parsed_path.parent.mkdir(parents=True)
    parsed_path.write_text("parsed", encoding="utf-8")

    def fail_read(path, *args, **kwargs):
        raise PermissionError(13, "permission denied", str(path))

    monkeypatch.setattr(Path, "read_text", fail_read)

    with pytest.raises(ValueError, match="could not be read") as exc_info:
        read_parsed_text(service, document_id, parsed_path, 6)

    assert str(parsed_path) not in str(exc_info.value)


@pytest.mark.parametrize(
    "validation_error",
    [
        PermissionError(13, "permission denied", "/private/parsed/guide.txt"),
        RuntimeError("symlink loop at /private/parsed/guide.txt"),
    ],
)
def test_read_parsed_text_sanitizes_path_validation_errors(
    tmp_path,
    monkeypatch,
    validation_error,
):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )

    def fail_validation(*args, **kwargs):
        raise validation_error

    monkeypatch.setattr(service, "_validate_parsed_path", fail_validation)

    with pytest.raises(ValueError, match="could not be read") as exc_info:
        read_parsed_text(
            service,
            "d" * 32,
            service.parsed_dir / ("d" * 32) / "guide.txt",
            6,
        )

    assert "/private/parsed/guide.txt" not in str(exc_info.value)


def test_build_document_file_deletion_plan_rejects_unsafe_paths(tmp_path):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    document_id = "b" * 32
    outside_path = tmp_path / "outside.txt"
    parsed_path = service.parsed_dir / document_id / "guide.txt"
    outside_path.write_text("outside", encoding="utf-8")
    parsed_path.parent.mkdir(parents=True)
    parsed_path.write_text("parsed", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid upload path"):
        build_deletion_plan(service, document_id, outside_path, parsed_path)

    with pytest.raises(ValueError, match="invalid document id"):
        build_deletion_plan(service, "not-a-document-id", outside_path, parsed_path)


def test_build_document_file_deletion_plan_rejects_directory_paths(tmp_path):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    document_id = "c" * 32
    upload_path = service.upload_dir / document_id / "not-a-file"
    parsed_path = service.parsed_dir / document_id / "guide.txt"
    upload_path.mkdir(parents=True)
    parsed_path.parent.mkdir(parents=True)
    parsed_path.write_text("parsed", encoding="utf-8")

    with pytest.raises(ValueError, match="upload path is not a file"):
        build_deletion_plan(service, document_id, upload_path, parsed_path)


def test_delete_document_files_uses_existing_deletion_plan(tmp_path, monkeypatch):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    document_id = "d" * 32
    upload_path = service.upload_dir / document_id / "guide.txt"
    parsed_path = service.parsed_dir / document_id / "guide.txt"
    upload_path.parent.mkdir(parents=True)
    parsed_path.parent.mkdir(parents=True)
    upload_path.write_text("upload", encoding="utf-8")
    parsed_path.write_text("parsed", encoding="utf-8")
    plan = build_deletion_plan(service, document_id, upload_path, parsed_path)

    def fail_if_revalidated(*args):
        raise AssertionError("delete_document_files should use the deletion plan")

    monkeypatch.setattr(service, "_validated_delete_paths", fail_if_revalidated)
    result = delete_files(service, plan)

    assert result.upload_path == str(upload_path)
    assert result.parsed_path == str(parsed_path)
    assert result.upload_file_deleted is True
    assert result.parsed_file_deleted is True
    assert not upload_path.exists()
    assert not parsed_path.exists()
