from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.db.models import RuntimeSetting
from app.schemas.runtime_settings import CompressionIntervalOptionRead, ResponseLengthPresetRead, RuntimeModelOptionRead, RuntimeSettingRead, RuntimeSettingUpdate
from app.services import model_provider_service, provider_secret_service

GLOBAL_RUNTIME_SETTING_ID = "global"
DEFAULT_MODEL_KEY = None
DEFAULT_FALLBACK_MODEL_KEY = None
DEFAULT_COMPRESSION_MODEL_KEY = None
DEFAULT_COMPRESSION_STRATEGY = "quality"
DEFAULT_RESPONSE_LENGTH_PRESET = "medium"
DEFAULT_COMPRESSION_INTERVAL_TURNS = 5
MIN_OUTPUT_TOKEN_LOW = 64
MIN_OUTPUT_TOKEN_HIGH = 4096


@dataclass(frozen=True)
class ResponseLengthPreset:
    key: str
    label: str
    description: str
    min_reply_bubbles: int
    max_reply_bubbles: int
    max_dialogue_chars: int
    max_action_chars: int
    target_output_tokens: int


@dataclass(frozen=True)
class CompressionIntervalOption:
    turns: int
    label: str
    description: str


RESPONSE_LENGTH_PRESETS: tuple[ResponseLengthPreset, ...] = (
    ResponseLengthPreset(
        key="short",
        label="짧게",
        description="비용 절약/빠른 티키타카. 보통 1~2버블, 대사도 짧게.",
        min_reply_bubbles=1,
        max_reply_bubbles=2,
        max_dialogue_chars=180,
        max_action_chars=90,
        target_output_tokens=320,
    ),
    ResponseLengthPreset(
        key="medium",
        label="중간",
        description="기본 역할놀이 밸런스. 감정선은 살리되 과출력 방지. 대사 중심 2~3버블.",
        min_reply_bubbles=2,
        max_reply_bubbles=3,
        max_dialogue_chars=360,
        max_action_chars=140,
        target_output_tokens=768,
    ),
    ResponseLengthPreset(
        key="long",
        label="긴대화",
        description="중요 장면/도장깨기용. 액션/속마음 뻥튀기보다 버블을 더 나눠 긴 감정선을 허용.",
        min_reply_bubbles=3,
        max_reply_bubbles=6,
        max_dialogue_chars=620,
        max_action_chars=160,
        target_output_tokens=1280,
    ),
)

COMPRESSION_INTERVAL_OPTIONS: tuple[CompressionIntervalOption, ...] = (
    CompressionIntervalOption(turns=2, label="2턴마다 · 강한 기억 유지", description="새 방/관계 초반처럼 첫 상황과 말맛을 자주 고정해야 할 때"),
    CompressionIntervalOption(turns=3, label="3턴마다 · 자주/안전", description="중요 장면, 리그 초반, 관계 변화가 잦은 방"),
    CompressionIntervalOption(turns=5, label="5턴마다 · 기본 추천", description="품질과 비용 균형이 가장 무난한 기본값"),
    CompressionIntervalOption(turns=8, label="8턴마다 · 비용 절약", description="긴 흐름은 유지하되 압축 호출을 줄이고 싶을 때"),
    CompressionIntervalOption(turns=12, label="12턴마다 · 최소 압축", description="테스트나 저비용 장시간 대화용"),
)
DEFAULT_MIN_OUTPUT_TOKENS = next(p.target_output_tokens for p in RESPONSE_LENGTH_PRESETS if p.key == DEFAULT_RESPONSE_LENGTH_PRESET)


def response_length_preset_by_key(key: str | None) -> ResponseLengthPreset:
    requested = key or DEFAULT_RESPONSE_LENGTH_PRESET
    return next((preset for preset in RESPONSE_LENGTH_PRESETS if preset.key == requested), response_length_preset_by_key(DEFAULT_RESPONSE_LENGTH_PRESET) if requested != DEFAULT_RESPONSE_LENGTH_PRESET else RESPONSE_LENGTH_PRESETS[0])


