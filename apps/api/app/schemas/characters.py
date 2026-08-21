from pydantic import BaseModel, Field, field_validator

DEFAULT_FORBIDDEN_RULES = [
    "다른 캐릭터의 대사를 대신 쓰지 않는다.",
    "사용자의 행동이나 감정을 대신 결정하지 않는다.",
]

DEFAULT_TRAIT_SCORES = {
    "confidence": 3,
    "kindness": 3,
    "jealousy": 3,
    "eros": 2,
    "aggression": 2,
    "playfulness": 3,
    "shyness": 2,
    "initiative": 3,
}


def normalize_trait_scores(value: dict[str, int] | None) -> dict[str, int]:
    if value is None:
        return DEFAULT_TRAIT_SCORES.copy()
    normalized = DEFAULT_TRAIT_SCORES.copy()
    for key, score in value.items():
        if not isinstance(score, int) or score < 1 or score > 5:
            raise ValueError("trait_scores values must be integers from 1 to 5")
        normalized[key] = score
    return normalized


class CharacterCreate(BaseModel):
    name: str = Field(min_length=1)
    persona: str = Field(min_length=1)
    description: str | None = None
    appearance: str | None = None
    behavior_style: str | None = None
    speech_style: str | None = None
    emotional_rules: list[str] = Field(default_factory=list)
    forbidden_rules: list[str] = Field(default_factory=lambda: DEFAULT_FORBIDDEN_RULES.copy())
    default_model: str | None = None
    avatar_url: str | None = None
    trait_scores: dict[str, int] = Field(default_factory=lambda: DEFAULT_TRAIT_SCORES.copy())
    tts_provider: str = "supertonic"
    tts_model: str = "supertonic-3"
    tts_voice_style: str = "F1"
    tts_sample_text: str | None = None

    @field_validator("trait_scores", mode="before")
    @classmethod
    def validate_trait_scores(cls, value):
        return normalize_trait_scores(value)


class CharacterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    persona: str | None = Field(default=None, min_length=1)
    description: str | None = None
    appearance: str | None = None
    behavior_style: str | None = None
    speech_style: str | None = None
    emotional_rules: list[str] | None = None
    forbidden_rules: list[str] | None = None
    default_model: str | None = None
    avatar_url: str | None = None
    trait_scores: dict[str, int] | None = None
    tts_provider: str | None = None
    tts_model: str | None = None
    tts_voice_style: str | None = None
    tts_sample_text: str | None = None

    @field_validator("trait_scores", mode="before")
    @classmethod
    def validate_trait_scores(cls, value):
        if value is None:
            return None
        return normalize_trait_scores(value)


class CharacterRead(BaseModel):
    id: str
    name: str
    persona: str
    description: str | None = None
    appearance: str | None = None
    behavior_style: str | None = None
    speech_style: str | None = None
    emotional_rules: list[str]
    forbidden_rules: list[str]
    default_model: str | None = None
    avatar_url: str | None = None
    trait_scores: dict[str, int] = Field(default_factory=lambda: DEFAULT_TRAIT_SCORES.copy())
    tts_provider: str = "supertonic"
    tts_model: str = "supertonic-3"
    tts_voice_style: str = "F1"
    tts_sample_text: str | None = None


class CharacterUsageRead(CharacterRead):
    message_count: int = 0
    room_count: int = 0
    usage_count: int = 0
