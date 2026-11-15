import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { characterApi, settingsApi } from '../api/resources';
import { DEFAULT_FORBIDDEN_RULES, DEFAULT_TRAIT_SCORES, EMPTY_CHARACTER } from '../constants/domain';
import { emptyWorld } from '../views/WorldSettings';
import type { RoomWork } from '../utils/roomWork';
import { agentApi } from './api';
import { agentActions, NATIVE_PROPOSAL_FIELDS, proposalValueEqual, useAgentStore } from './store';
import type { AgentDraft } from './types';

type Form = Record<string, any>;
type Review = { token: string; draft: AgentDraft; initial: Form; baseline: Form; form: Form };
export function proposalRoute(draft: AgentDraft) {
  const character = draft.kind === 'character';
  return { page: character ? (draft.target_id ? 'characterDetail' : 'characterNew') : (draft.target_id ? 'worldSettingDetail' : 'worldSettingNew'), id: draft.target_id || '', path: `/${character ? 'characters' : 'world-settings'}/${draft.target_id ? encodeURIComponent(draft.target_id) : 'new'}` };
}
export function prefillProposal(draft: AgentDraft, baseline: Form): Form {
  if (draft.kind !== 'character' && draft.kind !== 'world') throw new Error('Separate image review required.');
  if (draft.target_id && baseline.id !== draft.target_id) throw new Error('Proposal target identity does not match the editor.');
  const fields = NATIVE_PROPOSAL_FIELDS[draft.kind];
  if (Object.keys(draft.after).some(key => !(fields as readonly string[]).includes(key))) throw new Error('This proposal contains unsupported fields.');
  return { ...baseline, ...draft.after };
}

// Review state is separate from ordinary editors: late resource hydration cannot
// overwrite it, and opening/closing on the same route preserves unsaved CRUD work.
export function useNativeProposal(route: { page: string; id?: string }, navigate: (path: string) => void, routeWork: RoomWork) {
  const request = useAgentStore(s => s.proposalRequest);
  const working = useAgentStore(s => s.working);
  const pending = useAgentStore(s => s.pending.length > 0);
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState('');
  const arrived = useRef<string | null>(null);
  const target = request ? proposalRoute(request.draft) : null;
  const matches = !!target && route.page === target.page && (route.id || '') === target.id;
  useEffect(() => {
    if (!request) return;
    arrived.current = null;
    setReview(null); setError('');
    const next = proposalRoute(request.draft);
    if (route.page !== next.page || (route.id || '') !== next.id) navigate(next.path);
  }, [request?.token, navigate]);
  useEffect(() => {
    if (!request) return;
    if (!matches) {
      if (arrived.current === request.token) agentActions.closeProposal(request.token);
      return;
    }
    arrived.current = request.token;
    const visit = routeWork.capture();
    let active = true;
    const owns = () => active && visit.current && useAgentStore.getState().proposalRequest?.token === request.token;
    void (async () => {
      const expected = request.draft;
      const draft = await agentApi.draft(expected.id);
      if (!owns()) return;
      if (draft.id !== expected.id || draft.kind !== expected.kind || draft.target_id !== expected.target_id || draft.version !== expected.version || draft.preview_hash !== expected.preview_hash || draft.base_revision !== expected.base_revision || !['draft', 'approved'].includes(draft.status)) throw new Error('The proposal changed. Reopen it in Lore Agent.');
      let baseline: Form;
      if (draft.kind === 'character') {
        const entity = draft.target_id ? await characterApi.get(draft.target_id) as Form : {};
        baseline = { ...EMPTY_CHARACTER, forbidden_rules: [...DEFAULT_FORBIDDEN_RULES], ...entity, trait_scores: { ...DEFAULT_TRAIT_SCORES, ...(entity.trait_scores || {}) } };
      } else {
        const worlds = draft.target_id ? await settingsApi.worldSettings() as Form[] : [];
        baseline = { ...emptyWorld, ...(draft.target_id ? worlds.find(world => world.id === draft.target_id) : {}) };
      }
      const initial = prefillProposal(draft, baseline);
      if (owns()) setReview({ token: request.token, draft, initial, baseline, form: initial });
    })().catch(error => { if (owns()) setError(error instanceof Error ? error.message : 'The proposal could not be loaded.'); });
    return () => { active = false; };
  }, [request?.token, route.page, route.id, matches, routeWork]);
  const current = matches && review?.token === request?.token ? review : null;
  const setForm: Dispatch<SetStateAction<Form>> = value => setReview(old => {
    if (!old || old.token !== useAgentStore.getState().proposalRequest?.token || useAgentStore.getState().working || useAgentStore.getState().pending.length) return old;
    return { ...old, form: typeof value === 'function' ? value(old.form) : value };
  });
  const changedFields = current?.draft.target_id && (current.draft.kind === 'character' || current.draft.kind === 'world')
    ? NATIVE_PROPOSAL_FIELDS[current.draft.kind].filter(key => !proposalValueEqual(current.form[key], current.baseline[key])) : [];
  return {
    active: matches, ready: !!current, kind: request?.draft.kind, form: current?.form,
    review: current, error: matches ? error : '', changedFields, setForm,
    blocked: working || pending,
    cancel: () => { if (request) agentActions.closeProposal(request.token); },
    async save() {
      if (!current || !request || !matches) throw new Error('The proposal has not finished loading.');
      const visit = routeWork.capture();
      return agentActions.saveProposal(current.draft, current.form, current.initial,
        () => visit.current && useAgentStore.getState().proposalRequest?.token === current.token);
    },
  };
}
