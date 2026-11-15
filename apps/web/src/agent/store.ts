import { create } from 'zustand';
import { agentApi, AgentHttpError } from './api';
import type { AgentDraft, AgentOperation, AgentSession, AgentSettings, ContextSelection, ImageJob, ImageSelection, ImageChoice, ImageTargetKind, PendingRequest } from './types';

const RECOVERY_KEY = 'lore-agent-request-recovery-v1';
function recovered(): PendingRequest[] {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(RECOVERY_KEY) || '[]');
    return Array.isArray(value) ? value.filter((p): p is PendingRequest => !!p && typeof p.request_id === 'string' && ['session', 'message', 'commit', 'registration'].includes(p.kind)).map(p => ({ request_id: p.request_id, kind: p.kind, session_id: p.session_id, draft_id: p.draft_id, image_job_id: p.image_job_id })) : [];
  } catch { return []; }
}
function persist(requests: PendingRequest[]) { sessionStorage.setItem(RECOVERY_KEY, JSON.stringify(requests)); }
export type NativeProposalRequest = { token: string; draft: AgentDraft };
export const NATIVE_PROPOSAL_FIELDS = {
  character: ['name', 'persona', 'description', 'appearance', 'behavior_style', 'speech_style', 'emotional_rules', 'forbidden_rules', 'trait_scores'],
  world: ['title', 'description', 'genre_mode', 'location', 'mood', 'world_seed', 'opening_scene', 'opening_line', 'tone_preset', 'relationship_archetype', 'compression_focus', 'tags', 'enabled'],
} as const;
export function proposalValueEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) return false;
  const left = Object.keys(a), right = Object.keys(b);
  return left.length === right.length && left.every(key => Object.prototype.hasOwnProperty.call(b, key) && proposalValueEqual(a[key], b[key]));
}
export function nativeProposalFields(draft: AgentDraft, form: Record<string, unknown>, initial: Record<string, unknown>) {
  if (draft.kind !== 'character' && draft.kind !== 'world') throw new Error('This proposal uses a separate review.');
  const allowed: readonly string[] = NATIVE_PROPOSAL_FIELDS[draft.kind];
  const fields: Record<string, unknown> = {};
  for (const key of new Set([...Object.keys(initial), ...Object.keys(form)])) {
    if (proposalValueEqual(form[key], initial[key])) continue;
    if (!allowed.includes(key)) throw new Error('This proposal cannot save image or voice changes. Your edits have been retained.');
    fields[key] = form[key];
  }
  return fields;
}
function matchProposal(actual: AgentDraft, expected: AgentDraft) {
  if (actual.id !== expected.id || actual.kind !== expected.kind || actual.target_id !== expected.target_id || actual.base_revision !== expected.base_revision || actual.version !== expected.version || actual.preview_hash !== expected.preview_hash) throw new Error('The proposal changed. Reopen it before saving; your edits have been retained.');
}
export type AgentState = {
  proposalRequest: NativeProposalRequest | null;
  open: boolean; settings: AgentSettings | null; settingsError: string; error: string;
  working: boolean; newChat: boolean; sessions: Record<string, AgentSession>; selected: string; input: string;
  resources: { kind: ContextSelection['kind']; id: string; label: string }[];
  imageChoices: Record<string, ImageChoice>;
  imageSelections: Record<string, ImageSelection>;
  contexts: ContextSelection[]; drafts: Record<string, AgentDraft>; edits: Record<string, string>;
  pending: PendingRequest[]; operations: Record<string, AgentOperation>; imageJobs: Record<string, ImageJob>;
  setOpen: (open: boolean) => void;
};
export const useAgentStore = create<AgentState>((set) => ({
  proposalRequest: null,
  open: false, settings: null, settingsError: '', error: '', working: false, newChat: true,
  resources: [], imageChoices: {}, imageSelections: {}, sessions: {}, selected: '', input: '', contexts: [], drafts: {}, edits: {}, pending: recovered(), operations: {}, imageJobs: {},
  setOpen: open => set({ open }),
}));
const state = useAgentStore.getState;
const set = useAgentStore.setState;
const describe = (error: unknown) => error instanceof AgentHttpError ? error.message : 'agent.requestFailed';
function acceptSession(session: AgentSession) {
  session.drafts.forEach(acceptDraft);
  set(s => ({ sessions: { ...s.sessions, [session.id]: session }, drafts: { ...s.drafts, ...Object.fromEntries(session.drafts.map(d => [d.id, d])) } }));
}
function acceptDraft(draft: AgentDraft) {
  set(s => {
    const resources = [...s.resources];
    const id = draft.result?.id;
    if (draft.status === 'committed' && (draft.kind === 'character' || draft.kind === 'world') && typeof id === 'string') {
      const name = draft.kind === 'world' ? draft.after.title : draft.after.name;
      const resource = { kind: draft.kind, id, label: typeof name === 'string' ? name : id };
      const index = resources.findIndex(r => r.kind === resource.kind && r.id === id);
      if (index < 0) resources.push(resource); else resources[index] = resource;
    }
    return { drafts: { ...s.drafts, [draft.id]: draft }, resources };
  });
}
function forget(id: string) {
  const pending = state().pending.filter(p => p.request_id !== id);
  persist(pending); set({ pending });
}
function requestId() {
  const random = globalThis.crypto;
  if (typeof random?.randomUUID === 'function') return random.randomUUID();
  // HTTP private-network origins do not expose randomUUID. getRandomValues is
  // still available there; keep strong opaque identifiers without a login/TLS
  // prerequisite or a weak Math.random fallback.
  if (typeof random?.getRandomValues !== 'function') throw new Error('Secure request identifiers are unavailable');
  return Array.from(random.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join('');
}
function reserve(kind: PendingRequest['kind'], extra: Omit<PendingRequest, 'kind' | 'request_id'> = {}) {
  const request = { request_id: requestId(), kind, ...extra };
  const pending = [...state().pending, request];
  // Storage failure must stop dispatch, not produce an untraceable paid request.
  persist(pending); set({ pending }); return request.request_id;
}
async function guarded(action: () => Promise<void>) {
  if (state().working) return;
  set({ working: true, error: '' });
  try { await action(); } catch (error) { set({ error: describe(error) }); }
  finally { set({ working: false }); }
}
export const agentActions = {
  openProposal(draft: AgentDraft) {
    if (state().working || (draft.kind !== 'character' && draft.kind !== 'world') || !['draft', 'approved'].includes(draft.status)) return;
    // Opening is local navigation intent only. The native editor performs GETs.
    set({ proposalRequest: { token: requestId(), draft }, open: false, error: '' });
  },
  closeProposal(token: string) {
    if (state().proposalRequest?.token === token) set({ proposalRequest: null });
  },
  async saveProposal(expected: AgentDraft, form: Record<string, unknown>, initial: Record<string, unknown>, owns: () => boolean): Promise<AgentDraft> {
    if (state().working || state().pending.length) throw new Error('An Agent request is unresolved. Check its result in Lore Agent before saving.');
    const fields = nativeProposalFields(expected, form, initial);
    const checkOwner = () => { if (!owns()) throw new Error('The editor changed. Saving was stopped.'); };
    checkOwner();
    set({ working: true, error: '' });
    try {
      let draft = await agentApi.draft(expected.id); acceptDraft(draft);
      matchProposal(draft, expected); checkOwner();
      if (!['draft', 'approved'].includes(draft.status)) throw new Error('This proposal is no longer editable. Reopen it in Lore Agent.');
      if (Object.keys(fields).length) {
        const revised = await agentApi.revise(draft, fields);
        draft = await agentApi.draft(expected.id); acceptDraft(draft);
        matchProposal(draft, revised); checkOwner();
      }
      const approved = await agentApi.approve(draft);
      matchProposal(approved, draft);
      draft = await agentApi.draft(expected.id); acceptDraft(draft);
      matchProposal(draft, approved); checkOwner();
      if (draft.status !== 'approved') throw new Error('Approval could not be verified.');
      const id = reserve('commit', { draft_id: draft.id, ...(draft.session_id ? { session_id: draft.session_id } : {}) });
      let accepted = false;
      try {
        const operation = await agentApi.commit(draft, id); accepted = true;
        set(s => ({ operations: { ...s.operations, [id]: operation } }));
        const committed = await agentApi.draft(draft.id); acceptDraft(committed);
        matchProposal(committed, draft);
        if (operation.request_id !== id) throw new Error('Save receipt could not be verified. Check the result in Lore Agent.');
        if (operation.status === 'failed') { forget(id); throw new Error('The proposal could not be saved. Check its result in Lore Agent.'); }
        if (operation.status !== 'completed' || committed.status !== 'committed' || typeof committed.result?.id !== 'string' || committed.result.id !== operation.result?.id || (draft.target_id && committed.result.id !== draft.target_id)) throw new Error('Save result is unresolved. Check its result in Lore Agent; do not submit again.');
        forget(id);
        return committed;
      } catch (error) {
        if (!accepted && error instanceof AgentHttpError && error.status < 500) { forget(id); acceptDraft(await agentApi.draft(draft.id)); }
        throw error;
      }
    } catch (error) {
      set({ error: describe(error) }); throw error;
    } finally { set({ working: false }); }
  },
  async loadSettings() {
    try { set({ settings: await agentApi.settings(), settingsError: '' }); }
    catch (error) { set({ settingsError: describe(error) }); }
  },
  newChat: () => {
    if (state().working || state().pending.length) return;
    set({ selected: '', newChat: true, error: '' });
  },
  createSession: () => guarded(async () => {
    if (state().pending.length) return;
    const id = reserve('session');
    try { const session = await agentApi.createSession(id); acceptSession(session); set({ selected: session.id, newChat: false }); forget(id); }
    catch (error) { if (error instanceof AgentHttpError && error.status < 500) forget(id); throw error; }
  }),
  send: () => guarded(async () => {
    const s = state();
    if (!s.input.trim() || !s.settings?.enabled || !s.settings.llm_model_option_key || s.pending.length || s.sessions[s.selected]?.busy) return;
    let sessionId = s.selected;
    if (!sessionId) {
      // One guard owns both steps. Journal creation before dispatch; never replay
      // the model turn automatically if session creation has an unknown outcome.
      const creationId = reserve('session');
      try {
        const session = await agentApi.createSession(creationId);
        acceptSession(session); sessionId = session.id;
        set({ selected: sessionId, newChat: false }); forget(creationId);
      } catch (error) {
        if (error instanceof AgentHttpError && error.status < 500) forget(creationId);
        throw error;
      }
    }
    if (!state().settings?.enabled || !state().settings?.llm_model_option_key || state().pending.length || state().sessions[sessionId]?.busy) return;
    const id = reserve('message', { session_id: sessionId });
    try {
      const session = await agentApi.message(sessionId, id, s.input, s.contexts);
      acceptSession(session); forget(id);
      if (state().input === s.input) set({ input: '' });
    } catch (error) { if (error instanceof AgentHttpError && error.status < 500) forget(id); throw error; }
  }),
  refresh: () => guarded(async () => {
    // Recovery is GET-only. Missing receipts never authorize replay.
    for (const pending of state().pending) {
      try {
      const operation = await agentApi.operation(pending.request_id);
      set(s => ({ operations: { ...s.operations, [operation.request_id]: operation } }));
      if (pending.kind === 'session' && operation.status === 'completed') {
        const sessionId = operation.result?.id;
        if (typeof sessionId !== 'string' || !sessionId) throw new Error('Missing session receipt');
        const session = await agentApi.session(sessionId);
        if (session.id !== sessionId) throw new Error('Mismatched session receipt');
        acceptSession(session); set({ selected: session.id, newChat: false });
      }
      if (pending.session_id) acceptSession(await agentApi.session(pending.session_id));
      if (pending.draft_id) acceptDraft(await agentApi.draft(pending.draft_id));
      if (pending.image_job_id) {
        const job = await agentApi.imageJob(pending.image_job_id); set(s => ({ imageJobs: { ...s.imageJobs, [job.id]: job } }));
      }
      const recoveredDraftId = operation.result?.draft_id;
      if (typeof recoveredDraftId === 'string') acceptDraft(await agentApi.draft(recoveredDraftId));
      const imageJobId = operation.result?.job_id;
      if (typeof imageJobId === 'string') await agentActions.loadImageJob(imageJobId);
      if (operation.status === 'completed' || operation.status === 'failed') forget(pending.request_id);
      } catch (error) { set({ error: describe(error) }); }
    }
    const sessions = await agentApi.sessions(); sessions.forEach(acceptSession);
    if (state().selected) acceptSession(await agentApi.session(state().selected));
  }),
  refreshDraft: (id: string) => guarded(async () => {
    const draft = await agentApi.draft(id); acceptDraft(draft);
    if (draft.session_id && draft.session_id !== state().selected) { acceptSession(await agentApi.session(draft.session_id)); set({ selected: draft.session_id }); }
  }),
  revise: (draft: AgentDraft, fields: Record<string, unknown>) => guarded(async () => {
    try { acceptDraft(await agentApi.revise(draft, fields)); }
    catch (error) { acceptDraft(await agentApi.draft(draft.id)); throw error; }
  }),
  imageChoice: (draft: AgentDraft, mode: 'new' | 'reference', referenceId?: string) => guarded(async () => {
    try { await agentApi.imageChoice(draft, mode, referenceId); }
    finally { acceptDraft(await agentApi.draft(draft.id)); }
  }),
  approve: (draft: AgentDraft) => guarded(async () => {
    try { await agentApi.approve(draft); }
    finally { acceptDraft(await agentApi.draft(draft.id)); }
  }),
  commit: (draft: AgentDraft) => guarded(async () => {
    if (state().pending.length || draft.status !== 'approved') return;
    const id = reserve('commit', { draft_id: draft.id, ...(draft.session_id ? { session_id: draft.session_id } : {}) });
    let accepted = false;
    try {
      const operation = await agentApi.commit(draft, id); accepted = true;
      set(s => ({ operations: { ...s.operations, [id]: operation } }));
      // Verify committed target before clearing the durable request identifier.
      acceptDraft(await agentApi.draft(draft.id));
      const jobId = operation.result?.job_id;
      if (typeof jobId === 'string') await agentActions.loadImageJob(jobId);
      if (operation.status === 'completed' || operation.status === 'failed') forget(id);
    } catch (error) {
      if (!accepted && error instanceof AgentHttpError && error.status < 500) { forget(id); acceptDraft(await agentApi.draft(draft.id)); }
      throw error;
    }
  }),
  async loadImageJob(id: string) {
    const job = await agentApi.imageJob(id); set(s => ({ imageJobs: { ...s.imageJobs, [id]: job } }));
  },
  registration: (jobId: string, outputs: string[], target: string, metadata: Record<string, unknown>, targetKind: ImageTargetKind = 'character') => guarded(async () => {
    if (state().pending.length) return;
    const id = reserve('registration', { image_job_id: jobId });
    let accepted = false;
    try {
      const draft = await agentApi.registration(jobId, id, outputs, target, metadata, targetKind); accepted = true;
      acceptDraft(await agentApi.draft(draft.id)); forget(id);
    } catch (error) {
      if (!accepted && error instanceof AgentHttpError && error.status < 500) forget(id);
      throw error;
    }
  }),
};
