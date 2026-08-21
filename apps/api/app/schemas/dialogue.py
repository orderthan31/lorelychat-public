from typing import Literal

from pydantic import BaseModel, Field


class CharacterReply(BaseModel):
    character_id: str | None = None
    reply_type: Literal["character", "storytelling"] = "character"
    text: str = Field(min_length=1)
    emotion: str | None = None
    action: str | None = None
    thought: str | None = None


class MultiCharacterReply(BaseModel):
    replies: list[CharacterReply]
