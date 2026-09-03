from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlmodel import Session, select

from app.db.models import ModelOption, ModelProviderAccount, utc_now
from app.schemas.model_providers import (
    ModelOptionManualCreate,
    ModelOptionRead,
    ModelOptionTestRead,
    ModelOptionUpdate,
    ProviderAccountCreate,
    ProviderAccountRead,
    ProviderAccountTestRead,
    ProviderAccountUpdate,
)
from app.services import provider_secret_service

PROVIDER_TYPES = {"openai", "google", "anthropic", "openrouter", "xai", "openai_compatible"}
XAI_BASE_URL = "https://api.x.ai/v1"
GOOGLE_EXCLUDED_MODEL_PATTERNS = (
    "image",
    "imagen",
    "veo",
    "lyria",
    "embedding",
    "embed",
    "nano-banana",
    "banana",
    "robotics",
    "computer-use",
    "deep-research",
    "antigravity",
    "aqa",
    "audio generation",
    "video generation",
)
OPENAI_EXCLUDED_MODEL_PATTERNS = (
    "dall-e",
    "gpt-image",
    "image",
    "whisper",
    "transcription",
    "transcribe",
    "embedding",
    "text-embedding",
    "moderation",
    "omni-moderation",
    "realtime",
)
XAI_EXCLUDED_MODEL_PATTERNS = (
    "imagine",
    "image",
    "video",
    "audio",
    "voice",
    "speech",
    "tts",
    "transcription",
)
OPENAI_COMPATIBLE_ALLOWED_LLM_PATTERNS = (
    "gemma",
    "llama",
    "mistral",
    "qwen",
    "phi",
    "yi",
    "deepseek",
    "mixtral",
    "command",
    "solar",
    "exaone",
    "kanana",
)
OPENAI_COMPATIBLE_EXCLUDED_MODEL_PATTERNS = (
    "embedding",
    "embed",
    "rerank",
    "whisper",
    "tts",
    "audio",
    "image",
    "vision-only",
    "clip",
    "stable-diffusion",
    "flux",
    "sdxl",
    "moderation",
)
OPENROUTER_EXCLUDED_MODEL_PATTERNS = ("moderation", "safety")


@dataclass(frozen=True)
class ProviderModel:
    model: str
    label: str
    family: str
    supports_chat: bool = True
    supports_compression: bool = True
    supports_tts: bool = False
    supports_json: bool = True
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized or "model"


def _model_key(account: ModelProviderAccount, model: str) -> str:
    return f"{_slug(account.alias)}_{_slug(account.provider_type)}_{_slug(model)}"


def _model_id(value: str) -> str:
    return value.removeprefix("models/").strip()


def _metadata_text(model: str, metadata: dict | None = None, *extra: str | None) -> str:
    values = [model, *(value or "" for value in extra)]
    if metadata:
        for key in ("displayName", "display_name", "name", "description", "id"):
            value = metadata.get(key)
            if isinstance(value, str):
                values.append(value)
    return " ".join(values).lower()


def _contains_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in value for pattern in patterns)


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer():
        converted = int(value)
        return converted if converted > 0 else None
    if isinstance(value, str):
        normalized = value.strip().replace("_", "")
        if normalized.isdigit():
            converted = int(normalized)
            return converted if converted > 0 else None
    return None