def response_length_preset_reads() -> list[ResponseLengthPresetRead]:
    return [ResponseLengthPresetRead(**preset.__dict__) for preset in RESPONSE_LENGTH_PRESETS]


def compression_interval_option_reads() -> list[CompressionIntervalOptionRead]:
    return [CompressionIntervalOptionRead(**option.__dict__) for option in COMPRESSION_INTERVAL_OPTIONS]


def preset_token_target(key: str | None) -> int:
    return response_length_preset_by_key(key).target_output_tokens


def max_bubbles_for_preset(key: str | None, *, is_multi_room: bool) -> int:
    preset = response_length_preset_by_key(key)
    if is_multi_room:
        return max(2, preset.max_reply_bubbles)
    if preset.key == "long":
        return preset.max_reply_bubbles
    return min(3, preset.max_reply_bubbles)


def min_bubbles_for_preset(key: str | None, *, is_multi_room: bool) -> int:
    preset = response_length_preset_by_key(key)
    maximum = max_bubbles_for_preset(key, is_multi_room=is_multi_room)
    return max(1, min(maximum, preset.min_reply_bubbles))

def clamp_min_output_tokens(value: int | None) -> int:
    if value is None:
        return DEFAULT_MIN_OUTPUT_TOKENS
    return max(MIN_OUTPUT_TOKEN_LOW, min(MIN_OUTPUT_TOKEN_HIGH, int(value)))


def clamp_compression_interval_turns(value: int | None) -> int:
    if value is None:
        return DEFAULT_COMPRESSION_INTERVAL_TURNS
    return max(1, min(30, int(value)))


def _runtime_read_from_model_option(option) -> RuntimeModelOptionRead:
    return RuntimeModelOptionRead(
        key=option.key,
        label=option.display_label,
        provider=option.provider_type,
        model=option.model,
        provider_account_id=option.provider_account_id,
        provider_account_alias=option.provider_account_alias,
        description=None,
    )


def active_option_reads(session: Session, capability: str) -> list[RuntimeModelOptionRead]:
    options = model_provider_service.active_options_for_runtime(session, capability)
    return [_runtime_read_from_model_option(option) for option in options]


