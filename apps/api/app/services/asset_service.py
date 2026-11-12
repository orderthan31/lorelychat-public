from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from sqlmodel import Session, select, delete
from sqlalchemy import update

from app.db.models import Character, CharacterAsset, Message, MessageAsset, PostCommitTask, SceneState
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


def _cleanup_uncommitted_image(session: Session, image) -> None:
    """Unknown commit state retains bytes; never delete an existing input file."""
    try:
        session.rollback()
        registered = session.exec(select(CharacterAsset.id).where(
            CharacterAsset.image_url == image.image_url).limit(1)).first()
    except Exception:
        return
    if not registered:
        image.cleanup()


async def create_asset_ingested(session: Session, character_id: str, payload: CharacterAssetCreate, *, upload_root: Path, shared_root: str | None = None, data: bytes | None = None, source_mime: str | None = None) -> CharacterAsset:
    from app.services.asset_image_service import ingest_image
    if not session.get(Character, character_id):
        raise ValueError("Character not found")
    # Revalidate before any file/network side effects, even for non-HTTP callers.
    payload = CharacterAssetCreate.model_validate(payload.model_dump())
    if session.new or session.dirty or session.deleted:
        raise ValueError('Image ingestion requires a clean session')
    session.rollback()  # No DB transaction across URL import/native processing.
    image = await ingest_image(upload_root=upload_root, shared_root=shared_root, data=data,
                               source_mime=source_mime, image_url=payload.image_url)
    normalized = payload.model_copy(update={
        'image_url': image.image_url, 'thumbnail_url': image.thumbnail_url,
        'source': image.source, 'metadata': {**payload.metadata, **image.metadata},
    })
    try:
        with session.no_autoflush:
            reserved = session.exec(update(Character).where(Character.id == character_id)
                .values(updated_at=Character.updated_at).execution_options(synchronize_session=False))
        if reserved.rowcount != 1:
            raise ValueError('Character not found')
        session.expire_all()
        return create_asset(session, character_id, normalized)
    except BaseException:
        _cleanup_uncommitted_image(session, image)
        raise


async def update_asset_ingested(session: Session, asset: CharacterAsset, payload: CharacterAssetUpdate, *, upload_root: Path, shared_root: str | None = None) -> CharacterAsset:
    from app.services.asset_image_service import ingest_image
    payload = CharacterAssetUpdate.model_validate(payload.model_dump(exclude_unset=True))
    # An unchanged legacy URL is metadata-only: never fetch/convert it implicitly.
    if 'image_url' not in payload.model_fields_set or payload.image_url == asset.image_url:
        if 'thumbnail_url' in payload.model_fields_set and payload.thumbnail_url != asset.thumbnail_url:
            raise ValueError('Thumbnail is managed by image ingestion')
        if 'metadata' in payload.model_fields_set:
            metadata = dict(payload.metadata or {})
            for key in ('normalization', 'thumbnail'):
                metadata.pop(key, None)
                if key in (asset.metadata_ or {}):
                    metadata[key] = asset.metadata_[key]
            payload = payload.model_copy(update={'metadata': metadata})
        return update_asset(session, asset, payload)
    if session.new or session.dirty or session.deleted:
        raise ValueError('Image ingestion requires a clean session')
    asset_id = asset.id
    def fingerprint(row):
        return json.dumps({name: getattr(row, name) for name in type(row).model_fields},
                          sort_keys=True, default=str)
    before = fingerprint(asset)
    session.rollback()
    image = await ingest_image(upload_root=upload_root, shared_root=shared_root, image_url=payload.image_url)
    try:
        with session.no_autoflush:
            reserved = session.exec(update(CharacterAsset).where(CharacterAsset.id == asset_id)
                .values(updated_at=CharacterAsset.updated_at).execution_options(synchronize_session=False))
        session.expire_all()
        current = session.get(CharacterAsset, asset_id)
        if reserved.rowcount != 1 or current is None or fingerprint(current) != before:
            raise ValueError('Asset changed while importing image; reload before retrying')
        normalized = payload.model_copy(update={
            'image_url': image.image_url, 'thumbnail_url': image.thumbnail_url,
            'source': image.source, 'metadata': {**((payload.metadata if 'metadata' in payload.model_fields_set else current.metadata_) or {}), **image.metadata},
        })
        return update_asset(session, current, normalized)
    except BaseException:
        _cleanup_uncommitted_image(session, image)
        raise


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
    if payload.is_default:
        session.exec(update(CharacterAsset).where(CharacterAsset.character_id == character_id).values(is_default=False))
        asset.is_default = True
        character = session.get(Character, character_id)
        character.avatar_url = asset.image_url
        character.updated_at = datetime.now(timezone.utc)
        session.add(character)
    session.add(asset)
    session.commit()
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


def pending_asset_decision_message_ids(session: Session, conversation_id: str) -> set[str]:
    """Read ephemeral hydration state; no content or state is written on GET."""
    payloads = session.exec(select(PostCommitTask.payload_).where(
        PostCommitTask.task_type == "asset_decision",
        PostCommitTask.status.in_(("queued", "processing")),
        PostCommitTask.payload_["conversation_id"].as_string() == conversation_id,
    )).all()
    return {
        message_id
        for payload in payloads if isinstance(payload, dict)
        for message_id in (payload.get("message_ids") if isinstance(payload.get("message_ids"), list) else [])
        if isinstance(message_id, str)
    }


def serialize_message(session: Session, message: Message, *, pending_decision_ids: set[str] | None = None) -> dict:
    if pending_decision_ids is None:
        pending_decision_ids = pending_asset_decision_message_ids(session, message.conversation_id)
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
        "asset_decision_pending": message.id in pending_decision_ids,
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