def _first_positive_int(metadata: dict, keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = _positive_int(metadata.get(key))
        if value is not None:
            return value
    return None


def _context_metadata(metadata: dict | None) -> tuple[int | None, int | None]:
    metadata = metadata or {}
    context_window_tokens = _first_positive_int(
        metadata,
        (
            "inputTokenLimit",
            "input_token_limit",
            "context_length",
            "context_window",
            "context_window_tokens",
            "max_context_length",
        ),
    )
    max_output_tokens = _first_positive_int(
        metadata,
        (
            "outputTokenLimit",
            "output_token_limit",
            "max_output_tokens",
            "max_completion_tokens",
        ),
    )
    top_provider = metadata.get("top_provider")
    if max_output_tokens is None and isinstance(top_provider, dict):
        max_output_tokens = _first_positive_int(
            top_provider,
            ("max_completion_tokens", "max_output_tokens"),
        )
    return context_window_tokens, max_output_tokens


def _family_for_provider(provider_type: str, model: str) -> str:
    lowered = model.lower()
    if provider_type == "google":
        return "gemini"
    if provider_type == "openai":
        return "gpt" if lowered.startswith(("gpt", "o", "chatgpt")) else "custom"
    if provider_type == "anthropic":
        return "claude"
    if provider_type == "xai":
        return "grok"
    if provider_type == "openrouter":
        return "openrouter"
    if provider_type == "openai_compatible":
        return "local" if any(token in lowered for token in ("gemma", "llama", "mistral", "qwen", "phi")) else "custom"
    return "custom"


def _capabilities_for_model(provider_type: str, model: str, metadata: dict | None = None) -> tuple[bool, bool, bool, bool]:
    metadata = metadata or {}
    lowered = model.lower()
    if provider_type == "google":
        methods = set(metadata.get("supportedGenerationMethods") or metadata.get("supported_generation_methods") or [])
        text = _metadata_text(model, metadata)
        is_tts = "tts" in text or "text-to-speech" in text
        if is_tts:
            return False, False, True, True
        excluded = _contains_any(text, GOOGLE_EXCLUDED_MODEL_PATTERNS)
        supports_chat = "generateContent" in methods and not excluded
        return supports_chat, supports_chat, is_tts, True
    if provider_type == "openai":
        is_tts = lowered.startswith("tts-") or "tts" in lowered or lowered.startswith(("gpt-4o-mini-tts", "audio"))
        if is_tts:
            return False, False, True, True
        excluded = _contains_any(lowered, OPENAI_EXCLUDED_MODEL_PATTERNS)
        supports_chat = lowered.startswith(("gpt-", "o", "chatgpt-")) and not excluded
        return supports_chat, supports_chat, is_tts, True
    if provider_type == "anthropic":
        supports_chat = lowered.startswith("claude-")
        return supports_chat, supports_chat, False, True
    if provider_type == "xai":
        supports_chat = lowered.startswith("grok-") and not _contains_any(lowered, XAI_EXCLUDED_MODEL_PATTERNS)
        return supports_chat, supports_chat, False, supports_chat
    if provider_type == "openrouter":
        architecture = metadata.get("architecture") or {}
        output_modalities = architecture.get("output_modalities")
        if isinstance(output_modalities, list):
            supports_chat = "text" in {str(item).lower() for item in output_modalities}
        else:
            modality = str(architecture.get("modality") or "").lower()
            supports_chat = modality.endswith("->text") or "->text" in modality or not modality
        supports_chat = supports_chat and not _contains_any(lowered, OPENROUTER_EXCLUDED_MODEL_PATTERNS)
        return supports_chat, supports_chat, False, True
    if provider_type == "openai_compatible":
        excluded = _contains_any(lowered, OPENAI_COMPATIBLE_EXCLUDED_MODEL_PATTERNS)
        allowed = _contains_any(lowered, OPENAI_COMPATIBLE_ALLOWED_LLM_PATTERNS)
        supports_chat = allowed and not excluded
        return supports_chat, supports_chat, False, True
    return False, False, False, True


def _provider_model(model: str, label: str | None, provider_type: str, metadata: dict | None = None) -> ProviderModel:
    supports_chat, supports_compression, supports_tts, supports_json = _capabilities_for_model(provider_type, model, metadata)
    context_window_tokens, max_output_tokens = _context_metadata(metadata)
    return ProviderModel(
        model=model,
        label=label or model,
        family=_family_for_provider(provider_type, model),
        supports_chat=supports_chat,
        supports_compression=supports_compression,
        supports_tts=supports_tts,
        supports_json=supports_json,
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
    )


def fetch_provider_models(account: ModelProviderAccount) -> list[ProviderModel]:
    api_key = provider_secret_service.get_secret(account.api_key_secret_ref)
    timeout = 30.0
    try:
        if account.provider_type == "google":
            if not api_key:
                raise ValueError("Google provider API key is required")
            models: list[ProviderModel] = []
            page_token: str | None = None
            seen_page_tokens: set[str] = set()
            for _ in range(100):
                params = {"key": api_key, "pageSize": 1000}
                if page_token:
                    params["pageToken"] = page_token
                response = httpx.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params=params,
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = response.json()
                models.extend(
                    _provider_model(_model_id(item.get("name", "")), item.get("displayName"), "google", item)
                    for item in payload.get("models", [])
                    if item.get("name")
                )
                next_page_token = str(payload.get("nextPageToken") or "").strip()
                if not next_page_token:
                    return models
                if next_page_token in seen_page_tokens:
                    raise ValueError("Google provider model list returned a repeated page token")
                seen_page_tokens.add(next_page_token)
                page_token = next_page_token
            raise ValueError("Google provider model list exceeded the pagination limit")
        if account.provider_type == "openai":
            if not api_key:
                raise ValueError("OpenAI provider API key is required")
            response = httpx.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
            response.raise_for_status()
            return [_provider_model(item.get("id", ""), item.get("id"), "openai", item) for item in response.json().get("data", []) if item.get("id")]
        if account.provider_type == "anthropic":
            if not api_key:
                raise ValueError("Anthropic provider API key is required")
            models = []
            after_id: str | None = None
            seen_after_ids: set[str] = set()
            for _ in range(100):
                params: dict[str, str | int] = {"limit": 100}
                if after_id:
                    params["after_id"] = after_id
                response = httpx.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                    params=params,
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = response.json()
                page = payload.get("data", [])
                models.extend(
                    _provider_model(item.get("id", ""), item.get("display_name") or item.get("id"), "anthropic", item)
                    for item in page
                    if item.get("id")
                )
                if not payload.get("has_more"):
                    return models
                next_after_id = str(payload.get("last_id") or (page[-1].get("id") if page else "") or "").strip()
                if not next_after_id or next_after_id in seen_after_ids:
                    raise ValueError("Anthropic provider model list returned an invalid page boundary")
                seen_after_ids.add(next_after_id)
                after_id = next_after_id
            raise ValueError("Anthropic provider model list exceeded the pagination limit")
        if account.provider_type == "openrouter":
            response = httpx.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else None,
                timeout=timeout,
            )
            response.raise_for_status()
            return [_provider_model(item.get("id", ""), item.get("name") or item.get("id"), "openrouter", item) for item in response.json().get("data", []) if item.get("id")]
        if account.provider_type == "xai":
            if not api_key:
                raise ValueError("xAI provider API key is required")
            base_url = (account.base_url or XAI_BASE_URL).rstrip("/")
            response = httpx.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
            )
            response.raise_for_status()
            return [_provider_model(item.get("id", ""), item.get("id"), "xai", item) for item in response.json().get("data", []) if item.get("id")]
        if account.provider_type == "openai_compatible":
            if not account.base_url:
                raise ValueError("OpenAI Compatible provider base_url is required")
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            response = httpx.get(f"{account.base_url.rstrip('/')}/models", headers=headers, timeout=timeout)
            response.raise_for_status()
            return [_provider_model(item.get("id", ""), item.get("id"), "openai_compatible", item) for item in response.json().get("data", []) if item.get("id")]
    except httpx.HTTPError as error:
        raise ValueError(f"Provider model list request failed: {type(error).__name__}") from error
    raise ValueError("Unsupported provider type")


