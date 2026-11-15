export type AgentSettings = {
  enabled: boolean; llm_model_option_key: string | null; image_provider_account_id: string | null;
  image_model_id: string | null; mcp_enabled: boolean;
  llm_accounts?: { id: string; alias: string; provider_type: string }[];
  llm_options: { key: string; label: string; provider_account_id: string; provider_type: string }[];
  image_accounts: { id: string; alias: string; provider_type: string; image_model_ids?: string[] }[];
  image_models: { id: string; label: string; provider_type: string; supports_reference: boolean; max_count?: number; deprecated?: boolean; shutdown_date?: string | null; reference_unavailable_reason?: string }[];
};
export type AgentSettingsInput = Pick<AgentSettings, 'enabled' | 'llm_model_option_key' | 'image_provider_account_id' | 'image_model_id' | 'mcp_enabled'>;
export type ContextSelection = { kind: 'character' | 'world'; id: string; fields: string[] };
export type AgentDraft = {
  id: string; session_id: string | null; kind: 'character' | 'world' | 'image_generation' | 'image_registration';
  target_id: string | null; version: number; base_revision: number | null;
  before: Record<string, unknown> | null; after: Record<string, unknown>; changes: Record<string, unknown>;
  preview_hash: string; status: 'draft' | 'approved' | 'committed' | 'conflict'; warnings: string[]; result: Record<string, unknown> | null;
};
export type AgentSession = { id: string; title: string; messages: { id: string; role: 'user' | 'assistant'; content: string }[]; drafts: AgentDraft[]; busy: boolean; last_error: string | null };
export type AgentProgress = { stage: 'model' | 'search' | 'read' | 'draft' | 'review' | 'image_context' | 'image_plan' | 'navigation'; sequence: number; round?: number | null };
export type AgentOperation = { progress?: AgentProgress | null; request_id: string; status: 'completed' | 'running' | 'unknown' | 'failed'; kind: string; result: Record<string, unknown> | null; error: string | null };
export type ImageCandidate = { id: string; label: string; kind: string; is_default: boolean; available: boolean; reason?: string };
export type ImageContext = { has_existing_images: boolean; requires_choice: boolean; candidates: ImageCandidate[]; inventory_digest: string };
export type ImageChoice = { version: number; mode: 'new' | 'reference' | ''; referenceId: string };
export type ImageTargetKind = 'character' | 'world';
export type ImageSelection = { selected: string[]; target: string; targetKind: ImageTargetKind; label: string };
export type ImageJob = { id: string; plan?: { target_kind?: ImageTargetKind; target_id?: string | null }; status: 'queued' | 'running' | 'completed' | 'unknown' | 'failed'; outputs: { id: string; preview_url: string }[]; error: string | null };
export const MCP_SCOPES = ['lore:read', 'lore:draft', 'lore:commit', 'lore:image:generate'] as const;
export type McpScope = typeof MCP_SCOPES[number];
export type McpClient = { id: string; name: string; scopes: McpScope[]; enabled: boolean };
export type PendingRequest = { request_id: string; kind: 'session' | 'message' | 'commit' | 'registration'; session_id?: string; draft_id?: string; image_job_id?: string };
