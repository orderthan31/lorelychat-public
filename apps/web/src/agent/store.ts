import { create } from 'zustand';
import { agentApi, AgentHttpError } from './api';
import type { AgentDraft, AgentOperation, AgentSession, AgentSettings, ContextSelection, ImageJob, ImageSelection, ImageChoice, ImageTargetKind, PendingRequest } from './types';

const RECOVERY_KEY = 'lore-agent-request-recovery-v1';
export const AGENT_DOCK_QUERY = '(min-width: 768px)';
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
export const NATIVE_MANUAL_FIELDS = { character: ['avatar_url', 'tts_provider', 'tts_model', 'tts_voice_style', 'tts_sample_text'], world: ['thumbnail_url'] } as const;
export function nativeProposalFields(draft: AgentDraft, form: Record<string, unknown>, initial: Record<string, unknown>) {
  if (draft.kind !== 'character' && draft.kind !== 'world') throw new Error('This proposal uses a separate review.');
  const allowed: readonly string[] = NATIVE_PROPOSAL_FIELDS[draft.kind];
  const fields: Record<string, unknown> = {};
  for (const key of new Set([...Object.keys(initial), ...Object.keys(form)])) {
    if (proposalValueEqual(form[key], initial[key])) continue;
    if ((NATIVE_MANUAL_FIELDS[draft.kind] as readonly string[]).includes(key)) continue;
    if (!allowed.includes(key)) throw new Error('This proposal cannot save unsupported changes. Your edits have been retained.');
    fields[key] = form[key];
  }
  return fields;
}
function matchProposal(actual: AgentDraft, expected: AgentDraft) {
  if (actual.id !== expected.id || actual.kind !== expected.kind || actual.target_id !== expected.target_id || actual.base_revision !== expected.base_revision || actual.version !== expected.version || actual.preview_hash !== expected.preview_hash) throw new Error('The proposal changed. Reopen it before saving; your edits have been retained.');
}
export type RecoveryTarget = { key: string; requestId?: string; sessionId?: string; draftId?: string; jobId?: string };
export type RecoveryFocus = RecoveryTarget & { token: number };
export type AgentState = {
  recoveryLoading: string; recoveryErrors: Record<string, string>; recoveryFocus: RecoveryFocus | null; localRecovery: RecoveryTarget | null;
  surfaceMode: 'dock' | 'sheet';
  activeMessage: string | null;
  proposalRequest: NativeProposalRequest | null;
  open: boolean; settings: AgentSettings | null; settingsError: string; error: string;
  refreshing: boolean; working: boolean; newChat: boolean; sessions: Record<string, AgentSession>; selected: string; input: string;
  resources: { kind: ContextSelection['kind']; id: string; label: string }[];
  imageChoices: Record<string, ImageChoice>;
  imageSelections: Record<string, ImageSelection>;
  contexts: ContextSelection[]; drafts: Record<string, AgentDraft>; edits: Record<string, string>;
  pending: PendingRequest[]; operations: Record<string, AgentOperation>; imageJobs: Record<string, ImageJob>;
  setOpen: (open: boolean) => void;
};
export const useAgentStore = create<AgentState>((set) => ({
  surfaceMode: typeof window !== 'undefined' && window.matchMedia?.(AGENT_DOCK_QUERY).matches ? 'dock' : 'sheet',
  recoveryLoading: '', recoveryErrors: {}, recoveryFocus: null, localRecovery: null,
  activeMessage: null, proposalRequest: null,
  open: false, settings: null, settingsError: '', error: '', refreshing: false, working: false, newChat: true,
  resources: [], imageChoices: {}, imageSelections: {}, sessions: {}, selected: '', input: '', contexts: [], drafts: {}, edits: {}, pending: recovered(), operations: {}, imageJobs: {},
  setOpen: open => set({ open }),
}));
const state = useAgentStore.getState;
const set = useAgentStore.setState;
const describe = (error: unknown) => error instanceof AgentHttpError ? error.message : 'agent.requestFailed';
let selectionEpoch = 0;
useAgentStore.subscribe((next, previous) => { if (next.selected !== previous.selected) selectionEpoch++; });
const sessionVersions = new Map<string, number>();
export function conversationPending(s: AgentState, selected = s.selected) {
  return s.pending.some(p => p.kind === 'session' || (p.kind === 'message' && (!p.session_id || p.session_id === selected)));
}
async function readSession(id: string) {
  const version = sessionVersions.get(id);
  const session = await agentApi.session(id);
  if (session.id !== id) throw new Error('Mismatched session receipt');
  if (sessionVersions.get(id) === version) acceptSession(session);
}
function acceptSession(session: AgentSession) {
  sessionVersions.set(session.id, (sessionVersions.get(session.id) || 0) + 1);
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
// Each poll starts only after the preceding GET settles plus two seconds.
// This lifetime belongs to the POST, not a panel mount or an animation timer.
function pollMessage(id: string, sessionId: string) {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let sequence = 0;
  const owns = () => !stopped && state().activeMessage === id && state().selected === sessionId;
  const stop = () => { stopped = true; clearTimeout(timer); controller?.abort(); unsubscribe(); };
  const unsubscribe = useAgentStore.subscribe(() => { if (!owns()) stop(); });
  const poll = async () => {
    if (!owns()) return;
    controller = new AbortController();
    try {
      const operation = await agentApi.operation(id, controller.signal);
      if (!owns() || operation.request_id !== id) return;
      const progress = operation.progress;
      const valid = operation.status === 'running' && progress && Number.isInteger(progress.sequence) && progress.sequence >= 1 && progress.sequence > sequence
        && ['model', 'search', 'read', 'draft', 'review', 'image_context', 'image_plan', 'navigation'].includes(progress.stage)
        && (progress.round == null || (Number.isInteger(progress.round) && progress.round >= 1));
      if (valid) {
        sequence = progress.sequence;
        set(s => ({ operations: { ...s.operations, [id]: operation } }));
      } else if (operation.status === 'unknown' || operation.status === 'completed' || operation.status === 'failed') {
        set(s => ({ operations: { ...s.operations, [id]: { ...operation, progress: null } } }));
      }
      // Serialized readback exposes accepted messages and staged previews while
      // the POST is still running. Never let a late GET replace its final result.
      if (!owns()) return;
      const session = await agentApi.session(sessionId, controller.signal);
      if (!owns() || session.id !== sessionId) return;
      acceptSession(session);
      if (operation.status === 'completed' || operation.status === 'failed') { stop(); return; }
    } catch { /* A missing receipt is not acceptance and never permits a retry POST. */ }
    finally { if (owns()) timer = setTimeout(() => void poll(), 2000); }
  };
  timer = setTimeout(() => void poll(), 2000);
  return stop;
}
const stringId = (value: unknown) => typeof value === 'string' && value ? value : undefined;
function draftJob(draft?: AgentDraft) { return stringId(draft?.result?.job_id) || stringId(draft?.after.job_id); }
export function imageRecoveryTargets(s: AgentState): RecoveryTarget[] {
  const targets: RecoveryTarget[] = [];
  for (const pending of s.pending) {
    if (pending.request_id === s.activeMessage) continue;
    const operation = s.operations[pending.request_id];
    const draftId = pending.draft_id || stringId(operation?.result?.draft_id);
    const draft = draftId ? s.drafts[draftId] : undefined;
    const jobId = pending.image_job_id || stringId(operation?.result?.job_id) || draftJob(draft);
    if (!jobId && !draft?.kind.startsWith('image_') && !operation?.kind?.includes('image') && pending.kind !== 'registration') continue;
    if (operation?.status === 'running' && s.working) continue;
    targets.push({ key: pending.request_id, requestId: pending.request_id, sessionId: pending.session_id || draft?.session_id || undefined, draftId, jobId });
  }
  for (const job of Object.values(s.imageJobs)) {
    if (job.status !== 'unknown' || targets.some(t => t.jobId === job.id)) continue;
    const linked = Object.values(s.drafts).filter(d => draftJob(d) === job.id);
    const owners = [...new Set(linked.map(d => d.session_id).filter(Boolean))];
    targets.push({ key: `job:${job.id}`, jobId: job.id, ...(owners.length === 1 ? { sessionId: owners[0]! } : {}), ...(linked.length === 1 ? { draftId: linked[0].id } : {}) });
  }
  return targets.filter((target, i) => !targets.slice(0, i).some(other => target.jobId ? other.jobId === target.jobId : target.draftId ? other.draftId === target.draftId : other.key === target.key));
}
// A sessionless receipt is not permission to expose another conversation's job.
export function selectedImageJobs(s: AgentState): ImageJob[] {
  if (s.localRecovery) return Object.values(s.imageJobs).filter(job => job.id === s.localRecovery?.jobId);
  const linked = new Set(Object.values(s.drafts).filter(d => d.session_id === s.selected && !!s.selected || (!d.session_id && d.kind === 'image_registration')).map(draftJob));
  for (const p of s.pending) if (p.image_job_id && p.session_id === s.selected && !!s.selected) linked.add(p.image_job_id);
  if (s.recoveryFocus?.sessionId === s.selected && s.recoveryFocus.jobId) linked.add(s.recoveryFocus.jobId);
  return Object.values(s.imageJobs).filter(job => linked.has(job.id));
}
let recoveryToken = 0;
export const agentActions = {
  selectSession(id: string) {
    selectionEpoch++;
    set({ selected: id, newChat: !id, localRecovery: null, recoveryFocus: null });
  },
  async openRecovery(target: RecoveryTarget) {
    if (state().recoveryLoading) return;
    const epoch = selectionEpoch;
    const versions = new Map(sessionVersions);
    const owns = () => selectionEpoch === epoch;
    set(s => ({ recoveryLoading: target.key, recoveryErrors: { ...s.recoveryErrors, [target.key]: '' } }));
    try {
      let { sessionId, draftId, jobId } = target;
      const exact = (a: string | undefined, b: string | undefined) => {
        if (a && b && a !== b) throw new Error('Conflicting linkage');
        return a || b;
      };
      if (target.requestId) {
        try {
          const operation = await agentApi.operation(target.requestId);
          if (operation.request_id !== target.requestId) throw new Error('Mismatched receipt');
          draftId = exact(draftId, stringId(operation.result?.draft_id));
          jobId = exact(jobId, stringId(operation.result?.job_id));
          sessionId = exact(sessionId, stringId(operation.result?.session_id));
        } catch (error) {
          if (!(error instanceof AgentHttpError && error.status === 404 && (draftId || jobId))) throw error;
        }
      }
      let draft: AgentDraft | undefined;
      if (draftId) {
        draft = await agentApi.draft(draftId);
        if (draft.id !== draftId || !draft.kind.startsWith('image_')) throw new Error('Mismatched draft');
        sessionId = exact(sessionId, draft.session_id || undefined);
        jobId = exact(jobId, draftJob(draft));
      }
      let session: AgentSession | undefined;
      if (!sessionId && jobId && !draft) {
        // Search exact relationships, never labels, ordering or target resource IDs.
        const listed = await agentApi.sessions();
        const candidates = new Map([...Object.values(state().sessions), ...listed].map(s => [s.id, s]));
        const matches: AgentSession[] = [];
        for (const id of candidates.keys()) {
          if (!owns()) return;
          const candidate = await agentApi.session(id);
          if (candidate.id !== id) throw new Error('Mismatched session');
          if (candidate.drafts.some(d => draftJob(d) === jobId)) matches.push(candidate);
        }
        if (matches.length > 1) throw new Error('Ambiguous linkage');
        if (matches.length === 1) { session = matches[0]; sessionId = session.id; }
      }
      if (sessionId) {
        session ||= await agentApi.session(sessionId);
        if (session.id !== sessionId) throw new Error('Mismatched session');
        if (draft && draft.session_id !== sessionId) throw new Error('Mismatched draft owner');
        if (!draft && !session.drafts.some(d => draftJob(d) === jobId)) throw new Error('Missing exact linkage');
      }
      if (!draftId && !jobId) throw new Error('Missing linkage');
      const job = jobId ? await agentApi.imageJob(jobId) : undefined;
      if (job && job.id !== jobId) throw new Error('Mismatched job');
      if (!owns()) return;
      // A background GET may already have hydrated this same conversation.
      // Keep that newer cache, but do not discard the user's explicit jump.
      if (session && sessionVersions.get(session.id) !== versions.get(session.id)) {
        const current = state().sessions[session.id];
        const linked = current?.drafts.some(d => draftId ? d.id === draftId && (!jobId || draftJob(d) === jobId) : draftJob(d) === jobId);
        if (!current || !linked) throw new Error('Newer session no longer has the exact request');
        session = current;
        draft = undefined; // Never overwrite a newer draft with a staged GET.
      } else if (session) acceptSession(session);
      if (draft) acceptDraft(draft);
      if (job) set(s => ({ imageJobs: { ...s.imageJobs, [job.id]: job } }));
      const resolved = { ...target, sessionId, draftId, jobId };
      set({ selected: sessionId || '', newChat: !sessionId, open: true, localRecovery: sessionId ? null : resolved, recoveryFocus: { ...resolved, token: ++recoveryToken } });
    } catch {
      if (owns()) set(s => ({ recoveryErrors: { ...s.recoveryErrors, [target.key]: 'agent.recoveryFailed' } }));
    } finally { set({ recoveryLoading: '' }); }
  },
  openProposal(draft: AgentDraft) {
    if (state().working || (draft.kind !== 'character' && draft.kind !== 'world') || !['draft', 'approved'].includes(draft.status)) return;
    // Opening is local navigation intent only. The native editor performs GETs.
    const current = state().proposalRequest;
    if (current && current.draft.id === draft.id && current.draft.kind === draft.kind && current.draft.target_id === draft.target_id && current.draft.version === draft.version && current.draft.preview_hash === draft.preview_hash && current.draft.base_revision === draft.base_revision) {
      set({ open: state().open && state().surfaceMode === 'dock' }); return;
    }
    set({ proposalRequest: { token: requestId(), draft }, open: state().open && state().surfaceMode === 'dock', error: '' });
  },
  closeProposal(token: string) {
    if (state().proposalRequest?.token === token) set({ proposalRequest: null });
  },
  async saveProposal(expected: AgentDraft, form: Record<string, unknown>, initial: Record<string, unknown>, owns: () => boolean, baseline: Record<string, unknown> = initial): Promise<AgentDraft> {
    if (state().working || state().pending.length) throw new Error('An Agent request is unresolved. Check its result in Lore Agent before saving.');
    const fields = nativeProposalFields(expected, form, initial);
    if (expected.kind !== 'character' && expected.kind !== 'world') throw new Error('Separate review required.');
    const manualFields = Object.fromEntries(NATIVE_MANUAL_FIELDS[expected.kind].filter(key => !proposalValueEqual(form[key], initial[key])).map(key => [key, form[key]]));
    const baselineFields = expected.target_id ? Object.fromEntries(NATIVE_PROPOSAL_FIELDS[expected.kind].map(key => [key, baseline[key]])) : {};
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
        const operation = await agentApi.commitForm(draft, id, manualFields, baselineFields); accepted = true;
        set(s => ({ operations: { ...s.operations, [id]: operation } }));
        if (operation.request_id !== id) throw new Error('Save receipt could not be verified. Check the result in Lore Agent.');
        if (operation.status === 'failed') { forget(id); throw new Error('The proposal could not be saved. Check its result in Lore Agent.'); }
        const committed = await agentApi.draft(draft.id); acceptDraft(committed);
        // Owner commit may rebase a media-only target revision. Bind the new
        // committed preview to the receipt, never relax the pre-dispatch CAS.
        if (committed.id !== draft.id || committed.kind !== draft.kind || committed.target_id !== draft.target_id || operation.result?.draft_id !== committed.id || operation.result?.kind !== committed.kind || operation.result?.version !== committed.version || operation.result?.preview_hash !== committed.preview_hash) throw new Error('Save receipt could not be verified. Check the result in Lore Agent.');
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
    // Explicit new-chat intent also fences a creation begun from the blank view.
    selectionEpoch++;
    set({ selected: '', newChat: true, error: '', localRecovery: null, recoveryFocus: null });
  },
  createSession: () => guarded(async () => {
    if (conversationPending(state())) return;
    const epoch = selectionEpoch;
    const id = reserve('session');
    try { const session = await agentApi.createSession(id); acceptSession(session); if (selectionEpoch === epoch) set({ selected: session.id, newChat: false }); forget(id); }
    catch (error) { if (error instanceof AgentHttpError && error.status < 500) forget(id); throw error; }
  }),
  send: () => guarded(async () => {
    const s = state();
    if (!s.input.trim() || !s.settings?.enabled || !s.settings.llm_model_option_key || conversationPending(s) || s.sessions[s.selected]?.busy) return;
    let sessionId = s.selected;
    let epoch = selectionEpoch;
    if (!sessionId) {
      // One guard owns both steps. Journal creation before dispatch; never replay
      // the model turn automatically if session creation has an unknown outcome.
      const creationId = reserve('session');
      try {
        const session = await agentApi.createSession(creationId);
        acceptSession(session); sessionId = session.id;
        forget(creationId);
        if (selectionEpoch !== epoch) return;
        set({ selected: sessionId, newChat: false, localRecovery: null, recoveryFocus: null }); epoch = selectionEpoch;
      } catch (error) {
        if (error instanceof AgentHttpError && error.status < 500) forget(creationId);
        throw error;
      }
    }
    if (!state().settings?.enabled || !state().settings?.llm_model_option_key || conversationPending(state(), sessionId) || state().sessions[sessionId]?.busy) return;
    const id = reserve('message', { session_id: sessionId });
    set({ activeMessage: id });
    const stopPolling = pollMessage(id, sessionId);
    try {
      const session = await agentApi.message(sessionId, id, s.input, s.contexts);
      stopPolling();
      acceptSession(session); forget(id);
      if (selectionEpoch === epoch && state().selected === sessionId && state().input === s.input) set({ input: '' });
    } catch (error) { if (error instanceof AgentHttpError && error.status < 500) forget(id); throw error; }
    finally { stopPolling(); if (state().activeMessage === id) set({ activeMessage: null }); }
  }),
  refresh: async () => {
    if (state().refreshing) return;
    set({ refreshing: true });
    try {
    // Recovery is GET-only. Missing receipts never authorize replay.
    for (const pending of state().pending) {
      try {
      const operation = await agentApi.operation(pending.request_id);
      if (operation.request_id !== pending.request_id) throw new Error('Mismatched receipt');
      set(s => ({ operations: { ...s.operations, [operation.request_id]: operation } }));
      if (pending.kind === 'session' && operation.status === 'completed') {
        const sessionId = operation.result?.id;
        if (typeof sessionId !== 'string' || !sessionId) throw new Error('Missing session receipt');
        await readSession(sessionId); // Recovery never changes the browsing selection.
      }
      if (pending.session_id) await readSession(pending.session_id);
      if (pending.draft_id) acceptDraft(await agentApi.draft(pending.draft_id));
      if (pending.image_job_id) {
        const job = await agentApi.imageJob(pending.image_job_id); set(s => ({ imageJobs: { ...s.imageJobs, [job.id]: job } }));
      }
      const recoveredDraftId = operation.result?.draft_id;
      if (typeof recoveredDraftId === 'string') acceptDraft(await agentApi.draft(recoveredDraftId));
      const imageJobId = operation.result?.job_id;
      if (typeof imageJobId === 'string') await agentActions.loadImageJob(imageJobId);
      if (operation.status === 'completed' || operation.status === 'failed') forget(pending.request_id);
      } catch (error) {
        const target = imageRecoveryTargets(state()).find(t => t.requestId === pending.request_id);
        if (target) set(s => ({ recoveryErrors: { ...s.recoveryErrors, [target.key]: 'agent.recoveryFailed' } }));
        else set({ error: describe(error) });
      }
    }
    const versions = new Map(sessionVersions);
    const sessions = await agentApi.sessions();
    sessions.forEach(session => { if (sessionVersions.get(session.id) === versions.get(session.id)) acceptSession(session); });
    if (state().selected) await readSession(state().selected);
    } catch (error) { set({ error: describe(error) }); }
    finally { set({ refreshing: false }); }
  },
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