def _configured(account: ModelProviderAccount) -> bool:
    if not account.enabled:
        return False
    if account.provider_type == "openai_compatible":
        return bool(account.base_url) and (bool(account.api_key_secret_ref) or account.preset in {"local_gemma", "ollama", "lm_studio", "vllm", "custom"})
    if account.provider_type == "xai":
        return bool(account.base_url) and bool(account.api_key_secret_ref)
    return bool(account.api_key_secret_ref)


def _account_by_id(session: Session, account_id: str) -> ModelProviderAccount:
    account = session.get(ModelProviderAccount, account_id)
    if not account or account.deleted_at:
        raise ValueError("Provider account not found")
    return account


def account_by_id(session: Session, account_id: str) -> ModelProviderAccount:
    return _account_by_id(session, account_id)


def _option_by_id(session: Session, option_id: str) -> ModelOption:
    option = session.get(ModelOption, option_id)
    if not option or option.deleted_at:
        raise ValueError("Model option not found")
    return option


def list_accounts(session: Session) -> list[ProviderAccountRead]:
    accounts = session.exec(select(ModelProviderAccount).where(ModelProviderAccount.deleted_at.is_(None))).all()
    options = session.exec(select(ModelOption).where(ModelOption.deleted_at.is_(None), ModelOption.enabled == True)).all()  # noqa: E712
    counts: dict[str, int] = {}
    for option in options:
        counts[option.provider_account_id] = counts.get(option.provider_account_id, 0) + 1
    return [serialize_account(account, active_model_count=counts.get(account.id, 0)) for account in accounts]


