from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env."""

    app_name: str = "AnythingLLM Mini"
    app_env: str = "dev"
    debug: bool = True

    llm_provider: Literal["deepseek"] = "deepseek"
    model_name: str = "deepseek-v4-flash"
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"

    embedding_provider: Literal["sentence_transformers"] = "sentence_transformers"
    embedding_model_name: str = "intfloat/multilingual-e5-small"

    database_url: str = "sqlite:///./anythingllm_mini.db"

    vector_store: Literal["chroma", "qdrant"] = "chroma"
    chroma_persist_dir: str = "./storage/chroma"
    chroma_collection_name: str = "anythingllm-mini-documents"
    qdrant_path: str = "./storage/qdrant"

    upload_dir: str = "./storage/uploads"
    parsed_dir: str = "./storage/parsed"

    chunk_size: int = 400
    chunk_overlap: int = 60
    top_k: int = 5
    similarity_threshold: float = 0.75

    agent_max_steps: int = 5

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("deepseek_base_url")
    @classmethod
    def normalize_deepseek_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("chunk_size", "top_k")
    @classmethod
    def require_positive_integer(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be greater than zero")
        return value

    @field_validator("chunk_overlap")
    @classmethod
    def validate_chunk_overlap(cls, value: int, info) -> int:
        if value < 0:
            raise ValueError("chunk_overlap cannot be negative")

        # ValidationInfo.data exposes already-validated fields on this model.
        chunk_size = info.data.get("chunk_size")
        # Keep the overlap strictly smaller than the chunk size.
        if chunk_size is not None and value >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return value

    @field_validator("similarity_threshold")
    @classmethod
    def validate_similarity_threshold(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("similarity_threshold must be between 0 and 1")
        return value

    @property
    def deepseek_api_key_value(self) -> str:
        if self.deepseek_api_key is None:
            raise ValueError("DEEPSEEK_API_KEY is required to call DeepSeek.")

        api_key = self.deepseek_api_key.get_secret_value()
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is required to call DeepSeek.")
        return api_key

    def deepseek_client_options(self) -> dict[str, str]:
        return {
            "api_key": self.deepseek_api_key_value,
            "base_url": self.deepseek_base_url,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
