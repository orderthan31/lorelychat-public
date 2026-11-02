from datetime import datetime
from app.schemas.input_policy import RequestModel
from pydantic import BaseModel, Field


class WorldSettingBase(RequestModel):
    nonnull_fields = frozenset(['title', 'genre_mode', 'enabled'])
    nonblank_fields = frozenset(['title', 'genre_mode'])
    title: str = Field(min_length=1, max_length=120)
    thumbnail_url: str | None = None
    description: str | None = None
    genre_mode: str = "battle"
    location: str | None = None
    mood: str | None = None
    world_seed: str | None = None
    opening_scene: str | None = None
    opening_line: str | None = None
    tone_preset: str | None = None
    relationship_archetype: str | None = None
    compression_focus: str | None = None
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class WorldSettingCreate(WorldSettingBase):
    pass


class WorldSettingUpdate(RequestModel):
    nonnull_fields = frozenset(['title', 'genre_mode', 'enabled', 'tags'])
    nonblank_fields = frozenset(['title', 'genre_mode'])
    title: str | None = Field(default=None, min_length=1, max_length=120)
    thumbnail_url: str | None = None
    description: str | None = None
    genre_mode: str | None = None
    location: str | None = None
    mood: str | None = None
    world_seed: str | None = None
    opening_scene: str | None = None
    opening_line: str | None = None
    tone_preset: str | None = None
    relationship_archetype: str | None = None
    compression_focus: str | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


class WorldSettingRead(WorldSettingBase):
    validate_inputs = False
    id: str
    created_at: datetime
    updated_at: datetime