def create_account(session: Session, payload: ProviderAccountCreate) -> ModelProviderAccount:
    account = ModelProviderAccount(
        id=_id("provider_account"),
        provider_type=payload.provider_type,
        alias=payload.alias.strip(),
        enabled=payload.enabled,
        base_url=payload.base_url or (XAI_BASE_URL if payload.provider_type == "xai" else None),
        preset=payload.preset,
    )
    if payload.api_key:
        account.api_key_secret_ref, account.api_key_hint = provider_secret_service.save_secret(payload.api_key)
    account.configured = _configured(account)
    session.add(account)
    session.commit()
    session.refresh(account)
    should_auto_sync = account.configured and (account.provider_type != "openai_compatible" or bool(payload.api_key))
    if should_auto_sync:
        sync_models(session, account.id)
        session.refresh(account)
    return account


def update_account(session: Session, account_id: str, payload: ProviderAccountUpdate) -> ModelProviderAccount:
    account = _account_by_id(session, account_id)
    if payload.clear_api_key and payload.api_key:
        raise ValueError("api_key and clear_api_key cannot be used together")
    if payload.alias is not None:
        account.alias = payload.alias.strip()
    if payload.enabled is not None:
        account.enabled = payload.enabled
    if payload.base_url is not None:
        account.base_url = payload.base_url
    if payload.preset is not None:
        account.preset = payload.preset
    if payload.clear_api_key:
        provider_secret_service.delete_secret(account.api_key_secret_ref)
        account.api_key_secret_ref = None
        account.api_key_hint = None
        disabled_at = _now()
        options = session.exec(
            select(ModelOption).where(
                ModelOption.provider_account_id == account.id,
                ModelOption.deleted_at.is_(None),
            )
        ).all()
        for option in options:
            option.enabled = False
            option.updated_at = disabled_at
            session.add(option)
    elif payload.api_key:
        account.api_key_secret_ref, account.api_key_hint = provider_secret_service.update_secret(account.api_key_secret_ref, payload.api_key)
    account.configured = False if payload.clear_api_key else _configured(account)
    account.updated_at = _now()
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def test_account(session: Session, account_id: str) -> ProviderAccountTestRead:
    account = _account_by_id(session, account_id)
    account.configured = _configured(account)
    ok = account.configured
    if ok:
        message = "연결 정보가 설정되었습니다. 실제 provider 호출 테스트는 adapter 연동 단계에서 수행됩니다."
        status = "success"
    elif account.provider_type == "openai_compatible" and not account.base_url:
        message = "OpenAI Compatible provider에는 Base URL이 필요합니다."
        status = "failed"
    else:
        message = "API key 또는 필수 연결 정보가 없습니다."
        status = "failed"
    account.last_test_status = status
    account.last_test_message = message
    account.last_test_at = _now()
    account.updated_at = account.last_test_at
    session.add(account)
    session.commit()
    return ProviderAccountTestRead(provider_account_id=account.id, status=status, message=message, tested_at=account.last_test_at)


