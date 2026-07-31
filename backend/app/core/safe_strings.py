import re

WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:(?:$|[\\/]|[^\s])")
HOME_PATH_PATTERN = re.compile(r"^~(?:[A-Za-z0-9._-]+)?[\\/]")
FILE_URI_PATTERN = re.compile(r"^file:", re.IGNORECASE)
RELATIVE_PATH_PREFIXES = ("./", ".\\", "../", "..\\")
PATH_ONLY_VALUES = {".", "..", "~"}
PATH_WRAPPERS = {"'", '"', "`"}


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
