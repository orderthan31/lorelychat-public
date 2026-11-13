from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import wave
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from sqlalchemy import exists, update
from sqlmodel import Session, select

from app.core.config import get_settings
from app.db.models import Character, Message, ModelOption, ModelProviderAccount, RuntimeSetting

UPLOAD_ROOT = Path(get_settings().upload_root).expanduser().resolve()
TTS_UPLOAD_DIR = UPLOAD_ROOT / "tts"

SUPERSONIC_MODEL = "supertonic-3"
GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_TTS_PROVIDER = "supertonic"
DEFAULT_TTS_MODEL = SUPERSONIC_MODEL
DEFAULT_GEMINI_VOICE = "Kore"

VOICE_STYLE_OPTIONS = [
    {"key": "F1", "label": "Supertonic F1 · 밝고 선명한 여성", "gender": "female", "provider": "supertonic"},
    {"key": "F2", "label": "Supertonic F2 · 부드러운 여성", "gender": "female", "provider": "supertonic"},
    {"key": "F3", "label": "Supertonic F3 · 차분한 여성", "gender": "female", "provider": "supertonic"},
    {"key": "F4", "label": "Supertonic F4 · 또렷한 여성", "gender": "female", "provider": "supertonic"},
    {"key": "F5", "label": "Supertonic F5 · 낮고 안정적인 여성", "gender": "female", "provider": "supertonic"},
    {"key": "M1", "label": "Supertonic M1 · 밝고 선명한 남성", "gender": "male", "provider": "supertonic"},
    {"key": "M2", "label": "Supertonic M2 · 부드러운 남성", "gender": "male", "provider": "supertonic"},
    {"key": "M3", "label": "Supertonic M3 · 차분한 남성", "gender": "male", "provider": "supertonic"},
    {"key": "M4", "label": "Supertonic M4 · 또렷한 남성", "gender": "male", "provider": "supertonic"},
    {"key": "M5", "label": "Supertonic M5 · 낮고 안정적인 남성", "gender": "male", "provider": "supertonic"},
]
GEMINI_VOICE_OPTIONS = [
    {"key": "Kore", "label": "Gemini Kore · Firm", "gender": "neutral", "provider": "gemini"},
    {"key": "Aoede", "label": "Gemini Aoede · Breezy", "gender": "neutral", "provider": "gemini"},
    {"key": "Leda", "label": "Gemini Leda · Youthful", "gender": "neutral", "provider": "gemini"},
    {"key": "Achernar", "label": "Gemini Achernar · Soft", "gender": "neutral", "provider": "gemini"},
    {"key": "Vindemiatrix", "label": "Gemini Vindemiatrix · Gentle", "gender": "neutral", "provider": "gemini"},
    {"key": "Sulafat", "label": "Gemini Sulafat · Warm", "gender": "neutral", "provider": "gemini"},
    {"key": "Zephyr", "label": "Gemini Zephyr · Bright", "gender": "neutral", "provider": "gemini"},
    {"key": "Puck", "label": "Gemini Puck · Upbeat", "gender": "neutral", "provider": "gemini"},
    {"key": "Callirrhoe", "label": "Gemini Callirrhoe · Easy-going", "gender": "neutral", "provider": "gemini"},
    {"key": "Autonoe", "label": "Gemini Autonoe · Bright", "gender": "neutral", "provider": "gemini"},
]
VOICE_STYLE_KEYS = {option["key"] for option in VOICE_STYLE_OPTIONS}
GEMINI_VOICE_KEYS = {option["key"] for option in GEMINI_VOICE_OPTIONS}
DEFAULT_VOICE_STYLE = "F1"
DEFAULT_SAMPLE_TEXT = "안녕하세요. 이 목소리가 캐릭터와 잘 어울리는지 확인해 주세요."

