from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from app.schemas.assets import MessageAssetRead


class ParticipantCreate(BaseModel):
    type: str
    id: str
    role: str | None = None
    order_index: int | None = None


class SceneCreate(BaseModel):
    location: str | None = None
    time_label: str | None = None
    mood: str | None = None
    world_seed: str | None = None
    opening_scene: str | None = None
    opening_line: str | None = None
    tone_preset: str | None = None
    relationship_archetype: str | None = None
    current_conflict: str | None = None
    compression_focus: str | None = None
    user_description: str | None = None


class ConversationCreate(BaseModel):
    mode: str
    genre_mode: str = "battle"
    world_setting_id: str | None = None
    title: str | None = None
    thumbnail_url: str | None = None
    participants: list[ParticipantCreate] = Field(default_factory=list)
    scene: SceneCreate | None = None
    created_by: str | None = None
    tts_enabled: bool = False


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    thumbnail_url: str | None = None
    genre_mode: str | None = None
    world_setting_id: str | None = None
    participants: list[ParticipantCreate] | None = None
    scene: SceneCreate | None = None
    tts_enabled: bool | None = None


class ConversationRead(BaseModel):
    id: str
    title: str | None
    thumbnail_url: str | None = None
    mode: str
    genre_mode: str = "battle"
    world_setting_id: str | None = None
    auto_mode: bool
    created_at: datetime
    updated_at: datetime
    tts_enabled: bool = False
    active_command_id: str | None = None
    active_command_ids: list[str] = Field(default_factory=list)
    participants: list[dict] = Field(default_factory=list)
    last_message_id: str | None = None
    last_message_at: datetime | None = None
    has_unread: bool = False
    unread_count: int = 0


class ConversationReadMarker(BaseModel):
    conversation_id: str
    user_id: str
    last_read_message_id: str | None = None
    last_read_at: datetime


class ParticipantRead(BaseModel):
    type: str
    id: str
    role: str | None = None
    order_index: int | None = None


class ParticipantUpdate(BaseModel):
    role: str | None = None


class BattleControl(BaseModel):
    action: Literal["start", "progress", "end", "cancel"]
    match_id: str | None = None
    participant_a_id: str | None = None
    participant_b_id: str | None = None
    favored_character_id: str | None = None
    advantage: float | None = Field(default=None, ge=0, le=1)
    winner_id: str | None = None
    current_phase: str | None = None
    process_summary: str | None = None
    decisive_moment: str | None = None


class MessageCreate(BaseModel):
    speaker_type: str
    speaker_id: str
    content: str = ""
    action: str | None = None
    thought: str | None = None
    metadata: dict = Field(default_factory=dict)
    battle_control: BattleControl | None = None


class MessageUpdate(BaseModel):
    content: str | None = Field(default=None, max_length=8000)
    emotion: str | None = Field(default=None, max_length=120)
    action: str | None = Field(default=None, max_length=4000)
    thought: str | None = Field(default=None, max_length=4000)


class MessageRead(BaseModel):
    id: str
    conversation_id: str
    speaker_type: str
    speaker_id: str
    content: str
    emotion: str | None = None
    action: str | None = None
    thought: str | None = None
    metadata: dict = Field(default_factory=dict)
    assets: list[MessageAssetRead] = Field(default_factory=list)
    tts_audio_url: str | None = None
    tts_text: str | None = None


class MessageGenerationJobRead(BaseModel):
    id: str
    conversation_id: str
    incoming_message_id: str
    status: str
    error_message: str | None = None
    error_code: str | None = None
    generated_message_ids: list[str] = Field(default_factory=list)
    attempt_count: int = 0
    state_version: int = 0
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class MessageGenerationJobCreateRead(BaseModel):
    job: MessageGenerationJobRead
    incoming_message: MessageRead


class MessageBulkDeleteRequest(BaseModel):
    message_ids: list[str] = Field(min_length=1, max_length=100)


class MessageBulkDeleteRead(BaseModel):
    deleted_ids: list[str] = Field(default_factory=list)
    missing_ids: list[str] = Field(default_factory=list)


class SceneStateRead(BaseModel):
    location: str | None = None
    time_label: str | None = None
    mood: str | None = None
    world_seed: str | None = None
    opening_scene: str | None = None
    opening_line: str | None = None
    tone_preset: str | None = None
    relationship_archetype: str | None = None
    current_conflict: str | None = None
    compression_focus: str | None = None
    last_event: str | None = None
    tension_level: int = 0
    romance_level: int = 0
    user_description: str | None = None
    summary: str | None = None
    last_compression_error: str | None = None
    last_compression_attempt_at: datetime | None = None
    last_compressed_at: datetime | None = None
    last_compression_source_message_id: str | None = None
    compression_revision: int = 0


class SceneSummaryUpdate(BaseModel):
    summary: str = Field(min_length=1, max_length=2200)
    expected_revision: int = Field(ge=0)


class CharacterMemoryCreate(BaseModel):
    character_id: str = "__room__"
    memory_type: str = "user_note"
    content: str = Field(min_length=1, max_length=500)
    importance: int = Field(default=5, ge=1, le=5)


class CharacterMemoryUpdate(BaseModel):
    character_id: str | None = None
    memory_type: str | None = None
    content: str | None = Field(default=None, min_length=1, max_length=500)
    importance: int | None = Field(default=None, ge=1, le=5)


class CharacterMemoryRead(BaseModel):
    id: str
    character_id: str
    memory_type: str
    content: str
    importance: int


class RelationshipStateRead(BaseModel):
    character_id: str
    counterpart_type: str
    counterpart_id: str
    trust_level: int
    affinity_level: int
    tension_level: int
    conflict_level: int
    cooperation_level: int
    current_mood: str | None = None
    current_dynamic: str | None = None
    relationship_fact: str | None = None
    unresolved_hooks: list[str] = Field(default_factory=list)


class ConversationContextRead(BaseModel):
    scene: SceneStateRead | None = None
    relationships: list[RelationshipStateRead] = Field(default_factory=list)
    memories: list[CharacterMemoryRead] = Field(default_factory=list)


class UsageBucketRead(BaseModel):
    provider: str
    model: str
    purpose: str
    calls: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_calls: int = 0


class ConversationUsageRead(BaseModel):
    conversation_id: str | None = None
    total_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    buckets: list[UsageBucketRead] = Field(default_factory=list)


class ContextPreviewSectionRead(BaseModel):
    key: str
    title: str
    char_count: int
    approx_tokens: int
    used_tokens: int = 0
    included: bool = True
    content: str
    source: str = ""
    included_reason: str = ""
    budget_tokens: int = 0
    excluded_reason: str = ""


class ConversationContextPreviewRead(BaseModel):
    conversation_id: str
    model_key: str | None = None
    compression_model_key: str | None = None
    response_length_preset: str | None = None
    recent_message_count: int
    selected_recent_message_count: int
    total_budget_tokens: int = 0
    used_tokens: int = 0
    sections: list[ContextPreviewSectionRead] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ConversationCompressionPreviewRead(BaseModel):
    conversation_id: str
    recent_message_count: int
    selected_recent_message_count: int
    total_budget_tokens: int
    used_tokens: int
    pipeline_steps: list[str] = Field(default_factory=list)
    sections: list[ContextPreviewSectionRead] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MessageRegenerateRequest(BaseModel):
    mode: str = "after_message"
    replace_existing: bool = False
