from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from sqlmodel import Field, SQLModel, Column, JSON
from sqlalchemy import UniqueConstraint


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Character(SQLModel, table=True):
    __tablename__ = "characters"

    id: str = Field(primary_key=True)
    name: str
    description: Optional[str] = None
    persona: str
    appearance: Optional[str] = None
    behavior_style: Optional[str] = None
    speech_style: Optional[str] = None
    emotional_rules: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    forbidden_rules: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    default_model: Optional[str] = None
    avatar_url: Optional[str] = None
    tts_provider: str = Field(default="supertonic")
    tts_model: str = Field(default="supertonic-3")
    tts_voice_style: str = Field(default="F1")
    tts_sample_text: Optional[str] = None
    trait_scores: dict[str, int] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WorldSetting(SQLModel, table=True):
    __tablename__ = "world_settings"

    id: str = Field(primary_key=True)
    title: str = Field(index=True)
    thumbnail_url: Optional[str] = None
    description: Optional[str] = None
    genre_mode: str = Field(default="battle", index=True)
    location: Optional[str] = None
    mood: Optional[str] = None
    world_seed: Optional[str] = None
    opening_scene: Optional[str] = None
    opening_line: Optional[str] = None
    tone_preset: Optional[str] = None
    relationship_archetype: Optional[str] = None
    compression_focus: Optional[str] = None
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: str = Field(primary_key=True)
    title: Optional[str] = None
    thumbnail_url: Optional[str] = None
    world_setting_id: Optional[str] = Field(default=None, index=True)
    mode: str
    genre_mode: str = Field(default="battle")
    auto_mode: bool = False
    tts_enabled: bool = False
    tts_provider: str = Field(default="supertonic")
    tts_model: str = Field(default="supertonic-3")
    tts_voice_style: str = Field(default="F1")
    active_command_id: Optional[str] = Field(default=None, index=True)
    active_command_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ConversationParticipant(SQLModel, table=True):
    __tablename__ = "conversation_participants"

    conversation_id: str = Field(primary_key=True)
    participant_type: str = Field(primary_key=True)
    participant_id: str = Field(primary_key=True)
    role: Optional[str] = None
    order_index: Optional[int] = None


class ConversationReadState(SQLModel, table=True):
    __tablename__ = "conversation_read_states"

    conversation_id: str = Field(primary_key=True)
    user_id: str = Field(primary_key=True)
    last_read_message_id: Optional[str] = Field(default=None, index=True)
    last_read_at: datetime = Field(default_factory=utc_now, index=True)


class MessageGenerationJob(SQLModel, table=True):
    __tablename__ = "message_generation_jobs"

    id: str = Field(primary_key=True)
    conversation_id: str = Field(index=True)
    incoming_message_id: str = Field(index=True)
    status: str = Field(default="queued", index=True)
    error_message: Optional[str] = None
    generated_message_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    lease_owner: Optional[str] = Field(default=None, index=True)
    lease_expires_at: Optional[datetime] = Field(default=None, index=True)
    heartbeat_at: Optional[datetime] = Field(default=None, index=True)
    attempt_count: int = Field(default=0)
    cancel_requested_at: Optional[datetime] = Field(default=None, index=True)
    state_version: int = Field(default=0)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
    completed_at: Optional[datetime] = None


class GenerationJobEvent(SQLModel, table=True):
    __tablename__ = "generation_job_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(index=True)
    conversation_id: str = Field(index=True)
    status: str = Field(index=True)
    state_version: int = Field(default=0, index=True)
    payload_: dict = Field(default_factory=dict, sa_column=Column("payload", JSON))
    created_at: datetime = Field(default_factory=utc_now, index=True)


class PostCommitTask(SQLModel, table=True):
    __tablename__ = "post_commit_tasks"

    id: str = Field(primary_key=True)
    unique_key: str = Field(index=True, unique=True)
    task_type: str = Field(index=True)
    status: str = Field(default="queued", index=True)
    payload_: dict = Field(default_factory=dict, sa_column=Column("payload", JSON))
    attempt_count: int = Field(default=0)
    lease_owner: Optional[str] = Field(default=None, index=True)
    lease_expires_at: Optional[datetime] = Field(default=None, index=True)
    available_at: datetime = Field(default_factory=utc_now, index=True)
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
    completed_at: Optional[datetime] = None


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: str = Field(primary_key=True)
    conversation_id: str = Field(index=True)
    speaker_type: str
    speaker_id: str
    content: str
    emotion: Optional[str] = None
    action: Optional[str] = None
    thought: Optional[str] = None
    generation_job_id: Optional[str] = Field(default=None, index=True)
    reply_index: Optional[int] = None
    metadata_: dict = Field(default_factory=dict, sa_column=Column("metadata", JSON))
    created_at: datetime = Field(default_factory=utc_now)