TTS_MODEL_OPTIONS = [
    {"key": SUPERSONIC_MODEL, "label": "Supertonic 3 · 로컬/무료", "provider": "supertonic", "cost_hint": "로컬 CPU 사용, API 과금 없음"},
    {"key": GEMINI_TTS_MODEL, "label": "Gemini 3.1 Flash TTS Preview · 고품질/API", "provider": "gemini", "cost_hint": "Google Gemini API 과금"},
]


class TTSUnavailableError(RuntimeError):
    pass


def voice_options() -> list[dict[str, str]]:
    return [dict(option) for option in [*VOICE_STYLE_OPTIONS, *GEMINI_VOICE_OPTIONS]] + [
        {**option, 'provider': 'vertex_ai'} for option in GEMINI_VOICE_OPTIONS]


def tts_model_options(session: Session | None = None) -> list[dict[str, str]]:
    result = [dict(option) for option in TTS_MODEL_OPTIONS]
    if session is not None:
        from app.services import runtime_settings_service
        for option in runtime_settings_service.active_option_reads(session, 'tts'):
            result.append({'key': option.key, 'label': option.label,
                'provider': 'gemini' if option.provider == 'google' else option.provider,
                'cost_hint': 'Google Cloud API' if option.provider == 'vertex_ai' else 'Google Gemini API'})
    return result


def normalize_tts_provider(value: str | None) -> str:
    provider = (value or DEFAULT_TTS_PROVIDER).strip().lower()
    if provider == 'google':
        provider = 'gemini'
    return provider if provider in {"supertonic", "gemini", "vertex_ai"} else DEFAULT_TTS_PROVIDER


def normalize_tts_model(value: str | None, *, provider: str | None = None) -> str:
    normalized_provider = normalize_tts_provider(provider)
    model = (value or "").strip()
    if normalized_provider == 'vertex_ai':
        return model  # Exact ModelOption key, never guess an account by model ID.
    if normalized_provider == "gemini":
        return model or GEMINI_TTS_MODEL
    return SUPERSONIC_MODEL


def normalize_voice_style(value: str | None, *, provider: str | None = None) -> str:
    normalized_provider = normalize_tts_provider(provider)
    if normalized_provider in {"gemini", "vertex_ai"}:
        key = (value or DEFAULT_GEMINI_VOICE).strip()
        return key if key in GEMINI_VOICE_KEYS else DEFAULT_GEMINI_VOICE
    key = (value or DEFAULT_VOICE_STYLE).strip().upper()
    return key if key in VOICE_STYLE_KEYS else DEFAULT_VOICE_STYLE


@lru_cache(maxsize=1)
def _tts_engine():
    try:
        from supertonic import TTS  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on optional runtime package
        raise TTSUnavailableError("supertonic package is not installed") from exc
    return TTS(auto_download=True)


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_") or "tts"


def _hash_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def expression_prefix(*, emotion: str | None = None, action: str | None = None, text: str | None = None) -> str:
    combined = " ".join([emotion or "", action or "", text or ""]).lower()
    if any(token in combined for token in ["웃", "미소", "happy", "joy", "playful", "장난"]):
        return "<laugh> "
    if any(token in combined for token in ["한숨", "슬픔", "sad", "tired", "지침", "피곤", "sigh"]):
        return "<sigh> "
    if any(token in combined for token in ["긴장", "당황", "떨", "angry", "분노", "breath", "숨"]):
        return "<breath> "
    return ""


def build_tts_text(message: Message, character: Character | None = None, *, text_override: str | None = None) -> str:
    dialogue = " ".join((text_override if text_override is not None else message.content or "").split())
    if not dialogue:
        return ""
    prefix = expression_prefix(emotion=message.emotion, action=message.action, text=dialogue)
    return f"{prefix}{dialogue}".strip()


def _write_wave_file(path: Path, pcm: bytes, *, channels: int = 1, rate: int = 24000, sample_width: int = 2) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm)


