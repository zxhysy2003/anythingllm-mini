from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import documents as documents_api
from app.main import app
from app.services.document_service import DocumentService

client = TestClient(app)


@pytest.fixture
def temp_document_service(tmp_path, monkeypatch):
    service = DocumentService(
        upload_dir=tmp_path / "uploads",
        parsed_dir=tmp_path / "parsed",
    )
    monkeypatch.setattr(documents_api, "document_service", service)
    return service


def test_upload_document_saves_and_parses_txt(temp_document_service):
    response = client.post(
        "/documents/upload",
        files={"file": ("notes.txt", b"hello\nworld", "text/plain")},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["original_filename"] == "notes.txt"
    assert result["extension"] == ".txt"
    assert result["size_bytes"] == len(b"hello\nworld")
    assert result["character_count"] == len("hello\nworld")
    assert "text" not in result
    assert Path(result["upload_path"]).read_bytes() == b"hello\nworld"
    assert Path(result["parsed_path"]).read_text(encoding="utf-8") == "hello\nworld"


def test_upload_document_rejects_unsupported_extension(temp_document_service):
    response = client.post(
        "/documents/upload",
        files={"file": ("image.png", b"not supported", "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unsupported file extension: .png"
    assert not any(temp_document_service.upload_dir.rglob("*"))


def test_upload_document_rejects_empty_file(temp_document_service):
    response = client.post(
        "/documents/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "file cannot be empty"
    assert not any(temp_document_service.upload_dir.rglob("*"))


def test_upload_document_preserves_corrupt_source_file(temp_document_service):
    response = client.post(
        "/documents/upload",
        files={
            "file": (
                "broken.docx",
                b"not a docx archive",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "failed to parse document: broken.docx"
    uploaded_files = [
        path for path in temp_document_service.upload_dir.rglob("*") if path.is_file()
    ]
    assert len(uploaded_files) == 1
    assert uploaded_files[0].read_bytes() == b"not a docx archive"
    assert not temp_document_service.parsed_dir.exists()


def test_upload_document_requires_multipart_file():
    response = client.post("/documents/upload")

    assert response.status_code == 422


def test_upload_document_openapi_schema_uses_multipart():
    schema = app.openapi()
    upload_operation = schema["paths"]["/documents/upload"]["post"]

    assert "multipart/form-data" in upload_operation["requestBody"]["content"]
    assert "201" in upload_operation["responses"]
