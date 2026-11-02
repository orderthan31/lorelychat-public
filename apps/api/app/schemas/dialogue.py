from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, model_validator


_DECODED_BODY = object()


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

    @classmethod
    def from_decoded_body(cls, **values):
        """Trusted server decoder entry; provenance is never provider input."""
        return cls.model_validate(values, context=_DECODED_BODY)

    @model_validator(mode="before")
    @classmethod
    def reserve_render_provenance(cls, data, info: ValidationInfo):
        if (isinstance(data, dict) and
                {'response_protocol', 'raw_body', 'render_parts'} & data.keys() and
                info.context is not _DECODED_BODY):
            raise ValueError('Render provenance is server-owned')
        return data

    @model_validator(mode="after")
    def require_visible_body(self):
        from app.engine.text_scanner import require_unicode_scalars
        require_unicode_scalars((self.character_id, self.text, self.emotion, self.action,
                                 self.thought, self.raw_body, self.render_parts))
        if self.response_protocol == "legacy_json" and not self.text:
            raise ValueError("Legacy replies require dialogue")
        if not any((self.text.strip(), (self.action or "").strip(), (self.thought or "").strip())):
            raise ValueError("Empty reply")
        return self


class MultiCharacterReply(BaseModel):
    replies: list[CharacterReply]