class ChatCommand(SQLModel, table=True):
    __tablename__ = "chat_commands"

    id: str = Field(primary_key=True)
    name: str = Field(index=True, unique=True)
    display_name: str
    description: str = ""
    prompt: str
    generation_prompt: str = ""
    postprocess_prompt: str = ""
    postprocess_target: str = "all_bubbles"
    postprocess_probability: int = 100
    postprocess_context_options: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    enabled: bool = True
    priority: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CharacterAsset(SQLModel, table=True):
    __tablename__ = "character_assets"

    id: str = Field(primary_key=True)
    character_id: str = Field(index=True)
    asset_type: str = Field(default="image", index=True)
    label: str
    description: Optional[str] = None
    image_url: str
    thumbnail_url: Optional[str] = None
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    mood_tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    scene_tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    outfit_tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    pose_tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    expression_tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    priority: int = 50
    enabled: bool = True
    is_default: bool = False
    source: Optional[str] = None
    generation_prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    metadata_: dict = Field(default_factory=dict, sa_column=Column("metadata", JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MessageAsset(SQLModel, table=True):
    __tablename__ = "message_assets"
    __table_args__ = (UniqueConstraint("message_id", "asset_id", name="uq_message_assets_message_asset"),)

    id: str = Field(primary_key=True)
    message_id: str = Field(index=True)
    asset_id: str = Field(index=True)
    display_order: int = 0
    created_at: datetime = Field(default_factory=utc_now)


class SceneState(SQLModel, table=True):
    __tablename__ = "scene_states"

    conversation_id: str = Field(primary_key=True)
    location: Optional[str] = None
    time_label: Optional[str] = None
    mood: Optional[str] = None
    world_seed: Optional[str] = None
    opening_scene: Optional[str] = None
    opening_line: Optional[str] = None
    tone_preset: Optional[str] = None
    relationship_archetype: Optional[str] = None
    current_conflict: Optional[str] = None
    compression_focus: Optional[str] = None
    last_event: Optional[str] = None
    tension_level: int = 0
    romance_level: int = 0
    user_description: Optional[str] = None
    summary: Optional[str] = None
    last_compression_error: Optional[str] = None
    last_compression_attempt_at: Optional[datetime] = Field(default=None, index=True)
    last_compressed_at: Optional[datetime] = Field(default=None, index=True)
    last_compression_source_message_id: Optional[str] = Field(default=None, index=True)
    compression_revision: int = Field(default=0)
    updated_at: datetime = Field(default_factory=utc_now)


class CharacterMemory(SQLModel, table=True):
    __tablename__ = "character_memories"

    id: str = Field(primary_key=True)
    conversation_id: str = Field(index=True)
    character_id: str = Field(index=True)
    memory_type: str
    content: str
    importance: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BattleMatchRecord(SQLModel, table=True):
    __tablename__ = "battle_match_records"

    id: str = Field(primary_key=True)
    conversation_id: str = Field(index=True)
    genre_mode: str = Field(default="battle", index=True)
    matchup_key: str = Field(index=True)
    participant_a_id: str = Field(index=True)
    participant_b_id: str = Field(index=True)
    winner_id: Optional[str] = Field(default=None, index=True)
    loser_id: Optional[str] = Field(default=None, index=True)
    result_status: str = Field(default="completed", index=True)
    process_summary: Optional[str] = None
    decisive_moment: Optional[str] = None
    source_message_start_id: Optional[str] = None
    source_message_end_id: Optional[str] = None
    metadata_: dict = Field(default_factory=dict, sa_column=Column("metadata", JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BattleStanding(SQLModel, table=True):
    __tablename__ = "battle_standings"

    conversation_id: str = Field(primary_key=True)
    character_id: str = Field(primary_key=True)
    wins: int = 0
    losses: int = 0
    points: int = 0
    rank: Optional[int] = None
    updated_at: datetime = Field(default_factory=utc_now)


class ConversationRelationshipState(SQLModel, table=True):
    __tablename__ = "conversation_relationship_states"

    conversation_id: str = Field(primary_key=True)
    character_id: str = Field(primary_key=True)
    counterpart_type: str = Field(default="user", primary_key=True)
    counterpart_id: str = Field(default="user_001", primary_key=True)
    trust_level: int = 0
    affinity_level: int = 0
    tension_level: int = 0
    conflict_level: int = 0
    cooperation_level: int = 0
    current_mood: Optional[str] = None
    current_dynamic: Optional[str] = None
    unresolved_hooks: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=utc_now)


class RuntimeSetting(SQLModel, table=True):
    __tablename__ = "runtime_settings"

    id: str = Field(primary_key=True)  # "global" or conversation_id
    conversation_id: Optional[str] = Field(default=None, index=True)
    model_key: Optional[str] = None
    fallback_model_key: Optional[str] = None
    compression_model_key: Optional[str] = None
    compression_fallback_model_key: Optional[str] = None
    compression_strategy: str = Field(default="quality")
    response_length_preset: str = Field(default="medium")
    min_output_tokens: int = Field(default=768)
    compression_interval_turns: int = Field(default=5)
    default_tts_model_option_key: Optional[str] = None
    safety_preset: str = Field(default="medium")
    updated_at: datetime = Field(default_factory=utc_now)


class ModelProviderAccount(SQLModel, table=True):
    __tablename__ = "model_provider_accounts"

    id: str = Field(primary_key=True)
    provider_type: str = Field(index=True)
    alias: str
    enabled: bool = True
    configured: bool = False
    base_url: Optional[str] = None
    api_key_secret_ref: Optional[str] = None
    api_key_hint: Optional[str] = None
    preset: Optional[str] = None
    last_test_status: Optional[str] = None
    last_test_message: Optional[str] = None
    last_test_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deleted_at: Optional[datetime] = None


class ModelOption(SQLModel, table=True):
    __tablename__ = "model_options"

    id: str = Field(primary_key=True)
    key: str = Field(index=True, unique=True)
    provider_account_id: str = Field(index=True)
    provider_type: str = Field(index=True)
    model: str = Field(index=True)
    label: str
    enabled: bool = True
    supports_chat: bool = True
    supports_compression: bool = True
    supports_tts: bool = False
    model_family: str = Field(default="custom", index=True)
    supports_json: bool = True
    source: str = Field(default="manual", index=True)
    last_test_status: Optional[str] = None
    last_test_message: Optional[str] = None
    last_test_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deleted_at: Optional[datetime] = None


class LLMUsageEvent(SQLModel, table=True):
    __tablename__ = "llm_usage_events"

    id: str = Field(primary_key=True)
    provider: str = Field(index=True)
    model: str = Field(index=True)
    purpose: str = Field(index=True)
    conversation_id: Optional[str] = Field(default=None, index=True)
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    estimated: bool = False
    metadata_: dict = Field(default_factory=dict, sa_column=Column("metadata", JSON))
    created_at: datetime = Field(default_factory=utc_now)


class LLMPromptSnapshot(SQLModel, table=True):
    __tablename__ = "llm_prompt_snapshots"

    id: str = Field(primary_key=True)
    conversation_id: Optional[str] = Field(default=None, index=True)
    source_message_id: Optional[str] = Field(default=None, index=True)
    provider: Optional[str] = Field(default=None, index=True)
    model: Optional[str] = Field(default=None, index=True)
    purpose: str = Field(default="chat_generation", index=True)
    compiled_text: str
    messages_json: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    ledger_json: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    pipeline_steps: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    used_tokens: Optional[int] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    response_preview: Optional[str] = None
    metadata_: dict = Field(default_factory=dict, sa_column=Column("metadata", JSON))
    created_at: datetime = Field(default_factory=utc_now, index=True)


class Preset(SQLModel, table=True):
    __tablename__ = "presets"

    id: str = Field(primary_key=True)
    preset_type: str = Field(index=True)  # room_seed | speech_style | compression_focus | room_template
    title: str
    content: str
    description: Optional[str] = None
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
