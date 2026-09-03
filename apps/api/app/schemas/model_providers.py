from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ProviderType = Literal["openai", "google", "anthropic", "openrouter", "xai", "openai_compatible"]
ApiKeyStatus = Literal["missing", "set"]
TestStatus = Literal["untested", "success", "failed"]
ModelFamily = Literal["gemini", "gpt", "claude", "grok", "openrouter", "local", "custom"]
ModelSource = Literal["fetched", "manual", "preset"]


class ProviderAccountCreate(BaseModel):
    provider_type: ProviderType
    alias: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    base_url: str | None = None
    api_key: str | None = Field(default=None, min_length=1)
    preset: str | None = None


class ProviderAccountUpdate(BaseModel):
    alias: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None
    base_url: str | None = None
    api_key: str | None = Field(default=None, min_length=1)
    clear_api_key: bool = False
    preset: str | None = None


class ProviderAccountRead(BaseModel):
    id: str
    provider_type: ProviderType
    alias: str
    enabled: bool
    configured: bool
    base_url: str | None = None
    api_key_status: ApiKeyStatus
    api_key_hint: str | None = None
    preset: str | None = None
    last_test_status: TestStatus | None = None
    last_test_message: str | None = None
    last_test_at: datetime | None = None
    active_model_count: int = 0


class ProviderAccountTestRead(BaseModel):
    provider_account_id: str
    status: Literal["success", "failed"]
    message: str
    tested_at: datetime


class ModelOptionManualCreate(BaseModel):
    provider_account_id: str
    model: str = Field(min_length=1)
    label: str | None = None
    enabled: bool = True
    supports_chat: bool = True
    supports_compression: bool = True
    supports_tts: bool = False
    model_family: ModelFamily = "custom"
    supports_json: bool = True
    context_window_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)


class ModelOptionUpdate(BaseModel):
    label: str | None = None
    enabled: bool | None = None
    supports_chat: bool | None = None
    supports_compression: bool | None = None
    supports_tts: bool | None = None
    model_family: ModelFamily | None = None
    supports_json: bool | None = None
    context_window_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)


class ModelOptionRead(BaseModel):
    id: str
    key: str
    provider_account_id: str
    provider_type: ProviderType
    provider_account_alias: str
    model: str
    label: str
    display_label: str
    enabled: bool
    supports_chat: bool
    supports_compression: bool
    supports_tts: bool
    model_family: ModelFamily
    supports_json: bool
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    source: ModelSource
    last_test_status: TestStatus | None = None
    last_test_message: str | None = None
    last_test_at: datetime | None = None


class ModelOptionTestRead(BaseModel):
    model_option_id: str
    status: Literal["success", "failed"]
    message: str
    tested_at: datetime
