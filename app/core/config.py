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

    embedding_provider: str = "sentence_transformers"
    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"

    database_url: str = "sqlite:///./anythingllm_mini.db"

    vector_store: Literal["chroma", "qdrant"] = "chroma"
    chroma_persist_dir: str = "./storage/chroma"
    qdrant_path: str = "./storage/qdrant"

    upload_dir: str = "./storage/uploads"
    parsed_dir: str = "./storage/parsed"

    chunk_size: int = 800
    chunk_overlap: int = 100
    top_k: int = 5

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
