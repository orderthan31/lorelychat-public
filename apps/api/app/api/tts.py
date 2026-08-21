from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.db.models import Message
from app.db.session import get_session
from app.schemas.tts import TTSGenerateResponse, TTSModelOption, TTSSampleRequest, TTSVoiceOption
from app.services import character_service, tts_service

router = APIRouter(tags=["tts"])


@router.get("/tts/voices", response_model=list[TTSVoiceOption])
def list_tts_voices():
    return tts_service.voice_options()


@router.get("/tts/models", response_model=list[TTSModelOption])
def list_tts_models():
    return tts_service.tts_model_options()


@router.post("/characters/{character_id}/tts-sample", response_model=TTSGenerateResponse)
def synthesize_character_sample(character_id: str, payload: TTSSampleRequest, session: Session = Depends(get_session)):
    character = character_service.get_character(session, character_id)
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")
    try:
        return tts_service.synthesize_sample(
            character,
            sample_text=payload.text,
            voice_style=payload.voice_style,
            provider=payload.tts_provider,
            model=payload.tts_model,
        )
    except tts_service.TTSUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TTS synthesis failed: {exc}") from exc


@router.post("/messages/{message_id}/tts", response_model=TTSGenerateResponse)
def synthesize_message_tts(message_id: str, session: Session = Depends(get_session)):
    message = session.get(Message, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    character = character_service.get_character(session, message.speaker_id) if message.speaker_type == "character" else None
    if not character:
        raise HTTPException(status_code=422, detail="Only character messages can be synthesized")
    try:
        message = tts_service.ensure_message_tts(
            session,
            message,
            character=character,
        )
    except tts_service.TTSUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TTS synthesis failed: {exc}") from exc
    metadata = message.metadata_ or {}
    return {
        "audio_url": metadata.get("tts_audio_url", ""),
        "tts_text": metadata.get("tts_text", message.content),
        "voice_style": metadata.get("tts_voice_style", tts_service.normalize_voice_style(character.tts_voice_style)),
        "tts_provider": metadata.get("tts_provider", tts_service.DEFAULT_TTS_PROVIDER),
        "tts_model": metadata.get("tts_model", tts_service.DEFAULT_TTS_MODEL),
    }