def _run_tts_process(command: list[str], *, payload: bytes, timeout: float) -> None:
    """Bound the entire engine lifecycle, including download, and reap on exit.

    A thread timeout cannot stop native inference. A dedicated process group
    also contains descendants; no private text or engine output enters logs.
    """
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        process.communicate(payload, timeout=timeout)
        if process.returncode:
            raise TTSUnavailableError("TTS synthesis process failed")
    except BaseException as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise TTSUnavailableError("TTS synthesis deadline exceeded") from None
        raise


def _synthesize_supertonic(clean_text: str, *, voice_style: str, output_path: Path) -> None:
    configured_timeout = getattr(get_settings(), "tts_timeout_seconds", 120.0)
    timeout = 120.0 if configured_timeout is None else float(configured_timeout)
    if timeout <= 0:
        raise TTSUnavailableError("Invalid TTS deadline")
    _run_tts_process([sys.executable, "-m", "app.services.derived_tts_worker"],
        payload=json.dumps({"text": clean_text, "voice_style": voice_style,
                            "output_path": str(output_path)}).encode(), timeout=timeout)


def _synthesize_supertonic_in_process(clean_text: str, *, voice_style: str, output_path: Path) -> None:
    tts = _tts_engine()
    style = tts.get_voice_style(voice_name=voice_style)
    result = tts.synthesize(clean_text, voice_style=style, lang="ko")
    wav = result[0] if isinstance(result, tuple) else result
    tts.save_audio(wav, str(output_path))


