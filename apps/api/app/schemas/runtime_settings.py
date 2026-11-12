from typing import Literal

from app.schemas.input_policy import RequestModel
from app.schemas.model_providers import ModelInputCapabilities
from pydantic import BaseModel, Field


class RuntimeModelOptionRead(ModelInputCapabilities):
    key: str
    label: str
    provider: str
    model: str
    provider_account_id: str | None = None
    provider_account_alias: str | None = None
    description: str | None = None


class ResponseLengthPresetRead(BaseModel):
    key: str
    label: str
    description: str
    min_reply_bubbles: int
    max_reply_bubbles: int
    max_dialogue_chars: int
    max_action_chars: int
    target_output_tokens: int


class CompressionIntervalOptionRead(BaseModel):
    turns: int
    label: str
    description: str


class RuntimeSettingRead(BaseModel):
    scope: str
    chat_fallback_status: Literal["not_configured", "available", "unavailable"] = "not_configured"
    compression_fallback_status: Literal["not_configured", "available", "unavailable"] = "not_configured"
    conversation_id: str | None = None
    model_key: str | None = None
    fallback_model_key: str | None = None
    compression_model_key: str | None = None
    compression_fallback_model_key: str | None = None
    effective_compression_fallback_model_key: str | None = None
    compression_fallback_source: str = "none"
    compression_strategy: Literal["fast", "quality"] = "quality"
    response_length_preset: str
    default_tts_model_option_key: str | None = None
    decision_enabled: bool = False
    decision_model_option_key: str | None = None
    safety_preset: str = "medium"
    min_output_tokens: int
    compression_interval_turns: int
    options: list[RuntimeModelOptionRead] = Field(default_factory=list)
    compression_options: list[RuntimeModelOptionRead] = Field(default_factory=list)
    tts_options: list[RuntimeModelOptionRead] = Field(default_factory=list)
    decision_options: list[RuntimeModelOptionRead] = Field(default_factory=list)
    response_length_presets: list[ResponseLengthPresetRead] = Field(default_factory=list)
    compression_interval_options: list[CompressionIntervalOptionRead] = Field(default_factory=list)


class RuntimeSettingUpdate(RequestModel):
    nonnull_fields = frozenset(['compression_strategy', 'response_length_preset', 'safety_preset', 'min_output_tokens', 'compression_interval_turns'])
    nonblank_fields = frozenset(['response_length_preset'])
    model_key: str | None = None
    fallback_model_key: str | None = None
    compression_model_key: str | None = None
    compression_fallback_model_key: str | None = None
    compression_strategy: Literal["fast", "quality"] | None = None
    response_length_preset: str | None = None
    default_tts_model_option_key: str | None = None
    decision_enabled: bool | None = None
    decision_model_option_key: str | None = None
    safety_preset: str | None = Field(default=None, pattern="^(high|medium|low)$")
    # Backward-compatible internal knob. The UI should use response_length_preset instead.
    min_output_tokens: int | None = Field(default=None, ge=64, le=4096)
    compression_interval_turns: int | None = Field(default=None, ge=1, le=30)
