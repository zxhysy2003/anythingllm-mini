import re
import unicodedata
from pathlib import PurePosixPath

from app.core.safe_strings import looks_like_local_path

MAX_DISPLAY_FILENAME_CHARS = 255
SUPPORTED_DOCUMENT_EXTENSIONS = frozenset({".txt", ".pdf", ".docx"})
DOCUMENT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def normalize_upload_filename(raw_filename: str) -> str:
    if not isinstance(raw_filename, str):
        raise ValueError("filename is required")
    basename = raw_filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    normalized_basename = unicodedata.normalize("NFC", basename)
    _reject_prohibited_filename_characters(normalized_basename)
    display_filename = normalized_basename.strip()
    validate_display_filename(display_filename)
    extract_supported_extension(display_filename)
    return display_filename


def validate_display_filename(display_filename: str) -> str:
    if not isinstance(display_filename, str):
        raise ValueError("display filename must be a string")
    _reject_prohibited_filename_characters(display_filename)
    if unicodedata.normalize("NFC", display_filename) != display_filename:
        raise ValueError("display filename must use Unicode NFC normalization")
    if display_filename != display_filename.strip():
        raise ValueError("display filename must not have surrounding whitespace")
    if not display_filename or display_filename in {".", ".."}:
        raise ValueError("display filename is required")
    if display_filename.startswith("."):
        raise ValueError("hidden display filenames are not allowed")
    if "/" in display_filename or "\\" in display_filename:
        raise ValueError("display filename must be a basename")
    if looks_like_local_path(display_filename):
        raise ValueError("display filename must not look like a local path")
    if len(display_filename) > MAX_DISPLAY_FILENAME_CHARS:
        raise ValueError(
            f"display filename cannot exceed {MAX_DISPLAY_FILENAME_CHARS} characters"
        )
    return display_filename


def extract_supported_extension(display_filename: str) -> str:
    validate_display_filename(display_filename)
    extension = PurePosixPath(display_filename).suffix.casefold()
    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
        raise ValueError(f"unsupported file extension: {extension or 'none'}")
    return extension


def validate_document_id(document_id: str) -> str:
    if not DOCUMENT_ID_PATTERN.fullmatch(document_id):
        raise ValueError("invalid document id")
    return document_id


def _reject_prohibited_filename_characters(value: str) -> None:
    if any(
        unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in value
    ):
        raise ValueError("display filename must not contain control characters")