def _synthesize_gemini(clean_text: str, *, model: str, voice_style: str, output_path: Path, overrides: dict | None = None) -> None:
    settings = get_settings()
    api_key = (overrides.get('api_key') if overrides is not None else
               settings.gemini_api_key or settings.google_api_key or settings.chat_llm_api_key)
    if not re.fullmatch(r'gemini-[a-zA-Z0-9.-]+', model):
        raise TTSUnavailableError('Invalid Gemini TTS model')
    if not api_key:
        raise TTSUnavailableError("Gemini API key is not configured")
    payload = {
        "contents": [{"parts": [{"text": clean_text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice_style}
                }
            },
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        response = httpx.post(url, params={"key": api_key}, json=payload, timeout=120.0)
        response.raise_for_status()
    except Exception as exc:
        raise TTSUnavailableError(f"Gemini TTS endpoint unavailable: {model} ({type(exc).__name__})") from exc
    data = response.json()
    candidates = data.get("candidates") or []
    parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
    inline_data = next((part.get("inlineData") or part.get("inline_data") for part in parts if part.get("inlineData") or part.get("inline_data")), None)
    if not inline_data or not inline_data.get("data"):
        raise TTSUnavailableError("Gemini TTS response did not include audio data")
    pcm = base64.b64decode(inline_data["data"])
    _write_wave_file(output_path, pcm)


def _selected_tts(session, provider, model, *, conversation_id=None, use_default=False):
    """Read selection without creating settings or committing the caller's work."""
    from app.services import runtime_settings_service as runtime
    if session is None:
        return provider, model
    if use_default:
        setting = (runtime.get_setting(session, conversation_id) if conversation_id else None)
        setting = setting or runtime.get_setting(session, runtime.GLOBAL_RUNTIME_SETTING_ID)
        if setting is not None and setting.default_tts_model_option_key:
            try:
                values = runtime.tts_overrides_for_setting(setting, session)
                if values is None:
                    raise ValueError()
            except ValueError:
                raise TTSUnavailableError('Selected TTS model option is unavailable') from None
            return values['provider'], values['model_option_key']
    return provider, model


def _tts_overrides(session, provider, model):
    from app.services import runtime_settings_service as runtime, model_provider_service
    if session is not None and model_provider_service.option_by_key(session, model) is not None:
        try:
            values = runtime._dynamic_llm_overrides(session, model, 'tts')
            if values is None or normalize_tts_provider(values['provider']) != provider:
                raise ValueError()
            return values
        except ValueError:
            raise TTSUnavailableError('Selected TTS model option is unavailable') from None
    if provider == 'vertex_ai':
        raise TTSUnavailableError('Vertex TTS requires an active account-owned model option')
    return None


def _tts_route_key(overrides):
    if not overrides:
        return None
    return _hash_text(json.dumps({key: overrides.get(key) for key in (
        'provider_account_id', 'model_option_key', 'model', 'provider_config_revision',
        'vertex_project', 'vertex_location', 'vertex_auth_mode')}, sort_keys=True))


def _tts_selection_guards(session, overrides, *, conversation_id, use_default):
    """Bind new dynamic dependencies in the existing atomic message CAS."""
    guards = []
    if overrides:
        option = session.exec(select(ModelOption).where(ModelOption.key == overrides['model_option_key'])).one()
        account = session.get(ModelProviderAccount, option.provider_account_id)
        for table, row, fields in (
            (ModelOption, option, ('key', 'model', 'provider_account_id', 'provider_type',
                                  'model_family', 'enabled', 'deleted_at', 'supports_tts')),
            (ModelProviderAccount, account, ('provider_type', 'enabled', 'deleted_at',
                'configured', 'updated_at', 'api_key_secret_ref', 'vertex_project',
                'vertex_location', 'vertex_auth_mode')),
        ):
            guards.append(exists(select(table.id).where(table.id == row.id,
                *(getattr(table, field) == getattr(row, field) for field in fields))))
    if use_default:
        # Bind room row presence as well as inherited global default identity.
        ids = [conversation_id, 'global'] if conversation_id else ['global']
        for setting_id in ids:
            row = session.get(RuntimeSetting, setting_id)
            present = exists(select(RuntimeSetting.id).where(RuntimeSetting.id == setting_id))
            guards.append(exists(select(RuntimeSetting.id).where(RuntimeSetting.id == setting_id,
                RuntimeSetting.default_tts_model_option_key == row.default_tts_model_option_key))
                if row else ~present)
            if row:
                break
    return guards


def new_vertex_tts_transport():
    return httpx.HTTPTransport(retries=0, trust_env=False)


def _synthesize_vertex(clean_text: str, *, overrides: dict, voice_style: str, output_path: Path) -> None:
    """Cloud generateContent; no SDK global credentials or automatic paid retries.

    2.5/3.1: v1beta1, prebuiltVoiceConfig, PCM16. 3.8: v1,
    voiceConfig.voice and explicit AUDIO_L16 (unary default is otherwise WAV).
    """
    from app.engine import vertex_auth
    from app.engine.litellm_transport import resolve_route
    try:
        route = resolve_route(SimpleNamespace(provider=lambda: 'vertex_ai',
            model=lambda: overrides['model'], overrides=overrides))
        if route.model not in vertex_auth.TTS_MODELS:
            raise ValueError()
        modern = route.model.startswith('gemini-3.8-')
        if modern and route.vertex_location not in {'global', 'us', 'eu'}:
            raise TTSUnavailableError('Gemini 3.8 TTS requires a global, us or eu account location')
        if not modern and route.vertex_location in {'us', 'eu'}:
            raise TTSUnavailableError('This Gemini TTS model requires global or a regional account location')
        if len(clean_text.encode('utf-8')) > 8000:
            raise TTSUnavailableError('TTS input exceeds the 8000-byte synthesis budget')
        configured = getattr(get_settings(), 'tts_timeout_seconds', 120.0)
        timeout = float(configured if configured is not None else 120.0)
        if not 0 < timeout <= 600:
            raise TTSUnavailableError('Invalid TTS deadline')
        deadline = time.monotonic() + timeout
        token = vertex_auth._acquire_token(route, timeout)
        voice = {'voice': voice_style} if modern else {'prebuiltVoiceConfig': {'voiceName': voice_style}}
        config = {'responseModalities': ['AUDIO'], 'speechConfig': {'voiceConfig': voice}}
        if modern:
            config['responseFormat'] = [{'audio': {'mimeType': 'AUDIO_L16'}}]
        payload = {'contents': [{'role': 'user', 'parts': [{'text': clean_text}]}], 'generationConfig': config}
        version = 'v1' if modern else 'v1beta1'
        url = (f'https://{vertex_auth.endpoint_host(route.vertex_location)}/{version}/projects/'
               f'{route.vertex_project}/locations/{route.vertex_location}/publishers/google/models/{route.model}:generateContent')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        with httpx.Client(transport=new_vertex_tts_transport(), trust_env=False, follow_redirects=False) as client:
            with client.stream('POST', url, headers={'Authorization': f'Bearer {token}',
                    'x-goog-user-project': str(route.vertex_project)}, json=payload, timeout=remaining) as response:
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() >= deadline or len(raw) + len(chunk) > 48 * 1024 * 1024:
                        raise ValueError()
                    raw.extend(chunk)
        data = json.loads(raw)
        candidates = data.get('candidates') or []
        if len(candidates) != 1 or candidates[0].get('finishReason') not in (None, 'STOP'):
            raise ValueError()
        parts = candidates[0]['content']['parts']
        audio = [part.get('inlineData') or part.get('inline_data') for part in parts
                 if part.get('inlineData') or part.get('inline_data')]
        if len(audio) != 1:
            raise ValueError()
        mime = str(audio[0].get('mimeType') or audio[0].get('mime_type') or '').lower()
        if mime.split(';')[0].strip() != 'audio/l16':
            raise ValueError()
        pcm = base64.b64decode(audio[0]['data'], validate=True)
        if not pcm or len(pcm) % 2 or pcm.startswith(b'RIFF'):
            raise ValueError()
        _write_wave_file(output_path, pcm)
    except TTSUnavailableError:
        raise
    except Exception:
        # Never expose provider bodies, utterances, tokens or credential errors.
        raise TTSUnavailableError('Vertex TTS synthesis unavailable or invalid audio response') from None


def _valid_wave(path: Path) -> bool:
    """Reject truncated headers/data, empty sound, symlinks and incomplete PCM."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        with wave.open(str(path), "rb") as audio:
            if audio.getcomptype() != "NONE" or audio.getnframes() <= 0 or audio.getframerate() <= 0:
                return False
            remaining = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
            while remaining:
                block = audio.readframes(min(65536, max(1, remaining // (audio.getnchannels() * audio.getsampwidth()))))
                if not block:
                    return False
                remaining -= len(block)
            return remaining == 0
    except (OSError, EOFError, wave.Error, ValueError):
        return False


def _cached_audio_valid(url: Any) -> bool:
    if not isinstance(url, str) or not url.startswith("/uploads/tts/"):
        return False
    name = url.removeprefix("/uploads/tts/")
    return bool(name and Path(name).name == name and _valid_wave(TTS_UPLOAD_DIR / name))


def synthesize_to_upload(text: str, *, voice_style: str, file_stem: str, provider: str | None = None, model: str | None = None, session: Session | None = None) -> str:
    clean_text = " ".join((text or "").split())
    if not clean_text:
        raise ValueError("TTS text is empty")
    TTS_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    normalized_provider = normalize_tts_provider(provider)
    normalized_model = normalize_tts_model(model, provider=normalized_provider)
    voice_key = normalize_voice_style(voice_style, provider=normalized_provider)
    overrides = _tts_overrides(session, normalized_provider, normalized_model)
    # Include account/model revision, never secrets, in the audio cache identity.
    route_key = _tts_route_key(overrides)
    output_path = TTS_UPLOAD_DIR / f"{_safe_name(file_stem)}_{normalized_provider}_{_safe_name(normalized_model)}_{voice_key}_{_hash_text(clean_text)}.wav"
    if route_key:
        output_path = output_path.with_name(output_path.stem + '_' + route_key + '.wav')
    if _valid_wave(output_path):
        return f"/uploads/tts/{output_path.name}"
    # Each producer owns its staging file. Never remove a completed cache here:
    # another message/request may reference it. Atomic replacement is safe even
    # when equivalent producers finish concurrently.
    fd, temporary = tempfile.mkstemp(prefix=".partial-", suffix=".wav", dir=TTS_UPLOAD_DIR)
    os.close(fd)
    staging = Path(temporary)
    try:
        if normalized_provider == 'vertex_ai':
            if overrides is None:
                raise TTSUnavailableError('Vertex TTS requires an active model option')
            _synthesize_vertex(clean_text, overrides=overrides, voice_style=voice_key, output_path=staging)
        elif normalized_provider == "gemini":
            kwargs = {'overrides': overrides} if overrides is not None else {}
            _synthesize_gemini(clean_text, model=overrides['model'] if overrides else normalized_model,
                               voice_style=voice_key, output_path=staging, **kwargs)
        else:
            _synthesize_supertonic(clean_text, voice_style=voice_key, output_path=staging)
        if not _valid_wave(staging):
            raise TTSUnavailableError("TTS synthesis did not produce a complete WAV")
        with staging.open("rb") as audio:
            os.fsync(audio.fileno())
        os.replace(staging, output_path)
    finally:
        staging.unlink(missing_ok=True)
    return f"/uploads/tts/{output_path.name}"


def synthesize_sample(character: Character, *, sample_text: str | None = None, voice_style: str | None = None, provider: str | None = None, model: str | None = None, session: Session | None = None) -> dict[str, str]:
    text = (sample_text if sample_text is not None else character.tts_sample_text or DEFAULT_SAMPLE_TEXT).strip()
    provider, model = _selected_tts(session, provider or character.tts_provider, model or character.tts_model,
                                    use_default=provider is None and model is None)
    normalized_provider = normalize_tts_provider(provider or character.tts_provider)
    normalized_model = normalize_tts_model(model or character.tts_model, provider=normalized_provider)
    style = normalize_voice_style(voice_style or character.tts_voice_style, provider=normalized_provider)
    tts_text = f"{expression_prefix(text=text)}{text}".strip()
    audio_url = synthesize_to_upload(tts_text, voice_style=style, file_stem=f"sample_{character.id}", provider=normalized_provider, model=normalized_model,
                                     **({'session': session} if session is not None else {}))
    return {"audio_url": audio_url, "tts_text": tts_text, "voice_style": style, "tts_provider": normalized_provider, "tts_model": normalized_model}


def _tts_source(message: Message) -> tuple:
    metadata = message.metadata_ or {}
    return (message.conversation_id, message.speaker_type, message.speaker_id,
            message.created_at, message.content, message.action, message.emotion,
            message.thought, json.dumps({key: metadata.get(key) for key in
                ("raw_body", "render_parts", "response_protocol")}, sort_keys=True, ensure_ascii=False))


def ensure_message_tts(
    session: Session,
    message: Message,
    *,
    character: Character | None = None,
    provider: str | None = None,
    model: str | None = None,
    voice_style: str | None = None,
    commit: bool = True,
) -> Message:
    with session.no_autoflush:
        current = session.exec(select(Message).where(Message.id == message.id)
                               .execution_options(populate_existing=True)).first()
        if current is None:
            raise TTSUnavailableError("TTS source message no longer exists")
        message = current
    if message.speaker_type != "character" or not (message.content or "").strip():
        return message
    with session.no_autoflush:
        character = session.exec(select(Character).where(Character.id == message.speaker_id)
                                 .execution_options(populate_existing=True)).first()
    if not character:
        return message
    source = _tts_source(message)
    voice_source = (character.tts_provider, character.tts_model, character.tts_voice_style)
    metadata: dict[str, Any] = dict(message.metadata_ or {})
    use_default = provider is None and model is None
    requested_provider, requested_model = provider or character.tts_provider, model or character.tts_model
    provider, model = _selected_tts(session, requested_provider, requested_model,
                                   conversation_id=message.conversation_id, use_default=use_default)
    normalized_provider = normalize_tts_provider(provider or character.tts_provider)
    normalized_model = normalize_tts_model(model or character.tts_model, provider=normalized_provider)
    normalized_voice_style = normalize_voice_style(voice_style or character.tts_voice_style, provider=normalized_provider)
    selected_overrides = _tts_overrides(session, normalized_provider, normalized_model)
    route_key = _tts_route_key(selected_overrides)
    selection_guards = _tts_selection_guards(session, selected_overrides,
        conversation_id=message.conversation_id, use_default=use_default)
    tts_text = build_tts_text(message, character)
    if not tts_text:
        return message
    if (metadata.get("tts_text") == tts_text and _cached_audio_valid(metadata.get("tts_audio_url"))
            and metadata.get("tts_provider", DEFAULT_TTS_PROVIDER) == normalized_provider
            and metadata.get("tts_model", DEFAULT_TTS_MODEL) == normalized_model
            and metadata.get('tts_route_key') == route_key
            and metadata.get("tts_voice_style") == normalized_voice_style):
        return message
    audio_url = synthesize_to_upload(tts_text, voice_style=normalized_voice_style, file_stem=message.id, provider=normalized_provider, model=normalized_model,
                                     **({'session': session} if route_key else {}))
    # Read current DB state after the external effect; never restore captured
    # metadata. A conditional UPDATE closes the remaining read/write race.
    with session.no_autoflush:
        current = session.exec(select(Message).where(Message.id == message.id)
                               .execution_options(populate_existing=True)).first()
        current_character = session.exec(select(Character).where(Character.id == character.id)
                                         .execution_options(populate_existing=True)).first()
        if current is None or current_character is None or _tts_source(current) != source:
            return current or message
        if (current_character.tts_provider, current_character.tts_model, current_character.tts_voice_style) != voice_source:
            return current
        # A changed default/account/model while synthesis ran must not attach
        # audio for the superseded selection. Existing source CAS stays intact.
        try:
            selection = _selected_tts(session, requested_provider, requested_model,
                                     conversation_id=message.conversation_id, use_default=use_default)
            if selection != (provider, model) or _tts_route_key(
                    _tts_overrides(session, normalized_provider, normalized_model)) != route_key:
                return current
        except TTSUnavailableError:
            return current
        metadata = dict(current.metadata_ or {})
        updated_metadata = {**metadata,
            "tts_audio_url": audio_url, "tts_text": tts_text,
            "tts_voice_style": normalized_voice_style, "tts_provider": normalized_provider,
            "tts_model": normalized_model}
        if route_key:
            updated_metadata['tts_route_key'] = route_key
        else:
            updated_metadata.pop('tts_route_key', None)
        statement = update(Message).where(
            *selection_guards,
            Message.id == current.id, Message.conversation_id == current.conversation_id,
            Message.created_at == current.created_at, Message.speaker_id == current.speaker_id,
            Message.speaker_type == current.speaker_type, Message.content == current.content,
            Message.action == current.action, Message.emotion == current.emotion,
            Message.thought == current.thought, Message.metadata_ == metadata,
            exists(select(Character.id).where(Character.id == current_character.id,
                Character.tts_provider == voice_source[0], Character.tts_model == voice_source[1],
                Character.tts_voice_style == voice_source[2])),
        ).values(metadata_=updated_metadata).execution_options(synchronize_session=False)
        result = session.execute(statement)
        if result.rowcount != 1:
            session.refresh(current)
            return current
        if commit:
            session.commit()
        session.refresh(current)
    return current
