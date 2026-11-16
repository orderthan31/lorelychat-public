import { API_BASE } from '../constants/domain';
import type { AgentSettings, AgentSettingsInput, AgentSession, AgentDraft, AgentOperation, ContextSelection, ImageJob, ImageTargetKind, ImageContext, McpClient, McpScope } from './types';

export class AgentHttpError extends Error {
  constructor(public status: number) { super(`Lore Agent · HTTP ${status}`); }
}
const pathId = (id: string) => encodeURIComponent(id);
async function request<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}/lore-agent${path}`, {
    method, signal, cache: 'no-store', credentials: 'same-origin', redirect: 'error',
    headers: { 'Cache-Control': 'no-store', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) throw new AgentHttpError(response.status);
  return response.status === 204 ? undefined as T : await response.json() as T;
}
export const agentApi = {
  settings: () => request<AgentSettings>('/settings'),
  saveSettings: (value: AgentSettingsInput) => request<AgentSettings>('/settings', 'PUT', value),
  sessions: () => request<AgentSession[]>('/sessions'),
  session: (id: string, signal?: AbortSignal) => request<AgentSession>(`/sessions/${pathId(id)}`, 'GET', undefined, signal),
  createSession: (request_id: string) => request<AgentSession>('/sessions', 'POST', { request_id }),
  message: (id: string, request_id: string, content: string, context: ContextSelection[]) => request<AgentSession>(`/sessions/${pathId(id)}/messages`, 'POST', { request_id, content, context }),
  draft: (id: string) => request<AgentDraft>(`/drafts/${pathId(id)}`),
  revise: (draft: AgentDraft, fields: Record<string, unknown>) => request<AgentDraft>(`/drafts/${pathId(draft.id)}/revise`, 'POST', { expected_version: draft.version, fields }),
  approve: (draft: AgentDraft) => request<AgentDraft>(`/drafts/${pathId(draft.id)}/approve`, 'POST', { expected_version: draft.version, preview_hash: draft.preview_hash }),
  commit: (draft: AgentDraft, request_id: string) => request<AgentOperation>(`/drafts/${pathId(draft.id)}/commit`, 'POST', { request_id, expected_version: draft.version, preview_hash: draft.preview_hash }),
  commitForm: (draft: AgentDraft, request_id: string, manual_fields: Record<string, unknown>, baseline_fields: Record<string, unknown>) => request<AgentOperation>(`/drafts/${pathId(draft.id)}/commit-form`, 'POST', { request_id, expected_version: draft.version, preview_hash: draft.preview_hash, manual_fields, baseline_fields }),
  operation: (id: string, signal?: AbortSignal) => request<AgentOperation>(`/operations/${pathId(id)}`, 'GET', undefined, signal),
  clients: () => request<McpClient[]>('/mcp/clients'),
  createClient: (name: string, scopes: McpScope[]) => request<{ client: McpClient; token: string }>('/mcp/clients', 'POST', { name, scopes }),
  revokeClient: (id: string) => request<void>(`/mcp/clients/${pathId(id)}`, 'DELETE'),
  imageJob: (id: string) => request<ImageJob>(`/image-jobs/${pathId(id)}`),
  registration: (id: string, request_id: string, output_ids: string[], target_id: string, metadata: Record<string, unknown>, target_kind: ImageTargetKind = 'character') => request<AgentDraft>(`/image-jobs/${pathId(id)}/registration`, 'POST', { request_id, output_ids, target_id, target_kind, metadata }),
  imageContext: (kind: ImageTargetKind, id: string) => request<ImageContext>(`/image-context/${kind}/${pathId(id)}`),
  imageChoice: (draft: AgentDraft, generation_mode: 'new' | 'reference', reference_id?: string) => request<AgentDraft>(`/drafts/${pathId(draft.id)}/image-choice`, 'POST', { expected_version: draft.version, generation_mode, reference_id: reference_id || null }),
  referenceBlob: (draftId: string) => privateImageBlob(`/drafts/${pathId(draftId)}/reference`),
  candidateBlob: (kind: ImageTargetKind, target: string, reference: string) => privateImageBlob(`/image-context/${kind}/${pathId(target)}/references/${pathId(reference)}`),
  imageBlob: (id: string) => privateImageBlob(`/image-outputs/${pathId(id)}`),
};
async function privateImageBlob(path: string): Promise<Blob> {
    // Fetch only application-owned blob paths, never provider-supplied preview URLs.
    const response = await fetch(`${API_BASE}/lore-agent${path}`, { cache: 'no-store', credentials: 'same-origin', redirect: 'error', headers: { 'Cache-Control': 'no-store' } });
    if (!response.ok) throw new AgentHttpError(response.status);
    const blob = await response.blob();
    if (!/^image\/(png|jpeg|webp|gif)$/.test(blob.type)) throw new Error('Invalid image response');
    return blob;
}
