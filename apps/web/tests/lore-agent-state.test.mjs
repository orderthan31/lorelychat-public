import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { create } from 'zustand';

// Execute the production Zustand owner, not a reimplemented state machine.
const memory = new Map(); let failStorage = false;
globalThis.sessionStorage = { getItem: key => memory.get(key) || null, setItem: (key,value) => { if (failStorage) throw new Error('synthetic storage failure'); memory.set(key,value); } };
class AgentHttpError extends Error { constructor(status) { super(`Lore Agent · HTTP ${status}`); this.status = status; } }
const calls = []; let release; let mode = 'held';
const draft = { id: 'd', session_id: 's', kind: 'character', target_id: 'c', version: 3, base_revision: 1, before: {}, after: { name: 'N' }, changes: {}, preview_hash: 'hash-v3', status: 'approved', warnings: [], result: null };
const session = { id: 's', title: 'Synthetic', messages: [], drafts: [draft], busy: false, last_error: null };
const api = {
  settings: async () => { calls.push('GET settings'); return { enabled: false, llm_model_option_key: null, image_provider_account_id: null, image_model_id: null, mcp_enabled: false }; },
  sessions: async () => { calls.push('GET sessions'); return [session]; },
  session: async () => { calls.push('GET session'); return session; },
  draft: async () => { calls.push('GET draft'); return draft; },
  message: async (_id, request_id, content, context) => {
    calls.push('POST message');
    const journal = JSON.parse(memory.get('lore-agent-request-recovery-v1'));
    assert.equal(journal[0].request_id, request_id);
    assert.ok(!JSON.stringify(journal).includes(content)); assert.deepEqual(context, []);
    if (mode === 'held') await new Promise(r => { release = r; });
    if (mode === 'unknown') throw new TypeError('synthetic lost response');
    return session;
  },
  operation: async request_id => { calls.push('GET operation'); return { request_id, status: 'unknown', kind: 'agent_turn', result: null, error: null }; },
  commit: async (_draft, _requestId) => { calls.push('POST commit'); throw new AgentHttpError(409); },
};
const source = ts.transpileModule(readFileSync(new URL('../src/agent/store.ts', import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const module = { exports: {} };
new Function('require','module','exports',source)(name => name === 'zustand' ? { create } : name === './api' ? { agentApi: api, AgentHttpError } : (() => { throw new Error(`Unexpected import ${name}`); })(), module, module.exports);
const { agentActions: actions, useAgentStore: store } = module.exports;
await actions.loadSettings();
assert.equal(store.getState().settings.enabled, false);
assert.equal(store.getState().settings.llm_model_option_key, null);
assert.equal(store.getState().settings.mcp_enabled, false);
assert.deepEqual(calls, ['GET settings'], 'initial settings read neither authenticates nor dispatches model calls');
assert.equal('unlocked' in store.getState(), false);
assert.equal('unlock' in actions, false);
assert.equal('lock' in actions, false);
store.setState({ settings: { enabled: true, llm_model_option_key: 'model' }, selected: 's', sessions: { s: session }, input: 'Synthetic input not persisted' });
const first = actions.send(); await actions.send();
assert.equal(calls.filter(c => c === 'POST message').length,1,'synchronous submission ownership prevents duplicates');
assert.equal(store.getState().working,true); release(); await first;
assert.equal(store.getState().pending.length,0); assert.equal(store.getState().input,'');
mode = 'unknown'; store.setState({ input: 'Uncertain input retained' }); await actions.send();
assert.equal(store.getState().pending.length,1); assert.equal(store.getState().input,'Uncertain input retained');
const count = calls.filter(c => c.startsWith('POST')).length; await actions.refresh(); await actions.send();
assert.equal(calls.filter(c => c.startsWith('POST')).length,count,'uncertain receipt only performs GET and blocks repeat POST');
assert.equal(store.getState().pending.length,1);
store.setState({ pending: [] }); memory.clear();
await actions.commit(draft);
assert.equal(store.getState().pending.length,0,'definite CAS rejection releases pending reservation');
assert.equal(store.getState().error,'Lore Agent · HTTP 409');
failStorage = true; mode = 'completed'; store.setState({ input: 'Unjournaled input' });
const before = calls.length; await actions.send(); assert.equal(calls.length,before,'storage failure must prevent POST');
failStorage = false;
// Committed creation and session hydration expose fresh targets without App bootstrap reload.
const world = { ...draft, id: 'world-draft', kind: 'world', status: 'committed', target_id: null, after: { title: 'New world' }, result: { id: 'new-world', kind: 'world' } };
api.draft = async () => world;
await actions.refreshDraft(world.id);
assert.deepEqual(store.getState().resources, [{ kind: 'world', id: 'new-world', label: 'New world' }]);
api.sessions = async () => [{ ...session, drafts: [world, { ...draft, status: 'committed', result: { id: 'new-character' } }] }];
api.session = async () => ({ ...session, drafts: [world] });
await actions.refresh(); await actions.refresh();
assert.equal(store.getState().resources.length, 2, 'hydration upserts instead of duplicating targets');
assert.equal(store.getState().resources.find(r => r.id === 'new-character').label, 'N');
let registrationArgs;
api.registration = async (...args) => { registrationArgs = args; return world; };
await actions.registration('job', ['output'], 'new-world', {}, 'world');
assert.equal(registrationArgs[5], 'world');
await actions.registration('old-job', ['output'], 'new-character', {});
assert.equal(registrationArgs[5], 'character', 'legacy callers default to character');
// Owner choice is a dedicated POST followed by authoritative GET, never a tool flag.
const imageDraft = { ...draft, kind: 'image_generation', status: 'approved', after: { generation_mode: 'reference', owner_choice_confirmed: true } };
let choiceArgs; const updatedImage = { ...imageDraft, version: 4, status: 'draft', after: { generation_mode: 'new', owner_choice_confirmed: true } };
api.imageChoice = async (...args) => { choiceArgs = args; calls.push('POST image-choice'); return updatedImage; };
api.draft = async () => { calls.push('GET image-choice draft'); return updatedImage; };
await actions.imageChoice(imageDraft, 'new');
assert.equal(choiceArgs[0].version, 3); assert.equal(choiceArgs[1], 'new');
assert.deepEqual(calls.slice(-2), ['POST image-choice', 'GET image-choice draft']);
assert.equal(store.getState().drafts.d.status, 'draft'); assert.equal(store.getState().drafts.d.version, 4);
assert.ok(!JSON.stringify([...memory.values()]).includes('generation_mode'));
store.setState({ input: 'Retained draft', imageChoices: { d: { version: 4, mode: 'new', referenceId: '' } } });
store.getState().setOpen(true); store.getState().setOpen(false);
assert.equal(store.getState().input, 'Retained draft');
assert.equal(store.getState().imageChoices.d.mode, 'new');
assert.ok(store.getState().sessions.s);
console.log('PASS production Agent store: direct settings with master OFF, pre-POST identifier journal, duplicate ownership, unknown GET-only reconciliation, CAS rejection, failed-storage dispatch block, close retains drafts');

// Execute the real REST adapter with synthetic fetch, without any credential setup.
const apiModule = { exports: {} };
const apiSource = ts.transpileModule(readFileSync(new URL('../src/agent/api.ts', import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
new Function('require', 'module', 'exports', apiSource)(name => { assert.equal(name, '../constants/domain'); return { API_BASE: '/api' }; }, apiModule, apiModule.exports);
const originalFetch = globalThis.fetch; const requests = [];
globalThis.fetch = async (url, options) => { requests.push({ url, options }); return new Response(new Blob(['synthetic image'], { type: 'image/webp' }), { status: 200, headers: { 'Content-Type': 'image/webp' } }); };
try {
  const rest = apiModule.exports.agentApi;
  await rest.imageBlob('output'); await rest.referenceBlob('draft'); await rest.candidateBlob('character', 'target', 'reference');
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return Response.json({}); };
  await rest.settings(); await rest.saveSettings({ enabled: false, llm_model_option_key: null, image_provider_account_id: null, image_model_id: null, mcp_enabled: false }); await rest.sessions(); await rest.clients();
  for (const { url, options } of requests) {
    assert.ok(url.startsWith('/api/lore-agent/')); assert.ok(!url.includes('?'));
    assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store'); assert.equal(options.redirect, 'error');
    assert.equal(options.headers['Cache-Control'], 'no-store');
    assert.equal(Object.keys(options.headers).some(key => /owner|authorization/i.test(key)), false);
  }
  assert.equal('setAgentOwnerKey' in apiModule.exports, false);
  console.log('PASS production Agent REST: settings/session/client reads and settings save without owner key; three blob paths retain same-origin/no-store/redirect protections');
} finally { globalThis.fetch = originalFetch; }
