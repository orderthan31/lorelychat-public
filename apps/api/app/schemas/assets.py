from app.schemas.input_policy import RequestModel
from pydantic import BaseModel, Field, field_validator


def clean_tags(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.replace("\n", ",").split(",")
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        raw = value
    else:
        raise ValueError("tags must be a string or a list of strings")
    if len(raw) > 1024 or any(len(item) > 2048 for item in raw):
        raise ValueError("tags exceed collection/item budget")
    return [item.strip() for item in raw if item.strip()]


class CharacterAssetBase(RequestModel):
    nonnull_fields = frozenset(['asset_type', 'label', 'image_url', 'priority', 'enabled', 'is_default'])
    nonblank_fields = frozenset(['asset_type', 'label', 'image_url'])
    asset_type: str = "image"
    label: str = Field(min_length=1)
    description: str | None = None
    image_url: str = Field(min_length=1)
    thumbnail_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    mood_tags: list[str] = Field(default_factory=list)
    scene_tags: list[str] = Field(default_factory=list)
    outfit_tags: list[str] = Field(default_factory=list)
    pose_tags: list[str] = Field(default_factory=list)
    expression_tags: list[str] = Field(default_factory=list)
    priority: int = 50
    enabled: bool = True
    is_default: bool = False
    source: str | None = None
    generation_prompt: str | None = None
    negative_prompt: str | None = None
    metadata: dict = Field(default_factory=dict)

    @field_validator("tags", "mood_tags", "scene_tags", "outfit_tags", "pose_tags", "expression_tags", mode="before")
    @classmethod
    def normalize_tags(cls, value):
        if not cls.validate_inputs:
            if value is None: return []
            if isinstance(value, list): return [str(item).strip() for item in value if str(item).strip()]
        return clean_tags(value)

    @field_validator("priority")
    @classmethod
    def clamp_priority(cls, value):
        return max(0, min(100, int(value)))


class CharacterAssetCreate(CharacterAssetBase):
    pass


class CharacterAssetUpdate(RequestModel):
    nonnull_fields = frozenset(['asset_type', 'label', 'image_url', 'priority', 'enabled', 'is_default'])
    nonblank_fields = frozenset(['asset_type', 'label', 'image_url'])
    asset_type: str | None = None
    label: str | None = Field(default=None, min_length=1)
    description: str | None = None
    image_url: str | None = Field(default=None, min_length=1)
    thumbnail_url: str | None = None
    tags: list[str] | None = None
    mood_tags: list[str] | None = None
    scene_tags: list[str] | None = None
    outfit_tags: list[str] | None = None
    pose_tags: list[str] | None = None
    expression_tags: list[str] | None = None
    priority: int | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    source: str | None = None
    generation_prompt: str | None = None
    negative_prompt: str | None = None
    metadata: dict | None = None

    @field_validator("tags", "mood_tags", "scene_tags", "outfit_tags", "pose_tags", "expression_tags", mode="before")
    @classmethod
    def normalize_tags(cls, value):
        if value is None:
            return None
        return clean_tags(value)

    @field_validator("priority")
    @classmethod
    def clamp_priority(cls, value):
        if value is None:
            return None
        return max(0, min(100, int(value)))


class CharacterAssetRead(CharacterAssetBase):
    validate_inputs = False
    id: str
    character_id: str
    metadata: dict = Field(default_factory=dict)


class MessageAssetRead(BaseModel):
    id: str
    character_id: str
    asset_type: str = "image"
    label: str
    description: str | None = None
    image_url: str
    thumbnail_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    mood_tags: list[str] = Field(default_factory=list)
    scene_tags: list[str] = Field(default_factory=list)
    outfit_tags: list[str] = Field(default_factory=list)
    pose_tags: list[str] = Field(default_factory=list)
    expression_tags: list[str] = Field(default_factory=list)
