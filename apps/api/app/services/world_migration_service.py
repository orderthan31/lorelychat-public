from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Session, select

from app.db.models import Conversation, SceneState, WorldSetting
from app.services.conversation_service import normalize_genre_mode


def _clean(value: str | None) -> str | None:
    text = (value or '').strip()
    return text or None


def clone_missing_conversation_world_settings(session: Session) -> int:
    """Clone legacy per-room world fields into WorldSetting rows.

    Existing rooms created before world settings had scene/location/mood/world_seed
    directly on SceneState and genre_mode on Conversation.  For the new product
    model each room owns a cloned WorldSetting and references it through
    Conversation.world_setting_id.  This migration is idempotent: rooms that
    already reference a world setting are left untouched.
    """
    conversations = list(session.exec(select(Conversation)).all())
    created = 0
    now = datetime.now(timezone.utc)
    for conversation in conversations:
        if getattr(conversation, 'world_setting_id', None):
            continue
        scene = session.get(SceneState, conversation.id)
        title = (conversation.title or conversation.id or '대화방').strip()
        world = WorldSetting(
            id=f"world_{uuid4().hex[:12]}",
            title=f"{title} 세계관",
            thumbnail_url=_clean(getattr(conversation, 'thumbnail_url', None)),
            description=f"기존 대화방 '{title}' 설정에서 자동 클론됨",
            genre_mode=normalize_genre_mode(conversation.genre_mode),
            location=_clean(getattr(scene, 'location', None)),
            mood=_clean(getattr(scene, 'mood', None)),
            world_seed=_clean(getattr(scene, 'world_seed', None)),
            opening_scene=_clean(getattr(scene, 'opening_scene', None)),
            opening_line=_clean(getattr(scene, 'opening_line', None)),
            tone_preset=_clean(getattr(scene, 'tone_preset', None)),
            relationship_archetype=_clean(getattr(scene, 'relationship_archetype', None)),
            compression_focus=_clean(getattr(scene, 'compression_focus', None)),
            tags=['legacy-room-clone'],
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        session.add(world)
        conversation.world_setting_id = world.id
        conversation.updated_at = now
        session.add(conversation)
        created += 1
    if created:
        session.commit()
    return created
