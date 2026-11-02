from app.schemas.input_policy import RequestModel
from pydantic import BaseModel, Field


class PresetCreate(RequestModel):
    nonnull_fields = frozenset(['preset_type', 'title', 'content', 'enabled'])
    nonblank_fields = frozenset(['title', 'content'])
    preset_type: str = Field(pattern="^(room_seed|speech_style|compression_focus|room_template)$")
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    description: str | None = None
    enabled: bool = True


class PresetUpdate(RequestModel):
    nonnull_fields = frozenset(['preset_type', 'title', 'content', 'enabled'])
    nonblank_fields = frozenset(['title', 'content'])
    preset_type: str | None = Field(default=None, pattern="^(room_seed|speech_style|compression_focus|room_template)$")
    title: str | None = Field(default=None, min_length=1)
    content: str | None = Field(default=None, min_length=1)
    description: str | None = None
    enabled: bool | None = None


class PresetRead(BaseModel):
    id: str
    preset_type: str
    title: str
    content: str
    description: str | None = None
    enabled: bool
