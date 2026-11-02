import { api, ApiError } from './http';
import { FALLBACK_TTS_MODELS, FALLBACK_TTS_VOICES, MESSAGE_PAGE_TURNS } from '../constants/domain';
import type { MessageEditDraft, RuntimeSetting, RuntimeSettingUpdatePayload } from '../types/domain';

export type MessageGenerationJobRead = { id: string; conversation_id: string; incoming_message_id: string; status: 'queued' | 'running' | 'retrying' | 'completed' | 'failed' | 'cancelled'; state_version: number; generated_message_ids?: string[]; error_message?: string | null };
function readGenerationJob(value: unknown, conversationId: string, jobId?: string): MessageGenerationJobRead {
  const job = value as MessageGenerationJobRead;
  if (!job || typeof job.id !== 'string' || (jobId && job.id !== jobId) || job.conversation_id !== conversationId || typeof job.incoming_message_id !== 'string' || !['queued', 'running', 'retrying', 'completed', 'failed', 'cancelled'].includes(job.status) || !Number.isInteger(job.state_version) || job.state_version < 0 || (job.generated_message_ids !== undefined && (!Array.isArray(job.generated_message_ids) || job.generated_message_ids.some(id => typeof id !== 'string')))) throw new ApiError(200, 'Invalid generation job envelope', 'PROTOCOL_ERROR');
  return job;
}

type Id = string;
type Payload = unknown;
type SavePayload = { id?: string | null; payload: unknown };
type RegeneratePayload = { replace_existing?: boolean };

type RuntimeOption = { provider?: string; key: string; [key: string]: unknown };
export type ProviderType = 'openai' | 'google' | 'anthropic' | 'openrouter' | 'xai' | 'openai_compatible';
export type ProviderAccount = {
  id: string;
  provider_type: ProviderType;
  alias: string;
  enabled: boolean;
  configured: boolean;
  base_url?: string | null;
  api_key_status: 'missing' | 'set';
  api_key_hint?: string | null;
  preset?: string | null;
  last_test_status?: 'untested' | 'success' | 'failed' | null;
  last_test_message?: string | null;
  last_test_at?: string | null;
  active_model_count?: number;
};
export type ModelOption = {
  id: string;
  key: string;
  provider_account_id: string;
  provider_type: ProviderType;
  provider_account_alias: string;
  model: string;
  label: string;
  display_label: string;
  enabled: boolean;
  supports_chat: boolean;
  supports_compression: boolean;
  supports_tts: boolean;
  model_family: 'gemini' | 'gpt' | 'claude' | 'grok' | 'openrouter' | 'local' | 'custom';
  supports_json?: boolean;
  source: 'fetched' | 'manual' | 'preset';
  last_test_status?: 'untested' | 'success' | 'failed' | null;
  last_test_message?: string | null;
  last_test_at?: string | null;
};
type UsageQuery = { start_at?: string; end_at?: string };
export type PageQuery = { page?: number; page_size?: number; q?: string; include_disabled?: boolean };
export type PageResult<T = unknown> = { items: T[]; total: number; page: number; page_size: number; pages: number };

function pageQuery(params: PageQuery = {}) {
  const query = new URLSearchParams({ paginated: 'true' });
  if (params.page) query.set('page', String(params.page));
  if (params.page_size) query.set('page_size', String(params.page_size));
  if (params.q) query.set('q', params.q);
  if (params.include_disabled !== undefined) query.set('include_disabled', String(params.include_disabled));
  return `?${query.toString()}`;
}

function usageQuery(params?: UsageQuery) {
  const query = new URLSearchParams();
  if (params?.start_at) query.set('start_at', params.start_at);
  if (params?.end_at) query.set('end_at', params.end_at);
  const text = query.toString();
  return text ? `?${text}` : '';
}

export const healthApi = {
  check: () => api('/health'),
};

