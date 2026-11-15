import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { create } from 'zustand';

const memory = new Map();
let failStorage = false;
globalThis.sessionStorage = { getItem: key => memory.get(key) || null, setItem: (key, value) => { if (failStorage) throw new Error('Synthetic storage failure'); memory.set(key, value); } };
class AgentHttpError extends Error { constructor(status) { super(`HTTP ${status}`); this.status = status; } }
const clone = value => structuredClone(value);
let live, calls, lost, stale, owns, hold;
const api = {
  draft: async () => { calls.push('GET draft'); return clone(live); },
  revise: async (draft, fields) => {
    calls.push('POST revise'); assert.equal(draft.version, live.version);
    assert.deepEqual(fields, { title: 'Owner edited world' });
    live = { ...live, version: live.version + 1, preview_hash: 'hash-v2', status: 'draft', after: { ...live.after, ...fields } };
    return clone(live);
  },
  approve: async draft => {
    calls.push('POST approve'); assert.equal(draft.version, live.version); assert.equal(draft.preview_hash, live.preview_hash);
    live.status = 'approved'; if (hold) await hold;
    return clone(live);
  },
  commit: async (draft, request_id) => {
    calls.push('POST commit');
    assert.equal(live.status, 'approved'); assert.equal(draft.version, live.version); assert.equal(draft.preview_hash, live.preview_hash);
    const journal = JSON.parse(memory.get('lore-agent-request-recovery-v1'));
    assert.equal(journal[0].request_id, request_id); assert.equal(journal[0].draft_id, live.id);
    assert.ok(!JSON.stringify(journal).includes('Owner edited world'));
    if (stale) throw new AgentHttpError(409);
    if (lost) throw new TypeError('Synthetic lost response');
    live.status = 'committed'; live.result = { id: 'new-world', kind: 'world' };
    return { request_id, status: 'completed', kind: 'world', result: live.result };
  },
  sessions: async () => [],
  operation: async request_id => { calls.push('GET operation'); return { request_id, status: 'unknown', result: null }; },
};
const source = ts.transpileModule(readFileSync(new URL('../src/agent/store.ts', import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const module = { exports: {} };
new Function('require', 'module', 'exports', source)(name => name === 'zustand' ? { create } : name === './api' ? { agentApi: api, AgentHttpError } : (() => { throw new Error(`Unexpected import: ${name}`); })(), module, module.exports);
const { agentActions: actions, useAgentStore: store, nativeProposalFields, proposalValueEqual, NATIVE_PROPOSAL_FIELDS } = module.exports;
function reset() {
  calls = []; lost = false; stale = false; owns = true; hold = null; memory.clear();
  live = { id: 'world-proposal', session_id: null, kind: 'world', target_id: null, version: 1, base_revision: null, before: null, after: { title: 'Proposed world', world_seed: 'Synthetic rules' }, changes: {}, preview_hash: 'hash-v1', status: 'draft', warnings: [], result: null };
  store.setState({ working: false, pending: [], proposalRequest: null, sessions: {}, selected: '' });
  return clone(live);
}
let expected = reset();
actions.openProposal(expected);
assert.equal(calls.length, 0, 'CTA is only local intent, never a write');
assert.equal(store.getState().open, false);
assert.equal(store.getState().proposalRequest.draft.id, expected.id);
const token = store.getState().proposalRequest.token;
actions.openProposal({ ...expected, kind: 'image_generation' });
assert.equal(store.getState().proposalRequest.token, token, 'image proposals keep the specialized review');
const initial = { ...expected.after, thumbnail_url: '/unchanged.webp', metadata: { untouched: true } };
const form = { ...initial, title: 'Owner edited world' };
const result = await actions.saveProposal(expected, form, initial, () => owns);
assert.equal(result.status, 'committed'); assert.equal(result.version, 2);
assert.deepEqual(calls, ['GET draft', 'POST revise', 'GET draft', 'POST approve', 'GET draft', 'POST commit', 'GET draft']);
assert.equal(store.getState().pending.length, 0);
await assert.rejects(actions.saveProposal(expected, form, initial, () => owns), /proposal changed/);
assert.equal(calls.filter(x => x === 'POST commit').length, 1, 'completed proposal cannot commit twice');

expected = reset(); live.version = 2;
await assert.rejects(actions.saveProposal(expected, expected.after, expected.after, () => true), /proposal changed/);
assert.deepEqual(calls, ['GET draft'], 'stale preview cannot approve or commit');
expected = reset(); stale = true;
await assert.rejects(actions.saveProposal(expected, expected.after, expected.after, () => true), /409/);
assert.equal(store.getState().pending.length, 0, 'definite domain CAS rejection is not an unknown request');

expected = reset(); lost = true;
await assert.rejects(actions.saveProposal(expected, expected.after, expected.after, () => true), /lost response/);
assert.equal(store.getState().pending.length, 1);
await actions.refresh();
await assert.rejects(actions.saveProposal(expected, expected.after, expected.after, () => true), /unresolved/);
assert.equal(calls.filter(x => x === 'POST commit').length, 1, 'unknown result recovery is GET-only, never replay');
assert.equal(store.getState().pending.length, 1);

expected = reset();
let release; hold = new Promise(resolve => { release = resolve; });
const save = actions.saveProposal(expected, expected.after, expected.after, () => owns);
await new Promise(resolve => setTimeout(resolve, 0));
await assert.rejects(actions.saveProposal(expected, expected.after, expected.after, () => true), /unresolved/);
owns = false; release();
await assert.rejects(save, /editor changed/);
assert.equal(calls.includes('POST commit'), false, 'leaving during approval prevents commit dispatch');

expected = reset(); failStorage = true;
await assert.rejects(actions.saveProposal(expected, expected.after, expected.after, () => true), /storage failure/);
assert.equal(calls.includes('POST commit'), false, 'storage failure stops commit before dispatch');
failStorage = false;

expected = reset();
assert.throws(() => nativeProposalFields(expected, { ...initial, thumbnail_url: '/changed.webp' }, initial), /cannot save image or voice/);
const character = { ...expected, kind: 'character' };
const baseline = { name: 'Existing', persona: 'Keep', avatar_url: '/portrait.webp', tts_provider: 'supertonic', trait_scores: { empathy: 50 }, unrelated: 'preserved' };
assert.deepEqual(nativeProposalFields(character, { ...baseline, name: 'Edited' }, baseline), { name: 'Edited' });
assert.throws(() => nativeProposalFields(character, { ...baseline, tts_provider: 'other' }, baseline), /cannot save/);
assert.equal(proposalValueEqual({ a: 1, b: [2] }, { b: [2], a: 1 }), true);
assert.equal(NATIVE_PROPOSAL_FIELDS.character.includes('avatar_url'), false);
assert.equal(NATIVE_PROPOSAL_FIELDS.world.includes('thumbnail_url'), false);
console.log('PASS native proposal production owner: local-only CTA, exact revised approval/commit, durable receipt, duplicate/stale/CAS rejection, unknown GET-only recovery, route ownership, unsupported fields retained/rejected.');