def _field_was_set(payload: RuntimeSettingUpdate, field: str) -> bool:
    fields_set = getattr(payload, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(payload, "__fields_set__", set())
    return field in fields_set


def _validate_dynamic_model_option(session: Session, key: str, capability: str):
    option = model_provider_service.option_by_key(session, key)
    if not option:
        return None
    if not model_provider_service.option_is_active_for_capability(session, option, capability):
        raise ValueError(f"Model option '{key}' is not active or does not support {capability}")
    return option


def _validate_runtime_model_key(session: Session | None, key: str, capability: str, settings: Settings | None = None) -> str:
    if not key:
        raise ValueError("Model key is required")
    if session:
        dynamic_option = _validate_dynamic_model_option(session, key, capability)
        if dynamic_option:
            return dynamic_option.key
    raise ValueError(f"Model option '{key}' was not found")


def _validate_tts_model_key(session: Session | None, key: str | None) -> str | None:
    if not key:
        return None
    if not session:
        raise ValueError("TTS model option validation requires a database session")
    dynamic_option = _validate_dynamic_model_option(session, key, "tts")
    if not dynamic_option:
        raise ValueError(f"TTS model option '{key}' was not found")
    return dynamic_option.key


def get_setting(session: Session, setting_id: str) -> RuntimeSetting | None:
    return session.get(RuntimeSetting, setting_id)


def get_global_setting(session: Session) -> RuntimeSetting:
    setting = get_setting(session, GLOBAL_RUNTIME_SETTING_ID)
    if setting:
        return setting
    setting = RuntimeSetting(
        id=GLOBAL_RUNTIME_SETTING_ID,
        conversation_id=None,
        model_key=None,
        fallback_model_key=None,
        compression_model_key=None,
        compression_fallback_model_key=None,
        compression_strategy=DEFAULT_COMPRESSION_STRATEGY,
        response_length_preset=DEFAULT_RESPONSE_LENGTH_PRESET,
        min_output_tokens=DEFAULT_MIN_OUTPUT_TOKENS,
        compression_interval_turns=DEFAULT_COMPRESSION_INTERVAL_TURNS,
        safety_preset="medium",
    )
    session.add(setting)
    session.commit()
    session.refresh(setting)
    return setting


def get_effective_setting(session: Session, conversation_id: str | None = None) -> RuntimeSetting:
    if conversation_id:
        room_setting = get_setting(session, conversation_id)
        if room_setting:
            return room_setting
    return get_global_setting(session)


def serialize_setting(setting: RuntimeSetting, *, scope: str, options: bool = True, session: Session | None = None) -> RuntimeSettingRead:
    chat_options = active_option_reads(session, "chat") if options and session else []
    compression_options = active_option_reads(session, "compression") if options and session else []
    tts_options = active_option_reads(session, "tts") if options and session else []
    returned_chat_options = chat_options
    returned_compression_options = compression_options
    valid_option = next((option for option in returned_chat_options if option.key == setting.model_key), None)
    fallback_option = next((option for option in returned_chat_options if option.key == getattr(setting, "fallback_model_key", None)), None)
    compression_option = next((option for option in returned_compression_options if option.key == getattr(setting, "compression_model_key", None)), None)
    dedicated_compression_fallback_option = next((
        option for option in returned_compression_options
        if option.key == getattr(setting, "compression_fallback_model_key", None)
    ), None)
    inherited_compression_fallback_option = next((
        option for option in returned_compression_options
        if option.key == getattr(setting, "fallback_model_key", None)
    ), None)
    effective_compression_fallback_option = dedicated_compression_fallback_option or inherited_compression_fallback_option
    compression_fallback_source = (
        "dedicated" if dedicated_compression_fallback_option
        else "chat_fallback" if inherited_compression_fallback_option
        else "none"
    )
    preset = response_length_preset_by_key(getattr(setting, "response_length_preset", DEFAULT_RESPONSE_LENGTH_PRESET))
    return RuntimeSettingRead(
        scope=scope,
        conversation_id=setting.conversation_id,
        model_key=valid_option.key if valid_option else None,
        fallback_model_key=fallback_option.key if fallback_option else None,
        compression_model_key=compression_option.key if compression_option else None,
        compression_fallback_model_key=dedicated_compression_fallback_option.key if dedicated_compression_fallback_option else None,
        effective_compression_fallback_model_key=effective_compression_fallback_option.key if effective_compression_fallback_option else None,
        compression_fallback_source=compression_fallback_source,
        compression_strategy=(
            getattr(setting, "compression_strategy", DEFAULT_COMPRESSION_STRATEGY)
            if getattr(setting, "compression_strategy", DEFAULT_COMPRESSION_STRATEGY) in {"fast", "quality"}
            else DEFAULT_COMPRESSION_STRATEGY
        ),
        response_length_preset=preset.key,
        default_tts_model_option_key=getattr(setting, "default_tts_model_option_key", None),
        safety_preset=getattr(setting, "safety_preset", "medium") or "medium",
        min_output_tokens=preset.target_output_tokens,
        compression_interval_turns=clamp_compression_interval_turns(getattr(setting, "compression_interval_turns", DEFAULT_COMPRESSION_INTERVAL_TURNS)),
        options=returned_chat_options if options else [],
        compression_options=returned_compression_options if options else [],
        tts_options=tts_options if options else [],
        response_length_presets=response_length_preset_reads() if options else [],
        compression_interval_options=compression_interval_option_reads() if options else [],
    )


def update_global_setting(session: Session, payload: RuntimeSettingUpdate) -> RuntimeSetting:
    setting = get_global_setting(session)
    apply_update(setting, payload, session=session)
    session.add(setting)
    session.commit()
    session.refresh(setting)
    return setting


def update_conversation_setting(session: Session, conversation_id: str, payload: RuntimeSettingUpdate) -> RuntimeSetting:
    setting = get_setting(session, conversation_id)
    if not setting:
        global_setting = get_global_setting(session)
        setting = RuntimeSetting(
            id=conversation_id,
            conversation_id=conversation_id,
            model_key=global_setting.model_key,
            fallback_model_key=getattr(global_setting, "fallback_model_key", None),
            compression_model_key=getattr(global_setting, "compression_model_key", None),
            compression_fallback_model_key=getattr(global_setting, "compression_fallback_model_key", None),
            compression_strategy=getattr(global_setting, "compression_strategy", DEFAULT_COMPRESSION_STRATEGY) or DEFAULT_COMPRESSION_STRATEGY,
            response_length_preset=getattr(global_setting, "response_length_preset", DEFAULT_RESPONSE_LENGTH_PRESET),
            min_output_tokens=global_setting.min_output_tokens,
            compression_interval_turns=clamp_compression_interval_turns(getattr(global_setting, "compression_interval_turns", DEFAULT_COMPRESSION_INTERVAL_TURNS)),
            default_tts_model_option_key=getattr(global_setting, "default_tts_model_option_key", None),
            safety_preset=getattr(global_setting, "safety_preset", "medium") or "medium",
        )
    apply_update(setting, payload, session=session)
    session.add(setting)
    session.commit()
    session.refresh(setting)
    return setting


def apply_update(setting: RuntimeSetting, payload: RuntimeSettingUpdate, *, session: Session | None = None) -> None:
    if payload.model_key is not None:
        setting.model_key = _validate_runtime_model_key(session, payload.model_key, "chat")
    if _field_was_set(payload, "fallback_model_key"):
        setting.fallback_model_key = (
            _validate_runtime_model_key(session, payload.fallback_model_key, "chat")
            if payload.fallback_model_key
            else None
        )
    if setting.fallback_model_key and setting.fallback_model_key == setting.model_key:
        raise ValueError("Fallback model must differ from the primary chat model")
    if payload.compression_model_key is not None:
        setting.compression_model_key = _validate_runtime_model_key(session, payload.compression_model_key, "compression")
    if _field_was_set(payload, "compression_fallback_model_key"):
        setting.compression_fallback_model_key = (
            _validate_runtime_model_key(session, payload.compression_fallback_model_key, "compression")
            if payload.compression_fallback_model_key
            else None
        )
    effective_compression_fallback_key = (
        getattr(setting, "compression_fallback_model_key", None)
        or getattr(setting, "fallback_model_key", None)
    )
    if effective_compression_fallback_key and effective_compression_fallback_key == setting.compression_model_key:
        raise ValueError("Compression fallback model must differ from the primary compression model")
    if payload.compression_strategy is not None:
        setting.compression_strategy = payload.compression_strategy
    elif getattr(setting, "compression_strategy", None) not in {"fast", "quality"}:
        setting.compression_strategy = DEFAULT_COMPRESSION_STRATEGY
    if payload.response_length_preset is not None:
        preset = response_length_preset_by_key(payload.response_length_preset)
        setting.response_length_preset = preset.key
        setting.min_output_tokens = preset.target_output_tokens
    elif payload.min_output_tokens is not None:
        # Backward compatibility for old clients/tests. Map exact preset token targets when possible.
        target = clamp_min_output_tokens(payload.min_output_tokens)
        matched = next((preset for preset in RESPONSE_LENGTH_PRESETS if preset.target_output_tokens == target), None)
        setting.response_length_preset = matched.key if matched else getattr(setting, "response_length_preset", DEFAULT_RESPONSE_LENGTH_PRESET)
        setting.min_output_tokens = target
    if payload.compression_interval_turns is not None:
        setting.compression_interval_turns = clamp_compression_interval_turns(payload.compression_interval_turns)
    elif not getattr(setting, "compression_interval_turns", None):
        setting.compression_interval_turns = DEFAULT_COMPRESSION_INTERVAL_TURNS
    if _field_was_set(payload, "default_tts_model_option_key"):
        setting.default_tts_model_option_key = _validate_tts_model_key(session, payload.default_tts_model_option_key)
    if payload.safety_preset is not None:
        setting.safety_preset = payload.safety_preset
    setting.updated_at = datetime.now(timezone.utc)


def _provider_for_llm_client(provider_type: str) -> str:
    if provider_type == "google":
        return "gemini"
    return provider_type


def _dynamic_llm_overrides(session: Session, model_key: str | None, capability: str) -> dict | None:
    option = model_provider_service.option_by_key(session, model_key)
    if not option:
        return None
    if not model_provider_service.option_is_active_for_capability(session, option, capability):
        raise ValueError(f"Model option '{model_key}' is not active or does not support {capability}")
    account = model_provider_service.account_by_id(session, option.provider_account_id)
    api_key = provider_secret_service.get_secret(account.api_key_secret_ref)
    overrides = {
        "provider": _provider_for_llm_client(account.provider_type),
        "provider_type": account.provider_type,
        "provider_account_id": account.id,
        "model_option_key": option.key,
        "model": option.model,
    }
    if account.base_url:
        overrides["base_url"] = account.base_url
    if api_key:
        overrides["api_key"] = api_key
        if account.provider_type == "google":
            overrides["gemini_api_key"] = api_key
    return overrides


def llm_overrides_for_setting(setting: RuntimeSetting, settings: Settings | None = None, session: Session | None = None) -> dict:
    if session:
        dynamic_overrides = _dynamic_llm_overrides(session, setting.model_key, "chat")
        if dynamic_overrides:
            return dynamic_overrides
    if (settings or get_settings()).llm_mock:
        return {"provider": "mock", "model": "mock"}
    raise ValueError("No active chat model option is selected")


def fallback_llm_overrides_for_setting(setting: RuntimeSetting, session: Session | None = None) -> dict | None:
    model_key = getattr(setting, "fallback_model_key", None)
    if not model_key:
        return None
    if not session:
        raise ValueError("Fallback model option validation requires a database session")
    dynamic_overrides = _dynamic_llm_overrides(session, model_key, "chat")
    if dynamic_overrides:
        return dynamic_overrides
    raise ValueError("Selected fallback chat model is not active")


def compression_fallback_llm_overrides_for_setting(
    setting: RuntimeSetting,
    session: Session | None = None,
) -> dict | None:
    dedicated_key = getattr(setting, "compression_fallback_model_key", None)
    inherited_key = getattr(setting, "fallback_model_key", None)
    model_key = dedicated_key or inherited_key
    if not model_key or model_key == getattr(setting, "compression_model_key", None):
        return None
    if not session:
        raise ValueError("Compression fallback model option validation requires a database session")
    option = model_provider_service.option_by_key(session, model_key)
    if not option or not model_provider_service.option_is_active_for_capability(session, option, "compression"):
        if dedicated_key:
            raise ValueError("Selected compression fallback model is not active")
        return None
    return _dynamic_llm_overrides(session, model_key, "compression")


def compression_llm_overrides_for_setting(setting: RuntimeSetting, settings: Settings | None = None, session: Session | None = None) -> dict:
    model_key = getattr(setting, "compression_model_key", None)
    if session:
        dynamic_overrides = _dynamic_llm_overrides(session, model_key, "compression")
        if dynamic_overrides:
            return dynamic_overrides
    if (settings or get_settings()).llm_mock:
        return {"provider": "mock", "model": "mock"}
    raise ValueError("No active compression model option is selected")
