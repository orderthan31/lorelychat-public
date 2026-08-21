from pydantic import BaseModel, Field


class PresetCreate(BaseModel):
    preset_type: str = Field(pattern="^(room_seed|speech_style|compression_focus|room_template)$")
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    description: str | None = None
    enabled: bool = True


class PresetUpdate(BaseModel):
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
