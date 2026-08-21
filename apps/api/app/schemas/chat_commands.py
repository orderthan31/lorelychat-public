from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


PostprocessTarget = Literal["first_bubble", "last_bubble", "all_bubbles"]
PostprocessContextOption = Literal["scene", "world", "characters", "relationship_memory", "recent_messages", "turn_messages"]
DEFAULT_POSTPROCESS_CONTEXT_OPTIONS: list[PostprocessContextOption] = ["scene", "world", "turn_messages"]
POSTPROCESS_CONTEXT_OPTION_LABELS = {
    "scene": "현재 장면",
    "world": "세계관",
    "characters": "캐릭터",
    "relationship_memory": "관계/기억",
    "recent_messages": "최근 메시지",
    "turn_messages": "현재 턴 메시지",
}


def normalize_command_name(value: str) -> str:
    name = " ".join((value or "").strip().split())
    if name.startswith("!"):
        name = name[1:].strip()
    return name


RESERVED_COMMAND_NAMES = {"off", "OFF", "끄기", "해제", "커맨드해제", "커맨드끄기", "명령해제"}


class ChatCommandBase(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    display_name: str | None = Field(default=None, max_length=80)
    description: str = Field(default="", max_length=500)
    # Legacy compatibility field. Existing callers can still send/read prompt;
    # new UI should edit generation_prompt and postprocess_prompt separately.
    prompt: str = Field(default="", max_length=4000)
    generation_prompt: str = Field(default="", max_length=4000)
    postprocess_prompt: str = Field(default="", max_length=4000)
    postprocess_target: PostprocessTarget = "all_bubbles"
    postprocess_probability: int = Field(default=100, ge=0, le=100)
    postprocess_context_options: list[PostprocessContextOption] = Field(default_factory=lambda: list(DEFAULT_POSTPROCESS_CONTEXT_OPTIONS))
    enabled: bool = True
    priority: int = Field(default=0, ge=-1000, le=1000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        name = normalize_command_name(value)
        if not name:
            raise ValueError("커맨드명은 필수입니다")
        if any(ch.isspace() for ch in name):
            raise ValueError("커맨드명에는 공백을 넣을 수 없습니다")
        if name.startswith("/"):
            raise ValueError("커맨드명은 ! 접두 커맨드 전용입니다")
        if name in RESERVED_COMMAND_NAMES:
            raise ValueError("커맨드 해제는 채팅 입력이 아니라 대화방 상단 활성 커맨드 뱃지에서만 가능합니다")
        return name

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.strip().split()) or None

    @model_validator(mode="after")
    def require_at_least_one_prompt(self):
        if not (self.prompt or self.generation_prompt or self.postprocess_prompt):
            raise ValueError("생성 프롬프트나 후처리 프롬프트 중 하나는 필요합니다")
        if not self.generation_prompt and self.prompt:
            self.generation_prompt = self.prompt
        if not self.postprocess_prompt and self.prompt:
            self.postprocess_prompt = self.prompt
        if not self.prompt:
            self.prompt = self.postprocess_prompt or self.generation_prompt
        return self
    @field_validator("postprocess_context_options")
    @classmethod
    def normalize_postprocess_context_options(cls, value: list[PostprocessContextOption] | None) -> list[PostprocessContextOption]:
        if not value:
            return []
        allowed = set(POSTPROCESS_CONTEXT_OPTION_LABELS)
        deduped = []
        for item in value:
            if item in allowed and item not in deduped:
                deduped.append(item)
        return deduped


class ChatCommandCreate(ChatCommandBase):
    pass


class ChatCommandUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    display_name: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    prompt: str | None = Field(default=None, max_length=4000)
    generation_prompt: str | None = Field(default=None, max_length=4000)
    postprocess_prompt: str | None = Field(default=None, max_length=4000)
    postprocess_target: PostprocessTarget | None = None
    postprocess_probability: int | None = Field(default=None, ge=0, le=100)
    postprocess_context_options: list[PostprocessContextOption] | None = None
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=-1000, le=1000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return ChatCommandBase.validate_name(value)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        return ChatCommandBase.normalize_display_name(value)

    @field_validator("postprocess_context_options")
    @classmethod
    def normalize_postprocess_context_options(cls, value: list[PostprocessContextOption] | None) -> list[PostprocessContextOption] | None:
        if value is None:
            return None
        return ChatCommandBase.normalize_postprocess_context_options(value)


class ChatCommandRead(BaseModel):
    id: str
    name: str
    display_name: str
    description: str
    prompt: str
    generation_prompt: str
    postprocess_prompt: str
    postprocess_target: PostprocessTarget
    postprocess_probability: int
    postprocess_context_options: list[PostprocessContextOption] = Field(default_factory=list)
    enabled: bool
    priority: int
    created_at: datetime
    updated_at: datetime
