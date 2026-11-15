import { create } from 'zustand';
import { agentApi, AgentHttpError, setAgentOwnerKey } from './api';
import type { AgentDraft, AgentOperation, AgentSession, AgentSettings, ContextSelection, ImageJob, ImageSelection, ImageChoice, ImageTargetKind, PendingRequest } from './types';

const RECOVERY_KEY = 'lore-agent-request-recovery-v1';
function recovered(): PendingRequest[] {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(RECOVERY_KEY) || '[]');
    return Array.isArray(value) ? value.filter((p): p is PendingRequest => !!p && typeof p.request_id === 'string' && ['session', 'message', 'commit', 'registration'].includes(p.kind)).map(p => ({ request_id: p.request_id, kind: p.kind, session_id: p.session_id, draft_id: p.draft_id, image_job_id: p.image_job_id })) : [];
  } catch { return []; }
}
function persist(requests: PendingRequest[]) { sessionStorage.setItem(RECOVERY_KEY, JSON.stringify(requests)); }
export type AgentState = {
  open: boolean; unlocked: boolean; settings: AgentSettings | null; settingsError: string; error: string;
  working: boolean; sessions: Record<string, AgentSession>; selected: string; input: string;
  resources: { kind: ContextSelection['kind']; id: string; label: string }[];
  imageChoices: Record<string, ImageChoice>;
  imageSelections: Record<string, ImageSelection>;
  contexts: ContextSelection[]; drafts: Record<string, AgentDraft>; edits: Record<string, string>;
  pending: PendingRequest[]; operations: Record<string, AgentOperation>; imageJobs: Record<string, ImageJob>;
  setOpen: (open: boolean) => void;
};
export const useAgentStore = create<AgentState>((set) => ({
  open: false, unlocked: false, settings: null, settingsError: '', error: '', working: false,
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
function reserve(kind: PendingRequest['kind'], extra: Omit<PendingRequest, 'kind' | 'request_id'> = {}) {
  const request = { request_id: crypto.randomUUID(), kind, ...extra };
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
  async loadSettings() {
    try { set({ settings: await agentApi.settings(), settingsError: '' }); }
    catch (error) { set({ settingsError: describe(error) }); }
  },
  async unlock(key: string) {
    if (state().working) return;
    await guarded(async () => {
      setAgentOwnerKey(key);
      try {
        await agentApi.clients(); // Owner authentication works even with the master switch off.
        const sessions = state().settings?.enabled ? await agentApi.sessions() : [];
        sessions.forEach(acceptSession);
        set({ unlocked: true, selected: state().selected || sessions[0]?.id || '' });
      } catch (error) { setAgentOwnerKey(''); set({ unlocked: false }); throw error; }
    });
  },
  lock() { setAgentOwnerKey(''); set({ unlocked: false, open: false, sessions: {}, drafts: {}, imageJobs: {}, imageChoices: {}, imageSelections: {}, operations: {}, edits: {}, input: '', contexts: [] }); },
  createSession: () => guarded(async () => {
    if (state().pending.length) return;
    const id = reserve('session');
    try { const session = await agentApi.createSession(id); acceptSession(session); set({ selected: session.id }); forget(id); }
    catch (error) { if (error instanceof AgentHttpError && error.status < 500) forget(id); throw error; }
  }),
  send: () => guarded(async () => {
    const s = state();
    if (!s.input.trim() || !s.selected || !s.settings?.enabled || !s.settings.llm_model_option_key || s.pending.length || s.sessions[s.selected]?.busy) return;
    const id = reserve('message', { session_id: s.selected });
    try {
      const session = await agentApi.message(s.selected, id, s.input, s.contexts);
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
    if (!state().selected && sessions[0]) set({ selected: sessions[0].id });
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
