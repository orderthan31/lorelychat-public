from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session
from app.db.session import get_session
from app.schemas.characters import CharacterCreate, CharacterRead, CharacterUpdate, CharacterUsageRead
from app.schemas.pagination import CharacterPageRead
from app.services import character_service, tts_service

router = APIRouter(prefix="/characters", tags=["characters"])


@router.post("", response_model=CharacterRead)
def create_character(payload: CharacterCreate, session: Session = Depends(get_session)):
    payload.tts_provider = tts_service.normalize_tts_provider(payload.tts_provider)
    payload.tts_model = tts_service.normalize_tts_model(payload.tts_model, provider=payload.tts_provider)
    payload.tts_voice_style = tts_service.normalize_voice_style(payload.tts_voice_style, provider=payload.tts_provider)
    return character_service.create_character(session, payload)


@router.get("", response_model=list[CharacterRead] | CharacterPageRead)
def list_characters(
    paginated: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None),
    session: Session = Depends(get_session),
):
    if paginated:
        all_items = character_service.list_characters(session)
        search = (q or "").strip()
        if search:
            lowered = search.lower()
            all_items = [
                character for character in all_items
                if lowered in f"{character.name or ''} {character.description or ''} {character.persona or ''}".lower()
            ]
        all_items.sort(key=lambda character: ((character.name or ""), character.id))
        total = len(all_items)
        pages = max(1, (total + page_size - 1) // page_size)
        safe_page = min(page, pages)
        start = (safe_page - 1) * page_size
        return CharacterPageRead(items=[CharacterRead.model_validate(item.model_dump()) for item in all_items[start:start + page_size]], total=total, page=safe_page, page_size=page_size, pages=pages)
    return character_service.list_characters(session)


@router.get("/usage-ranking", response_model=list[CharacterUsageRead])
def list_character_usage_ranking(session: Session = Depends(get_session)):
    return character_service.list_characters_by_usage(session)


@router.get("/{character_id}", response_model=CharacterRead)
def get_character(character_id: str, session: Session = Depends(get_session)):
    character = character_service.get_character(session, character_id)
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")
    return character


@router.patch("/{character_id}", response_model=CharacterRead)
def update_character(character_id: str, payload: CharacterUpdate, session: Session = Depends(get_session)):
    character = character_service.get_character(session, character_id)
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")
    if payload.tts_provider is not None:
        payload.tts_provider = tts_service.normalize_tts_provider(payload.tts_provider)
    effective_provider = payload.tts_provider or getattr(character, "tts_provider", tts_service.DEFAULT_TTS_PROVIDER)
    if payload.tts_model is not None or payload.tts_provider is not None:
        payload.tts_model = tts_service.normalize_tts_model(payload.tts_model or getattr(character, "tts_model", None), provider=effective_provider)
    if payload.tts_voice_style is not None or payload.tts_provider is not None:
        payload.tts_voice_style = tts_service.normalize_voice_style(payload.tts_voice_style or getattr(character, "tts_voice_style", None), provider=effective_provider)
    return character_service.update_character(session, character, payload)


@router.delete("/{character_id}", status_code=204)
def delete_character(character_id: str, session: Session = Depends(get_session)):
    character = character_service.get_character(session, character_id)
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")
    character_service.delete_character(session, character)
