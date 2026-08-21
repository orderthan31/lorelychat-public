from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlmodel import Session, select, delete

from app.db.models import Character, CharacterAsset, Message, MessageAsset, SceneState
from app.schemas.assets import CharacterAssetCreate, CharacterAssetUpdate, CharacterAssetRead, MessageAssetRead
from app.services.asset_selector import select_asset_for_message as select_asset_v2


def _metadata(asset: CharacterAsset) -> dict:
    return asset.metadata_ or {}


def to_asset_read(asset: CharacterAsset) -> CharacterAssetRead:
    return CharacterAssetRead(
        id=asset.id,
        character_id=asset.character_id,
        asset_type=asset.asset_type,
        label=asset.label,
        description=asset.description,
        image_url=asset.image_url,
        thumbnail_url=asset.thumbnail_url,
        tags=asset.tags or [],
        mood_tags=asset.mood_tags or [],
        scene_tags=asset.scene_tags or [],
        outfit_tags=asset.outfit_tags or [],
        pose_tags=asset.pose_tags or [],
        expression_tags=asset.expression_tags or [],
        priority=asset.priority,
        enabled=asset.enabled,
        is_default=asset.is_default,
        source=asset.source,
        generation_prompt=asset.generation_prompt,
        negative_prompt=asset.negative_prompt,
        metadata=_metadata(asset),
    )


def to_message_asset_read(asset: CharacterAsset) -> MessageAssetRead:
    return MessageAssetRead(
        id=asset.id,
        character_id=asset.character_id,
        asset_type=asset.asset_type,
        label=asset.label,
        description=asset.description,
        image_url=asset.image_url,
        thumbnail_url=asset.thumbnail_url,
        tags=asset.tags or [],
        mood_tags=asset.mood_tags or [],
        scene_tags=asset.scene_tags or [],
        outfit_tags=asset.outfit_tags or [],
        pose_tags=asset.pose_tags or [],
        expression_tags=asset.expression_tags or [],
    )


def list_assets(session: Session, character_id: str, *, include_disabled: bool = True) -> list[CharacterAsset]:
    stmt = select(CharacterAsset).where(CharacterAsset.character_id == character_id).order_by(CharacterAsset.is_default.desc(), CharacterAsset.priority.desc(), CharacterAsset.created_at.desc())
    if not include_disabled:
        stmt = stmt.where(CharacterAsset.enabled == True)  # noqa: E712
    return list(session.exec(stmt).all())


def get_asset(session: Session, asset_id: str) -> CharacterAsset | None:
    return session.get(CharacterAsset, asset_id)


