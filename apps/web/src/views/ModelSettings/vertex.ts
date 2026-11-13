export type VertexDraft = {
  vertex_project: string;
  vertex_location: string;
  vertex_auth_mode: 'service_account' | 'adc';
  api_key: string;
};

export function validVertexConfiguration(draft: VertexDraft, storedCredential = false) {
  return !!(draft.vertex_project.trim() && draft.vertex_location.trim()
    && (draft.vertex_auth_mode === 'adc' || storedCredential || draft.api_key.trim()));
}

/** Never include an unchanged secret, or a stale upload in ADC mode. */
export function vertexPayload(draft: VertexDraft) {
  return {
    vertex_project: draft.vertex_project.trim(),
    vertex_location: draft.vertex_location.trim(),
    vertex_auth_mode: draft.vertex_auth_mode,
    ...(draft.vertex_auth_mode === 'service_account' && draft.api_key.trim() ? { api_key: draft.api_key.trim() } : {}),
  };
}

export const MAX_VERTEX_CREDENTIAL_SIZE = 32768;

export function readServiceAccountJson(text: string): string {
  // File size is checked before reading too. Do not echo parser errors or JSON.
  if (text.length > MAX_VERTEX_CREDENTIAL_SIZE) throw new Error('Invalid service account JSON');
  let value;
  try { value = JSON.parse(text); } catch { throw new Error('Invalid service account JSON'); }
  if (!value || value.type !== 'service_account' || typeof value.client_email !== 'string'
    || !value.client_email.trim() || typeof value.private_key !== 'string' || !value.private_key.trim()) {
    throw new Error('Invalid service account JSON');
  }
  return text;
}
