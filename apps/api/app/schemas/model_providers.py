from datetime import datetime
from typing import Literal

from app.schemas.input_policy import RequestModel
from pydantic import BaseModel, Field, field_validator

ProviderType = Literal["openai", "google", "anthropic", "openrouter", "xai", "openai_compatible"]
ApiKeyStatus = Literal["missing", "set"]
TestStatus = Literal["untested", "success", "failed"]
ModelFamily = Literal["gemini", "gpt", "claude", "grok", "openrouter", "local", "custom"]
ModelSource = Literal["fetched", "manual", "preset"]


class ProviderAccountCreate(RequestModel):
    provider_type: ProviderType
    alias: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    base_url: str | None = None
    api_key: str | None = Field(default=None, min_length=1)
    preset: str | None = None


class ProviderAccountUpdate(RequestModel):
    nonnull_fields = frozenset(['alias', 'enabled'])
    nonblank_fields = frozenset(['alias'])
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


CapabilitySource = Literal["provider_metadata", "official_registry", "manual", "unknown"]


class ModelInputCapabilities(BaseModel):
    input_modalities: list[str] | None = Field(default=None, max_length=16)
    image_mime_types: list[str] | None = Field(default=None, max_length=16)
    capability_source: CapabilitySource = "unknown"

    @field_validator("input_modalities", "image_mime_types", mode="before")
    @classmethod
    def normalize_capabilities(cls, value, info):
        if value is None:
            return None
        allowed = ({"text", "image", "audio", "video", "file"} if info.field_name == "input_modalities"
                   else {"image/png", "image/jpeg", "image/webp", "image/gif"})
        if not isinstance(value, list) or len(value) > 16:
            raise ValueError("Capabilities must be a bounded list")
        result = []
        for item in value:
            if not isinstance(item, str) or len(item) > 32 or item.strip().lower() not in allowed:
                raise ValueError("Unsupported capability value")
            item = item.strip().lower()
            if item not in result:
                result.append(item)
        return result


class ModelOptionManualCreate(RequestModel, ModelInputCapabilities):
    provider_account_id: str
    model: str = Field(min_length=1)
    label: str | None = None
    enabled: bool = True
    supports_chat: bool = True
    supports_compression: bool = True
    supports_tts: bool = False
    supports_decision: bool = False
    model_family: ModelFamily = "custom"
    supports_json: bool = True
    context_window_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)


class ModelOptionUpdate(RequestModel, ModelInputCapabilities):
    nonnull_fields = frozenset(['label', 'enabled', 'supports_chat', 'supports_compression', 'supports_tts', 'model_family', 'supports_json'])
    nonblank_fields = frozenset(['label'])
    label: str | None = None
    enabled: bool | None = None
    supports_chat: bool | None = None
    supports_compression: bool | None = None
    supports_tts: bool | None = None
    supports_decision: bool | None = None
    model_family: ModelFamily | None = None
    supports_json: bool | None = None
    context_window_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)


class ModelOptionRead(ModelInputCapabilities):
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
    supports_decision: bool
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
