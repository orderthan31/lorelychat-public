from __future__ import annotations

import base64
import hashlib
import re
import wave
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from sqlmodel import Session

from app.core.config import get_settings
from app.db.models import Character, Message

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
    return [dict(option) for option in [*VOICE_STYLE_OPTIONS, *GEMINI_VOICE_OPTIONS]]


def tts_model_options() -> list[dict[str, str]]:
    return [dict(option) for option in TTS_MODEL_OPTIONS]


def normalize_tts_provider(value: str | None) -> str:
    provider = (value or DEFAULT_TTS_PROVIDER).strip().lower()
    return provider if provider in {"supertonic", "gemini"} else DEFAULT_TTS_PROVIDER


def normalize_tts_model(value: str | None, *, provider: str | None = None) -> str:
    normalized_provider = normalize_tts_provider(provider)
    model = (value or "").strip()
    if normalized_provider == "gemini":
        return model if model == GEMINI_TTS_MODEL else GEMINI_TTS_MODEL
    return SUPERSONIC_MODEL


def normalize_voice_style(value: str | None, *, provider: str | None = None) -> str:
    normalized_provider = normalize_tts_provider(provider)
    if normalized_provider == "gemini":
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


def _synthesize_supertonic(clean_text: str, *, voice_style: str, output_path: Path) -> None:
    tts = _tts_engine()
    style = tts.get_voice_style(voice_name=voice_style)
    result = tts.synthesize(clean_text, voice_style=style, lang="ko")
    wav = result[0] if isinstance(result, tuple) else result
    tts.save_audio(wav, str(output_path))


def _synthesize_gemini(clean_text: str, *, model: str, voice_style: str, output_path: Path) -> None:
    settings = get_settings()
    api_key = settings.gemini_api_key or settings.google_api_key or settings.chat_llm_api_key
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


def synthesize_to_upload(text: str, *, voice_style: str, file_stem: str, provider: str | None = None, model: str | None = None) -> str:
    clean_text = " ".join((text or "").split())
    if not clean_text:
        raise ValueError("TTS text is empty")
    TTS_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    normalized_provider = normalize_tts_provider(provider)
    normalized_model = normalize_tts_model(model, provider=normalized_provider)
    voice_key = normalize_voice_style(voice_style, provider=normalized_provider)
    output_path = TTS_UPLOAD_DIR / f"{_safe_name(file_stem)}_{normalized_provider}_{_safe_name(normalized_model)}_{voice_key}_{_hash_text(clean_text)}.wav"
    if output_path.exists():
        return f"/uploads/tts/{output_path.name}"
    if normalized_provider == "gemini":
        _synthesize_gemini(clean_text, model=normalized_model, voice_style=voice_key, output_path=output_path)
    else:
        _synthesize_supertonic(clean_text, voice_style=voice_key, output_path=output_path)
    return f"/uploads/tts/{output_path.name}"


def synthesize_sample(character: Character, *, sample_text: str | None = None, voice_style: str | None = None, provider: str | None = None, model: str | None = None) -> dict[str, str]:
    text = (sample_text if sample_text is not None else character.tts_sample_text or DEFAULT_SAMPLE_TEXT).strip()
    normalized_provider = normalize_tts_provider(provider or character.tts_provider)
    normalized_model = normalize_tts_model(model or character.tts_model, provider=normalized_provider)
    style = normalize_voice_style(voice_style or character.tts_voice_style, provider=normalized_provider)
    tts_text = f"{expression_prefix(text=text)}{text}".strip()
    audio_url = synthesize_to_upload(tts_text, voice_style=style, file_stem=f"sample_{character.id}", provider=normalized_provider, model=normalized_model)
    return {"audio_url": audio_url, "tts_text": tts_text, "voice_style": style, "tts_provider": normalized_provider, "tts_model": normalized_model}


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
    if message.speaker_type != "character" or not (message.content or "").strip():
        return message
    character = character or session.get(Character, message.speaker_id)
    if not character:
        return message
    metadata: dict[str, Any] = dict(message.metadata_ or {})
    normalized_provider = normalize_tts_provider(provider or character.tts_provider)
    normalized_model = normalize_tts_model(model or character.tts_model, provider=normalized_provider)
    normalized_voice_style = normalize_voice_style(voice_style or character.tts_voice_style, provider=normalized_provider)
    if metadata.get("tts_audio_url") and metadata.get("tts_provider", DEFAULT_TTS_PROVIDER) == normalized_provider and metadata.get("tts_model", DEFAULT_TTS_MODEL) == normalized_model and metadata.get("tts_voice_style") == normalized_voice_style:
        return message
    tts_text = build_tts_text(message, character)
    if not tts_text:
        return message
    audio_url = synthesize_to_upload(tts_text, voice_style=normalized_voice_style, file_stem=message.id, provider=normalized_provider, model=normalized_model)
    metadata.update({
        "tts_audio_url": audio_url,
        "tts_text": tts_text,
        "tts_voice_style": normalized_voice_style,
        "tts_provider": normalized_provider,
        "tts_model": normalized_model,
    })
    message.metadata_ = metadata
    session.add(message)
    if commit:
        session.commit()
        session.refresh(message)
    return message