def sync_models(session: Session, account_id: str) -> list[ModelOptionRead]:
    account = _account_by_id(session, account_id)
    fetched_by_model = {
        fetched.model.strip(): fetched
        for fetched in fetch_provider_models(account)
        if fetched.model.strip()
    }
    if not fetched_by_model:
        raise ValueError("Provider model list returned no models; existing catalog was preserved")
    existing_options = session.exec(
        select(ModelOption).where(
            ModelOption.provider_account_id == account.id,
            ModelOption.deleted_at.is_(None),
        )
    ).all()
    existing_by_model = {option.model: option for option in existing_options}
    refreshed_at = _now()
    for model_id, fetched in fetched_by_model.items():
        existing = existing_by_model.get(model_id)
        if existing and existing.source != "fetched":
            # A manually maintained option remains user-owned even if the provider
            # later starts advertising the same model id.
            continue
        if existing:
            option = existing
        else:
            option = ModelOption(
                id=_id("model_option"),
                key=_model_key(account, model_id),
                provider_account_id=account.id,
                provider_type=account.provider_type,
                model=model_id,
                label=fetched.label,
                enabled=False,
                source="fetched",
            )
        option.label = fetched.label
        option.provider_type = account.provider_type
        option.supports_chat = fetched.supports_chat
        option.supports_compression = fetched.supports_compression
        option.supports_tts = fetched.supports_tts
        option.supports_json = fetched.supports_json
        option.model_family = fetched.family
        if fetched.context_window_tokens is not None:
            option.context_window_tokens = fetched.context_window_tokens
        if fetched.max_output_tokens is not None:
            option.max_output_tokens = fetched.max_output_tokens
        if not (option.supports_chat or option.supports_compression or option.supports_tts):
            option.enabled = False
        option.updated_at = refreshed_at
        session.add(option)
    for option in existing_options:
        if option.source != "fetched" or option.model in fetched_by_model:
            continue
        option.enabled = False
        option.supports_chat = False
        option.supports_compression = False
        option.supports_tts = False
        option.updated_at = refreshed_at
        session.add(option)
    account.configured = _configured(account)
    account.updated_at = refreshed_at
    session.add(account)
    session.commit()
    return list_options(session, provider_account_id=account.id)


def list_options(session: Session, *, provider_account_id: str | None = None) -> list[ModelOptionRead]:
    statement = select(ModelOption).where(ModelOption.deleted_at.is_(None))
    if provider_account_id:
        statement = statement.where(ModelOption.provider_account_id == provider_account_id)
    options = session.exec(statement).all()
    accounts = {account.id: account for account in session.exec(select(ModelProviderAccount)).all()}
    return [serialize_option(option, accounts.get(option.provider_account_id)) for option in options if accounts.get(option.provider_account_id)]


def create_manual_option(session: Session, payload: ModelOptionManualCreate) -> ModelOption:
    account = _account_by_id(session, payload.provider_account_id)
    option = ModelOption(
        id=_id("model_option"),
        key=_model_key(account, payload.model),
        provider_account_id=account.id,
        provider_type=account.provider_type,
        model=payload.model.strip(),
        label=(payload.label or payload.model).strip(),
        enabled=payload.enabled,
        supports_chat=payload.supports_chat,
        supports_compression=payload.supports_compression,
        supports_tts=payload.supports_tts,
        model_family=payload.model_family,
        supports_json=payload.supports_json,
        context_window_tokens=payload.context_window_tokens,
        max_output_tokens=payload.max_output_tokens,
        source="manual",
    )
    session.add(option)
    session.commit()
    session.refresh(option)
    return option


