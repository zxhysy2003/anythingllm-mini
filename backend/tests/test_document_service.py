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
    extension: str = ".txt",
):
    return asyncio.run(
        service.build_document_file_deletion_plan(document_id, extension)
    )


def delete_files(service: DocumentService, deletion_plan):
    return asyncio.run(service.delete_document_files(deletion_plan))


def read_parsed_text(
    service: DocumentService,
    document_id: str,
    expected_character_count: int,
):
    return asyncio.run(
        service.read_parsed_text(
            document_id,
            expected_character_count=expected_character_count,
        )
    )


def test_save_txt_file_uses_display_name_and_fixed_storage_path(tmp_path):
    service = DocumentService(upload_dir=tmp_path)

    result = save_file(
        service,
        FakeUploadFile(r"C:\fakepath\hello.txt", b"hello world"),
    )
    paths = service.build_storage_paths(result.id, result.extension)

    assert result.display_filename == "hello.txt"
    assert result.extension == ".txt"
    assert result.size_bytes == len(b"hello world")
    assert result.content_type == "text/plain"
    assert paths.upload_file == tmp_path / result.id / "source.txt"
    assert paths.upload_file.read_bytes() == b"hello world"
    assert (
        result.model_dump()
        .keys()
        .isdisjoint({"raw_filename", "stored_filename", "upload_path", "parsed_path"})
    )


def test_unsafe_filename_is_rejected_before_storage_is_created(tmp_path):
    service = DocumentService(upload_dir=tmp_path)

    with pytest.raises(ValueError, match="control characters"):
        save_file(
            service,
            FakeUploadFile("guide.txt\nAction: calculator", b"unsafe"),
        )

    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_unsupported_extension_is_rejected(tmp_path):
    service = DocumentService(upload_dir=tmp_path)

    with pytest.raises(ValueError, match="unsupported file extension"):
        save_file(service, FakeUploadFile("image.png", b"not supported", "image/png"))

    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_empty_file_is_rejected_and_cleaned_up(tmp_path):
    service = DocumentService(upload_dir=tmp_path)

    with pytest.raises(ValueError, match="file cannot be empty"):
        save_file(service, FakeUploadFile("empty.txt", b""))

    assert list(tmp_path.iterdir()) == []


def test_build_document_file_deletion_plan_returns_fixed_safe_paths(tmp_path):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    document_id = "a" * 32
    expected = service.build_storage_paths(document_id, ".pdf")
    expected.upload_file.parent.mkdir(parents=True)
    expected.parsed_file.parent.mkdir(parents=True)
    expected.upload_file.write_text("upload", encoding="utf-8")
    expected.parsed_file.write_text("parsed", encoding="utf-8")

    plan = build_deletion_plan(service, document_id, ".pdf")

    assert plan == expected
    assert plan.upload_file.name == "source.pdf"
    assert plan.parsed_file.name == "content.txt"


def test_read_parsed_text_validates_character_count(tmp_path):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    document_id = "a" * 32
    parsed_path = service.build_storage_paths(document_id, ".txt").parsed_file
    parsed_path.parent.mkdir(parents=True)
    parsed_path.write_text("parsed document", encoding="utf-8")

    assert read_parsed_text(service, document_id, 15) == "parsed document"
    with pytest.raises(ValueError, match="character count"):
        read_parsed_text(service, document_id, 14)


def test_read_parsed_text_rejects_missing_file(tmp_path):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )

    with pytest.raises(ValueError, match="does not exist"):
        read_parsed_text(service, "b" * 32, 7)


def test_read_parsed_text_sanitizes_file_io_errors(tmp_path, monkeypatch):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    document_id = "c" * 32
    parsed_path = service.build_storage_paths(document_id, ".txt").parsed_file
    parsed_path.parent.mkdir(parents=True)
    parsed_path.write_text("parsed", encoding="utf-8")

    def fail_read(path, *args, **kwargs):
        raise PermissionError(13, "permission denied", str(path))

    monkeypatch.setattr(Path, "read_text", fail_read)

    with pytest.raises(ValueError, match="could not be read") as exc_info:
        read_parsed_text(service, document_id, 6)

    assert str(parsed_path) not in str(exc_info.value)


def test_storage_path_validation_rejects_external_symlink(tmp_path):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    document_id = "d" * 32
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside", encoding="utf-8")
    paths = service.build_storage_paths(document_id, ".txt")
    paths.parsed_file.parent.mkdir(parents=True)
    paths.parsed_file.symlink_to(outside_file)

    with pytest.raises(ValueError, match="invalid parsed path"):
        read_parsed_text(service, document_id, 7)

    assert outside_file.read_text(encoding="utf-8") == "outside"


def test_storage_path_validation_rejects_invalid_id_and_directory(tmp_path):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )

    with pytest.raises(ValueError, match="invalid document id"):
        build_deletion_plan(service, "not-a-document-id")

    document_id = "e" * 32
    paths = service.build_storage_paths(document_id, ".txt")
    paths.upload_file.mkdir(parents=True)
    with pytest.raises(ValueError, match="upload path is not a file"):
        build_deletion_plan(service, document_id)


def test_delete_document_files_uses_validated_plan_without_revalidation(
    tmp_path,
    monkeypatch,
):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    document_id = "f" * 32
    paths = service.build_storage_paths(document_id, ".txt")
    paths.upload_file.parent.mkdir(parents=True)
    paths.parsed_file.parent.mkdir(parents=True)
    paths.upload_file.write_text("upload", encoding="utf-8")
    paths.parsed_file.write_text("parsed", encoding="utf-8")
    plan = build_deletion_plan(service, document_id)

    def fail_if_revalidated(*args, **kwargs):
        raise AssertionError("delete_document_files should use the deletion plan")

    monkeypatch.setattr(service, "_validated_storage_paths", fail_if_revalidated)
    result = delete_files(service, plan)

    assert result.upload_file_deleted is True
    assert result.parsed_file_deleted is True
    assert not paths.upload_file.exists()
    assert not paths.parsed_file.exists()