export const characterApi = {
  list: () => api<unknown[]>('/characters'),
  page: (params?: PageQuery) => api<PageResult>('/characters' + pageQuery(params)),
  usageRanking: () => api<unknown[]>('/characters/usage-ranking'),
  get: (id: Id) => api(`/characters/${id}`),
  create: (payload: Payload) => api('/characters', { method: 'POST', body: JSON.stringify(payload) }),
  update: (id: Id, payload: Payload) => api(`/characters/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  save: ({ id, payload }: SavePayload) => id ? characterApi.update(id, payload) : characterApi.create(payload),
  remove: (id: Id) => api(`/characters/${id}`, { method: 'DELETE' }),
  ttsSample: (id: Id, payload: Payload) => api(`/characters/${id}/tts-sample`, { method: 'POST', body: JSON.stringify(payload) }),
  uploadAvatar: (formData: FormData) => api('/uploads/avatars', { method: 'POST', body: formData }),
  assets: (id: Id) => api<unknown[]>(`/characters/${id}/assets`),
  createAsset: (id: Id, payload: Payload) => api(`/characters/${id}/assets`, { method: 'POST', body: JSON.stringify(payload) }),
  uploadAsset: (id: Id, formData: FormData) => api(`/characters/${id}/assets/upload`, { method: 'POST', body: formData }),
  patchAsset: (assetId: Id, patch: Payload) => api(`/character-assets/${assetId}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteAsset: (assetId: Id) => api(`/character-assets/${assetId}`, { method: 'DELETE' }),
  setDefaultAsset: (assetId: Id) => api(`/character-assets/${assetId}/set-default-avatar`, { method: 'POST' }),
};

export const conversationApi = {
  list: async () => [...((await api<unknown[]>('/conversations')) || [])].sort((a: any, b: any) => String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || ''))),
  get: (id: Id) => api(`/conversations/${id}`),
  create: (payload: Payload) => api('/conversations', { method: 'POST', body: JSON.stringify(payload) }),
  uploadThumbnail: (formData: FormData) => api('/uploads/conversation-thumbnails', { method: 'POST', body: formData }),
  update: (id: Id, payload: Payload) => api(`/conversations/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  remove: (id: Id) => api(`/conversations/${id}`, { method: 'DELETE' }),
  participants: (id: Id) => api(`/conversations/${id}/participants`),
  messages: (id: Id, beforeId?: Id | null) => api(`/conversations/${id}/messages?recent_turns=${MESSAGE_PAGE_TURNS}${beforeId ? `&before_id=${beforeId}` : ''}`),
  generateMessageTts: (messageId: Id) => api(`/messages/${messageId}/tts`, { method: 'POST', body: JSON.stringify({}) }),
  markRead: (id: Id) => api(`/conversations/${id}/read`, { method: 'POST', body: JSON.stringify({}) }),
  sendMessage: (id: Id, payload: Payload) => api(`/conversations/${id}/messages`, { method: 'POST', body: JSON.stringify(payload) }),
  createMessageJob: async (id: Id, payload: Payload) => {
    const result = await api<any>(`/conversations/${id}/messages/jobs`, { method: 'POST', body: JSON.stringify(payload) });
    const job = readGenerationJob(result?.job, id);
    if (result?.incoming_message?.id !== job.incoming_message_id) throw new ApiError(200, 'Invalid job source receipt', 'PROTOCOL_ERROR');
    return { job, incoming_message: result.incoming_message };
  },
  generationJob: async (id: Id, jobId: Id) => readGenerationJob(await api(`/conversations/${id}/generation-jobs/${jobId}`), id, jobId),
  generationReceipt: async (id: Id, requestId: string) => {
    try {
      const result = await api<any>(`/conversations/${id}/generation-jobs/by-request/${encodeURIComponent(requestId)}`);
      const job = readGenerationJob(result?.job, id);
      if (result?.incoming_message?.id !== job.incoming_message_id || result.incoming_message.conversation_id !== id || result.incoming_message.metadata?.client_request_id !== requestId) throw new ApiError(200, 'Invalid generation request receipt', 'PROTOCOL_ERROR');
      return { job, incoming_message: result.incoming_message };
    } catch (error) {
      // A generic router 404 is old-server capability absence, not a receipt.
      if (error.status === 405 || (error.status === 404 && error.detail === 'Not Found')) throw new ApiError(error.status, 'Server does not support request receipt lookup', 'RECEIPT_LOOKUP_UNSUPPORTED', error);
      throw error;
    }
  },
  activeGenerationJobs: (id: Id) => api<{ jobs: any[] }>(`/conversations/${id}/generation-jobs/active`).then(result => {
    if (!result || !Array.isArray(result.jobs) || result.jobs.some(job => !job?.id || !['queued', 'running', 'retrying'].includes(job.status))) throw new Error('Invalid active generation jobs envelope');
    result.jobs.forEach(job => readGenerationJob(job, id));
    return result;
  }).catch(error => {
    // Old backend capability absence is not a missing *known* job.
    if (error.status === 404 || error.status === 405) return { jobs: [], supported: false };
    throw error;
  }),
  retryGenerationJob: async (id: Id, jobId: Id, expectedStateVersion: number) => {
    if (!Number.isInteger(expectedStateVersion) || expectedStateVersion < 0) throw new Error('Invalid expected state version');
    return readGenerationJob(await api(`/conversations/${id}/generation-jobs/${jobId}/retry`, { method: 'POST', body: JSON.stringify({ expected_state_version: expectedStateVersion }) }), id, jobId);
  },
  regenerateMessage: (id: Id, messageId: Id, payload: RegeneratePayload = { replace_existing: false }) => api(`/conversations/${id}/messages/${messageId}/regenerate`, { method: 'POST', body: JSON.stringify(payload) }),
  updateMessage: (id: Id, messageId: Id, payload: MessageEditDraft) => api(`/conversations/${id}/messages/${messageId}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteMessage: (id: Id, messageId: Id) => api(`/conversations/${id}/messages/${messageId}`, { method: 'DELETE' }),
  bulkDeleteMessages: (id: Id, messageIds: Id[]) => api(`/conversations/${id}/messages/bulk-delete`, { method: 'POST', body: JSON.stringify({ message_ids: messageIds }) }),
  compressNow: (id: Id) => api(`/conversations/${id}/compress-now`, { method: 'POST', body: JSON.stringify({}) }),
  updateSceneSummary: (id: Id, payload: Payload) => api(`/conversations/${id}/scene-summary`, { method: 'PATCH', body: JSON.stringify(payload) }),
  createMemory: (id: Id, payload: Payload) => api(`/conversations/${id}/memories`, { method: 'POST', body: JSON.stringify(payload) }),
  updateMemory: (id: Id, memoryId: Id, payload: Payload) => api(`/conversations/${id}/memories/${memoryId}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteMemory: (id: Id, memoryId: Id) => api(`/conversations/${id}/memories/${memoryId}`, { method: 'DELETE' }),
  inviteParticipant: (id: Id, characterId: Id) => api(`/conversations/${id}/participants`, { method: 'POST', body: JSON.stringify({ type: 'character', id: characterId }) }),
  updateParticipantRole: (id: Id, characterId: Id, role: string | null) => api(`/conversations/${id}/participants/character/${characterId}`, { method: 'PATCH', body: JSON.stringify({ role }) }),
  removeCharacterParticipant: (id: Id, characterId: Id) => api(`/conversations/${id}/participants/character/${characterId}`, { method: 'DELETE' }),
  runtimeSetting: (id: Id) => api<RuntimeSetting>(`/runtime-settings/conversations/${id}`),
  saveRuntimeSetting: (id: Id, payload: RuntimeSettingUpdatePayload) => api<RuntimeSetting>(`/runtime-settings/conversations/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  context: (id: Id) => api(`/conversations/${id}/context`),
  usage: (id: Id, params?: UsageQuery) => api(`/conversations/${id}/usage${usageQuery(params)}`),
  usageSummary: (params?: UsageQuery) => api(`/conversations/usage/summary${usageQuery(params)}`),
  contextPreview: (id: Id) => api(`/conversations/${id}/context-preview`),
  battleState: (id: Id) => api(`/conversations/${id}/battle-state`),
  clearActiveCommand: (id: Id, commandId?: Id) => api(commandId ? `/conversations/${id}/active-command/${commandId}/clear` : `/conversations/${id}/active-command/clear`, { method: 'POST', body: JSON.stringify({}) }),
};

export const settingsApi = {
  runtimeDefault: () => api<RuntimeSetting>('/runtime-settings/default'),
  updateRuntimeDefault: (payload: RuntimeSettingUpdatePayload) => api<RuntimeSetting>('/runtime-settings/default', { method: 'PATCH', body: JSON.stringify(payload) }),
  ttsVoices: async () => {
    const list = await api<RuntimeOption[]>('/tts/voices');
    return list?.length ? list : FALLBACK_TTS_VOICES;
  },
  ttsModels: async () => {
    const list = await api<RuntimeOption[]>('/tts/models');
    return list?.length ? list : FALLBACK_TTS_MODELS;
  },
  presets: () => api('/presets?include_disabled=true'),
  preset: (id: Id) => api(`/presets/${id}`),
  savePreset: ({ id, payload }: SavePayload) => api(id ? `/presets/${id}` : '/presets', { method: id ? 'PATCH' : 'POST', body: JSON.stringify(payload) }),
  deletePreset: (id: Id) => api(`/presets/${id}`, { method: 'DELETE' }),

  chatCommands: () => api('/chat-commands'),
  chatCommandsPage: (params?: PageQuery) => api<PageResult>('/chat-commands' + pageQuery({ include_disabled: true, ...(params || {}) })),
  worldSettings: () => api('/world-settings?include_disabled=true'),
  worldSettingsPage: (params?: PageQuery) => api<PageResult>('/world-settings' + pageQuery({ include_disabled: true, ...(params || {}) })),
  uploadWorldThumbnail: (formData: FormData) => api('/uploads/world-thumbnails', { method: 'POST', body: formData }),
  saveWorldSetting: ({ id, payload }: SavePayload) => api(id ? `/world-settings/${id}` : '/world-settings', { method: id ? 'PATCH' : 'POST', body: JSON.stringify(payload) }),
  deleteWorldSetting: (id: Id) => api(`/world-settings/${id}`, { method: 'DELETE' }),
  saveChatCommand: ({ id, payload }: SavePayload) => api(id ? `/chat-commands/${id}` : '/chat-commands', { method: id ? 'PATCH' : 'POST', body: JSON.stringify(payload) }),
  deleteChatCommand: (id: Id) => api(`/chat-commands/${id}`, { method: 'DELETE' }),
};

export const modelProviderApi = {
  accounts: () => api<ProviderAccount[]>('/model-provider-accounts'),
  createAccount: (payload: Payload) => api<ProviderAccount>('/model-provider-accounts', { method: 'POST', body: JSON.stringify(payload) }),
  updateAccount: (id: Id, payload: Payload) => api<ProviderAccount>(`/model-provider-accounts/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  testAccount: (id: Id) => api(`/model-provider-accounts/${id}/test`, { method: 'POST', body: JSON.stringify({}) }),
  syncModels: (id: Id) => api<ModelOption[]>(`/model-provider-accounts/${id}/sync-models`, { method: 'POST', body: JSON.stringify({}) }),
  options: () => api<ModelOption[]>('/model-options'),
  updateOption: (id: Id, payload: Payload) => api<ModelOption>(`/model-options/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  testOption: (id: Id) => api(`/model-options/${id}/test`, { method: 'POST', body: JSON.stringify({}) }),
  createManualOption: (payload: Payload) => api<ModelOption>('/model-options/manual', { method: 'POST', body: JSON.stringify(payload) }),
};