def update_option(session: Session, option_id: str, payload: ModelOptionUpdate) -> ModelOption:
    option = _option_by_id(session, option_id)
    for field in ("label", "enabled", "supports_chat", "supports_compression", "supports_tts", "model_family", "supports_json"):
        value = getattr(payload, field)
        if value is not None:
            setattr(option, field, value)
    for field in ("context_window_tokens", "max_output_tokens"):
        if field in payload.model_fields_set:
            setattr(option, field, getattr(payload, field))
    option.updated_at = _now()
    session.add(option)
    session.commit()
    session.refresh(option)
    return option


def test_option(session: Session, option_id: str) -> ModelOptionTestRead:
    option = _option_by_id(session, option_id)
    account = _account_by_id(session, option.provider_account_id)
    ok = _configured(account) and option.enabled
    status = "success" if ok else "failed"
    message = "모델 옵션이 활성화되어 있고 provider 연결 정보가 있습니다." if ok else "모델 옵션이 비활성화되어 있거나 provider 연결 정보가 부족합니다."
    option.last_test_status = status
    option.last_test_message = message
    option.last_test_at = _now()
    option.updated_at = option.last_test_at
    session.add(option)
    session.commit()
    return ModelOptionTestRead(model_option_id=option.id, status=status, message=message, tested_at=option.last_test_at)


def active_options_for_runtime(session: Session, capability: str) -> list[ModelOptionRead]:
    all_options = list_options(session)
    result: list[ModelOptionRead] = []
    for option in all_options:
        account = _account_by_id(session, option.provider_account_id)
        if not account.enabled or not account.configured or not option.enabled:
            continue
        if capability == "chat" and option.supports_chat:
            result.append(option)
        elif capability == "compression" and option.supports_compression:
            result.append(option)
        elif capability == "tts" and option.supports_tts and option.provider_type == "google" and option.model_family == "gemini":
            result.append(option)
    return result


def option_by_key(session: Session, key: str | None) -> ModelOption | None:
    if not key:
        return None
    return session.exec(select(ModelOption).where(ModelOption.key == key, ModelOption.deleted_at.is_(None))).first()


def option_is_active_for_capability(session: Session, option: ModelOption, capability: str) -> bool:
    account = _account_by_id(session, option.provider_account_id)
    if not account.enabled or not account.configured or not option.enabled:
        return False
    if capability == "chat":
        return option.supports_chat
    if capability == "compression":
        return option.supports_compression
    if capability == "tts":
        return option.supports_tts and option.provider_type == "google" and option.model_family == "gemini"
    return False


def serialize_account(account: ModelProviderAccount, *, active_model_count: int = 0) -> ProviderAccountRead:
    return ProviderAccountRead(
        id=account.id,
        provider_type=account.provider_type,
        alias=account.alias,
        enabled=account.enabled,
        configured=account.configured,
        base_url=account.base_url,
        api_key_status="set" if account.api_key_secret_ref else "missing",
        api_key_hint=account.api_key_hint,
        preset=account.preset,
        last_test_status=account.last_test_status,
        last_test_message=account.last_test_message,
        last_test_at=account.last_test_at,
        active_model_count=active_model_count,
    )


def serialize_option(option: ModelOption, account: ModelProviderAccount | None = None) -> ModelOptionRead:
    account_alias = account.alias if account else "Unknown"
    return ModelOptionRead(
        id=option.id,
        key=option.key,
        provider_account_id=option.provider_account_id,
        provider_type=option.provider_type,
        provider_account_alias=account_alias,
        model=option.model,
        label=option.label,
        display_label=f"{account_alias} · {option.label}",
        enabled=option.enabled,
        supports_chat=option.supports_chat,
        supports_compression=option.supports_compression,
        supports_tts=option.supports_tts,
        model_family=option.model_family,
        supports_json=option.supports_json,
        context_window_tokens=option.context_window_tokens,
        max_output_tokens=option.max_output_tokens,
        source=option.source,
        last_test_status=option.last_test_status,
        last_test_message=option.last_test_message,
        last_test_at=option.last_test_at,
    )
