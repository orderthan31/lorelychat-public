from dataclasses import dataclass
from typing import Any, Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCOPES = frozenset({'lore:read', 'lore:draft', 'lore:commit', 'lore:image:generate'})
CHARACTER_FIELDS = frozenset({'name','persona','description','appearance','behavior_style','speech_style','emotional_rules','forbidden_rules','trait_scores'})
WORLD_FIELDS = frozenset({'title','description','genre_mode','location','mood','world_seed','opening_scene','opening_line','tone_preset','relationship_archetype','compression_focus','tags','enabled'})

@dataclass(frozen=True)
class Principal:
    id: str
    scopes: frozenset[str] = frozenset()
    is_owner: bool = False

class AgentError(ValueError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')]
class Empty(StrictModel):
    pass
class SettingsUpdate(StrictModel):
    enabled: bool = False
    llm_model_option_key: str | None = Field(default=None, max_length=256)
    image_provider_account_id: Identifier | None = None
    image_model_id: str | None = Field(default=None, max_length=256)
    mcp_enabled: bool = False
class RequestInput(StrictModel):
    request_id: Identifier
class ContextSelection(StrictModel):
    kind: Literal['character','world']
    id: Identifier
    fields: list[str] = Field(min_length=1, max_length=14)
    @model_validator(mode='after')
    def allowed_fields(self):
        allowed = CHARACTER_FIELDS if self.kind == 'character' else WORLD_FIELDS
        if not set(self.fields).issubset(allowed):
            raise ValueError('Unsupported context fields')
        return self
class MessageInput(RequestInput):
    content: str = Field(min_length=1, max_length=16000)
    context: list[ContextSelection] = Field(default_factory=list, max_length=8)
class SearchInput(StrictModel):
    query: str = Field(default='', max_length=200)
    kind: Literal['character','world','all'] = 'all'
    limit: int = Field(default=10, ge=1, le=20)
class EntityInput(StrictModel):
    id: Identifier
    fields: list[str] | None = Field(default=None, max_length=14)
class NavigationInput(StrictModel):
    kind: Literal['character','world','characters','worlds','settings','home']
    id: Identifier | None = None
class StageInput(StrictModel):
    target_id: Identifier | None = None
    fields: dict[str, Any]
class DraftInput(StrictModel):
    draft_id: Identifier
class ReviseBody(StrictModel):
    expected_version: int = Field(ge=1)
    fields: dict[str, Any]
class ReviseInput(ReviseBody):
    draft_id: Identifier
class ApprovalBody(StrictModel):
    expected_version: int = Field(ge=1)
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
class CommitBody(ApprovalBody):
    request_id: Identifier
class CommitFormBody(CommitBody):
    # Trusted native editor only; deliberately absent from TOOL_SCHEMAS.
    manual_fields: dict[str, Any]
    baseline_fields: dict[str, Any]
class CommitInput(CommitBody):
    draft_id: Identifier
class ClientInput(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[Literal['lore:read','lore:draft','lore:commit','lore:image:generate']] = Field(min_length=1, max_length=4)
class ImagePlanInput(StrictModel):
    target_id: Identifier
    target_kind: Literal['character','world'] = 'character'
    prompt: str = Field(min_length=1, max_length=8000)
    count: int = Field(default=1, ge=1, le=4)
    options: dict[str, Any] = Field(default_factory=dict)
    reference_output_id: Identifier | None = None
class ImageContextInput(StrictModel):
    target_id: Identifier
    target_kind: Literal['character','world'] = 'character'
class NewImageChoiceInput(StrictModel):
    expected_version: int = Field(ge=1)
    generation_mode: Literal['new','reference']
    reference_id: Identifier | None = None
    @model_validator(mode='after')
    def reference_choice(self):
        if (self.generation_mode == 'reference') != (self.reference_id is not None):
            raise ValueError('Reference mode requires one reference; new mode takes none')
        return self
class ImageJobInput(StrictModel):
    job_id: Identifier
class RegistrationBody(RequestInput):
    output_ids: list[Identifier] = Field(min_length=1, max_length=4)
    target_id: Identifier
    target_kind: Literal['character','world'] = 'character'
    metadata: dict[str, Any] = Field(default_factory=dict)
    @model_validator(mode='after')
    def world_thumbnail_selection(self):
        if self.target_kind == 'world' and (len(self.output_ids) != 1 or self.metadata):
            raise ValueError('World thumbnail registration requires one output and no asset metadata')
        return self
class RegistrationInput(RegistrationBody):
    job_id: Identifier

class AgentDraftView(StrictModel):
    id: str
    session_id: str | None
    kind: Literal['character','world','image_generation','image_registration']
    target_id: str | None
    version: int
    base_revision: int | None
    before: dict[str, Any] | None
    after: dict[str, Any]
    changes: dict[str, Any]
    preview_hash: str
    status: Literal['draft','approved','committed','conflict']
    warnings: list[str]
    result: dict[str, Any] | None
class AgentMessage(StrictModel):
    id: str
    role: Literal['user','assistant']
    content: str
class AgentSessionView(StrictModel):
    id: str
    title: str
    messages: list[AgentMessage]
    drafts: list[AgentDraftView]
    busy: bool
    last_error: str | None
class OperationProgress(StrictModel):
    stage: Literal['model','search','read','draft','review','image_context','image_plan','navigation']
    sequence: int = Field(ge=1)
    round: int | None = Field(default=None, ge=1)
class OperationView(StrictModel):
    request_id: str
    status: Literal['completed','running','unknown','failed']
    kind: str
    result: dict[str, Any] | None
    error: str | None
    progress: OperationProgress | None = None
class ModelChoice(StrictModel):
    key: str
    label: str
    provider_account_id: str
    provider_type: str
class ImageAccount(StrictModel):
    id: str
    alias: str
    provider_type: str
class ImageProviderAccount(ImageAccount):
    image_model_ids: list[str] = Field(default_factory=list)
class ImageChoice(StrictModel):
    id: str
    label: str
    provider_type: str
    supports_reference: bool
    max_count: int = Field(default=4, strict=True, ge=1, le=4)
    deprecated: bool = False
    shutdown_date: str | None = None
    admission_cutoff_date: str | None = None
    vertex_locations: list[str] | None = None
    reference_unavailable_reason: str | None = None
class AgentSettingsView(SettingsUpdate):
    llm_options: list[ModelChoice]
    llm_accounts: list[ImageAccount] = Field(default_factory=list)
    image_accounts: list[ImageProviderAccount]
    image_models: list[ImageChoice]
class ClientView(StrictModel):
    id: str
    name: str
    scopes: list[str]
    enabled: bool
class ImageOutputView(StrictModel):
    id: str
    preview_url: str
class ImageJobView(StrictModel):
    id: str
    status: Literal['queued','running','completed','unknown','failed']
    outputs: list[ImageOutputView]
    error: str | None
    plan: dict[str, Any] = Field(default_factory=dict)

TOOL_SCHEMAS = {
    'lore_capabilities': Empty, 'lore_search': SearchInput,
    'lore_get_character': EntityInput, 'lore_get_world': EntityInput,
    'lore_navigation_target': NavigationInput,
    'lore_stage_character': StageInput, 'lore_stage_world': StageInput,
    'lore_revise_draft': ReviseInput, 'lore_preview_draft': DraftInput,
    'lore_request_approval': DraftInput, 'lore_commit_draft': CommitInput,
    'lore_get_operation': RequestInput, 'lore_plan_image': ImagePlanInput,
    'lore_image_context': ImageContextInput,
    'lore_generate_image': CommitInput, 'lore_get_image_job': ImageJobInput,
    'lore_stage_image_registration': RegistrationInput,
}
