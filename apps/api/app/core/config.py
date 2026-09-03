from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/lorechat.db"
    upload_root: str = "./uploads"
    log_dir: str | None = None
    provider_secret_root: str = "./secrets"
    cors_origins: str = "http://localhost:8080,http://127.0.0.1:8080,http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174"

    # Legacy local OpenAI-compatible endpoint. Runtime model selection comes from provider model_options.
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_mock: bool = True
    llm_timeout_seconds: float = 120.0
    # Context pressure is the product default. legacy/shadow remain explicit
    # rollback modes for operators and existing deployments.
    context_management_mode: Literal["legacy", "shadow", "automatic"] = "automatic"

    # Purpose-specific model routing.
    # chat_generation can use Gemini while compression/summary stays on local Gemma.
    chat_llm_provider: str = "openai_compatible"  # openai_compatible | gemini
    chat_llm_base_url: str | None = None
    chat_llm_api_key: str | None = None
    chat_llm_model: str | None = None
    chat_llm_timeout_seconds: float | None = None

    compression_llm_provider: str = "openai_compatible"
    compression_llm_base_url: str | None = None
    compression_llm_api_key: str | None = None
    compression_llm_model: str | None = None
    compression_llm_timeout_seconds: float | None = None

    gemini_api_key: str | None = None
    google_api_key: str | None = None
    gemini_safety_threshold: str = "BLOCK_NONE"

    # Hidden model fallback is disabled for provider/model settings. A selected
    # model failure should be visible so the user can choose another model.
    local_llm_fallback_enabled: bool = False
    local_llm_fallback_provider: str = "gemini"
    local_llm_fallback_model: str | None = None

    # Optional external semantic memory layer. Defaults keep the runtime fully local.
    memory_provider: str = "local"  # local | mem0
    mem0_enabled: bool = False
    mem0_api_key: str | None = None
    mem0_base_url: str | None = None
    mem0_user_id: str = "user_001"
    mem0_app_id: str = "lorechat"
    mem0_workspace: str = "default"
    mem0_search_limit: int = 3
    mem0_write_enabled: bool = False
    mem0_read_enabled: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
