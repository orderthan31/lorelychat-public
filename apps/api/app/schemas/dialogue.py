from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CharacterReply(BaseModel):
    character_id: str | None = None
    reply_type: Literal["character", "storytelling"] = "character"
    text: str
    emotion: str | None = None
    action: str | None = None
    thought: str | None = None
    response_protocol: Literal["legacy_json", "free_body_json", "tagged_text"] = Field(default="legacy_json", exclude=True)
    raw_body: str | None = Field(default=None, exclude=True)
    render_parts: list[dict[str, str]] = Field(default_factory=list, exclude=True)

    @model_validator(mode="after")
    def require_visible_body(self):
        if self.response_protocol == "legacy_json" and not self.text:
            raise ValueError("Legacy replies require dialogue")
        if not any((self.text.strip(), (self.action or "").strip(), (self.thought or "").strip())):
            raise ValueError("Empty reply")
        return self


class MultiCharacterReply(BaseModel):
    replies: list[CharacterReply]