def create_asset(session: Session, character_id: str, payload: CharacterAssetCreate) -> CharacterAsset:
    if not session.get(Character, character_id):
        raise ValueError("Character not found")
    asset = CharacterAsset(
        id=f"asset_{uuid4().hex[:12]}",
        character_id=character_id,
        asset_type=payload.asset_type,
        label=payload.label,
        description=payload.description,
        image_url=payload.image_url,
        thumbnail_url=payload.thumbnail_url,
        tags=payload.tags,
        mood_tags=payload.mood_tags,
        scene_tags=payload.scene_tags,
        outfit_tags=payload.outfit_tags,
        pose_tags=payload.pose_tags,
        expression_tags=payload.expression_tags,
        priority=payload.priority,
        enabled=payload.enabled,
        is_default=False,
        source=payload.source,
        generation_prompt=payload.generation_prompt,
        negative_prompt=payload.negative_prompt,
        metadata_=payload.metadata,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    if payload.is_default:
        set_default_avatar(session, asset.id)
        session.refresh(asset)
    return asset


def update_asset(session: Session, asset: CharacterAsset, payload: CharacterAssetUpdate) -> CharacterAsset:
    data = payload.model_dump(exclude_unset=True)
    set_default = bool(data.pop("is_default", False))
    if "metadata" in data:
        asset.metadata_ = data.pop("metadata") or {}
    for key, value in data.items():
        setattr(asset, key, value)
    asset.updated_at = datetime.now(timezone.utc)
    session.add(asset)
    session.commit()
    session.refresh(asset)
    if set_default:
        set_default_avatar(session, asset.id)
        session.refresh(asset)
    return asset


def delete_asset(session: Session, asset: CharacterAsset) -> None:
    session.exec(delete(MessageAsset).where(MessageAsset.asset_id == asset.id))
    session.delete(asset)
    session.commit()


def set_default_avatar(session: Session, asset_id: str) -> CharacterAsset:
    asset = session.get(CharacterAsset, asset_id)
    if not asset:
        raise ValueError("Asset not found")
    character = session.get(Character, asset.character_id)
    if not character:
        raise ValueError("Character not found")
    for other in list_assets(session, asset.character_id):
        other.is_default = other.id == asset.id
        session.add(other)
    character.avatar_url = asset.image_url
    character.updated_at = datetime.now(timezone.utc)
    session.add(character)
    session.commit()
    session.refresh(asset)
    return asset


def attach_asset_to_message(
    session: Session,
    message_id: str,
    asset_id: str,
    *,
    display_order: int = 0,
    commit: bool = True,
) -> MessageAsset:
    existing = session.exec(
        select(MessageAsset).where(
            MessageAsset.message_id == message_id,
            MessageAsset.asset_id == asset_id,
        )
    ).first()
    if existing:
        return existing
    link = MessageAsset(id=f"msgasset_{uuid4().hex[:12]}", message_id=message_id, asset_id=asset_id, display_order=display_order)
    session.add(link)
    if commit:
        session.commit()
        session.refresh(link)
    return link


def list_message_assets(session: Session, message_id: str) -> list[CharacterAsset]:
    links = list(session.exec(select(MessageAsset).where(MessageAsset.message_id == message_id).order_by(MessageAsset.display_order)).all())
    assets = []
    for link in links:
        asset = session.get(CharacterAsset, link.asset_id)
        if asset:
            assets.append(asset)
    return assets


def serialize_message(session: Session, message: Message) -> dict:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "speaker_type": message.speaker_type,
        "speaker_id": message.speaker_id,
        "content": message.content,
        "emotion": message.emotion,
        "action": message.action,
        "thought": message.thought,
        "metadata": message.metadata_ or {},
        "assets": [to_message_asset_read(asset).model_dump() for asset in list_message_assets(session, message.id)],
        "tts_audio_url": (message.metadata_ or {}).get("tts_audio_url"),
        "tts_text": (message.metadata_ or {}).get("tts_text"),
    }


def migrate_existing_avatars(session: Session) -> int:
    created = 0
    characters = list(session.exec(select(Character)).all())
    for character in characters:
        avatar_url = (character.avatar_url or "").strip()
        if not avatar_url:
            continue
        exists = session.exec(select(CharacterAsset).where(CharacterAsset.character_id == character.id, CharacterAsset.image_url == avatar_url)).first()
        if exists:
            if not exists.is_default:
                set_default_avatar(session, exists.id)
            continue
        asset = CharacterAsset(
            id=f"asset_{uuid4().hex[:12]}",
            character_id=character.id,
            asset_type="image",
            label="기본 프사",
            description=f"{character.name}의 기본 프로필 이미지",
            image_url=avatar_url,
            tags=["profile", "default", "korean", "realistic"],
            mood_tags=["neutral"],
            scene_tags=["default"],
            pose_tags=["headshot", "upper_body"],
            expression_tags=["neutral"],
            priority=50,
            enabled=True,
            is_default=True,
            source="existing_avatar",
        )
        for other in list_assets(session, character.id):
            other.is_default = False
            session.add(other)
        session.add(asset)
        created += 1
    session.commit()
    return created


def select_asset_for_message(
    session: Session,
    *,
    conversation_id: str,
    character_id: str,
    emotion: str | None,
    action: str | None,
    content: str | None,
    scene_state: SceneState | None,
    min_score: int = 25,
    excluded_asset_ids: set[str] | None = None,
    selection_seed: str | None = None,
) -> CharacterAsset | None:
    return select_asset_v2(
        session,
        conversation_id=conversation_id,
        character_id=character_id,
        emotion=emotion,
        action=action,
        content=content,
        scene_state=scene_state,
        min_score=min_score,
        excluded_asset_ids=excluded_asset_ids,
        selection_seed=selection_seed,
    )
