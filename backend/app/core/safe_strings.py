import re
import unicodedata
from collections.abc import Iterable

MAX_SAFE_BASENAME_CHARS = 255
WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:(?:$|[\\/]|[^\s])")
HOME_PATH_PATTERN = re.compile(r"^~(?:[A-Za-z0-9._-]+)?[\\/]")
FILE_URI_PATTERN = re.compile(r"^file:", re.IGNORECASE)
RELATIVE_PATH_PREFIXES = ("./", ".\\", "../", "..\\")
PATH_ONLY_VALUES = {".", "..", "~"}
PATH_WRAPPERS = {"'", '"', "`"}


def validate_safe_basename(value: str, *, field_name: str = "filename") -> str:
    if any(
        unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in value
    ):
        raise ValueError(f"{field_name} must not contain control characters")
    filename = value.strip()
    if not filename:
        raise ValueError(f"{field_name} cannot be empty")
    if filename.startswith("."):
        raise ValueError(f"hidden {field_name}s are not allowed")
    if "/" in filename or "\\" in filename:
        raise ValueError(f"{field_name} must be a safe basename")
    return filename


def looks_like_local_path(value: str) -> bool:
    candidate = value.strip()
    if (
        len(candidate) >= 2
        and candidate[0] == candidate[-1]
        and candidate[0] in PATH_WRAPPERS
    ):
        candidate = candidate[1:-1].strip()

    if candidate in PATH_ONLY_VALUES:
        return True
    if candidate.startswith(("/", "\\", *RELATIVE_PATH_PREFIXES)):
        return True
    if HOME_PATH_PATTERN.match(candidate):
        return True
    if WINDOWS_DRIVE_PATH_PATTERN.match(candidate):
        return True
    return FILE_URI_PATTERN.match(candidate) is not None


def select_safe_basename(
    candidates: Iterable[str],
    *,
    field_name: str = "filename",
    max_length: int = MAX_SAFE_BASENAME_CHARS,
    fallback: str = "document.txt",
) -> str:
    for candidate in candidates:
        try:
            filename = validate_safe_basename(candidate, field_name=field_name)
        except ValueError:
            continue
        if len(filename) <= max_length and not looks_like_local_path(filename):
            return filename
    return fallback
