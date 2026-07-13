import json
import re

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

MAX_TOOL_OUTPUT_COUNT = 20
MAX_TOOL_OUTPUT_JSON_CHARS = 8_000
OUTPUT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:(?:$|[\\/]|[^\s])")
HOME_PATH_PATTERN = re.compile(r"^~(?:[A-Za-z0-9._-]+)?[\\/]")
FILE_URI_PATTERN = re.compile(r"^file:", re.IGNORECASE)
RELATIVE_PATH_PREFIXES = ("./", ".\\", "../", "..\\")
PATH_ONLY_VALUES = {".", "..", "~"}
PATH_WRAPPERS = {"'", '"', "`"}
CAMEL_CASE_BOUNDARY_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
PATH_KEY_TOKEN_PATTERN = re.compile(r"[^a-z0-9]+")
PATH_KEY_TOKENS = {
    "path",
    "paths",
    "pathname",
    "pathnames",
    "filepath",
    "filepaths",
}


class ToolSourceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=255)
    original_filename: str = Field(min_length=1, max_length=255)
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)

    @field_validator("original_filename")
    @classmethod
    def validate_original_filename(cls, value: str) -> str:
        filename = value.strip()
        if not filename:
            raise ValueError("original_filename cannot be empty")
        if filename.startswith("."):
            raise ValueError("hidden filenames are not allowed in tool artifacts")
        if "/" in filename or "\\" in filename or "\x00" in filename:
            raise ValueError("original_filename must be a safe basename")
        return filename


class ToolArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[ToolSourceArtifact] = Field(default_factory=list)
    outputs: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("outputs")
    @classmethod
    def validate_outputs(
        cls,
        outputs: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if len(outputs) > MAX_TOOL_OUTPUT_COUNT:
            raise ValueError(
                f"tool artifacts support at most {MAX_TOOL_OUTPUT_COUNT} outputs"
            )

        for name, value in outputs.items():
            if not OUTPUT_NAME_PATTERN.fullmatch(name):
                raise ValueError(
                    "tool output names must be 1-64 character snake_case identifiers"
                )
            _reject_path_key(name)
            _reject_local_paths(value)

        try:
            serialized = json.dumps(
                outputs,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("tool outputs must be JSON-safe") from exc
        if len(serialized) > MAX_TOOL_OUTPUT_JSON_CHARS:
            raise ValueError(
                "serialized tool outputs cannot exceed "
                f"{MAX_TOOL_OUTPUT_JSON_CHARS} characters"
            )
        return outputs


def _reject_local_paths(value: JsonValue) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            _reject_path_key(str(key))
            _reject_local_paths(nested_value)
        return
    if isinstance(value, list):
        for item in value:
            _reject_local_paths(item)
        return
    if not isinstance(value, str):
        return

    if _looks_like_local_path(value):
        raise ValueError("local paths are not allowed in tool outputs")


def _looks_like_local_path(value: str) -> bool:
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


def _reject_path_key(key: str) -> None:
    expanded = CAMEL_CASE_BOUNDARY_PATTERN.sub("_", key.strip())
    tokens = [
        token for token in PATH_KEY_TOKEN_PATTERN.split(expanded.casefold()) if token
    ]
    if any(
        token in PATH_KEY_TOKENS or token.endswith(("path", "paths"))
        for token in tokens
    ):
        raise ValueError("path fields are not allowed in tool outputs")
