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

// First Send owns create + message under one guard; a lost create never sends.
store.setState({ settings: { enabled: true, llm_model_option_key: 'vertex-model' }, selected: '', newChat: true, sessions: {}, pending: [], input: 'First prompt', contexts: [] }); memory.clear(); calls.length = 0;
let releaseCreate;
api.createSession = async request_id => {
  calls.push('POST session');
  const journal = JSON.parse(memory.get('lore-agent-request-recovery-v1'));
  assert.equal(journal[0].request_id, request_id); assert.equal(journal[0].kind, 'session');
  assert.ok(!JSON.stringify(journal).includes('First prompt'));
  await new Promise(resolve => { releaseCreate = resolve; }); return session;
};
mode = 'completed';
const autoSend = actions.send(); await actions.send();
assert.deepEqual(calls, ['POST session'], 'double Send during creation cannot dispatch again');
let capturedTurn; const originalMessage = api.message;
api.message = async (...args) => { capturedTurn = args; return originalMessage(...args); };
store.setState({ input: 'Edited while creation awaits', contexts: [{ kind: 'world', id: 'later', fields: ['title'] }] });
releaseCreate(); await autoSend;
assert.equal(capturedTurn[2], 'First prompt'); assert.deepEqual(capturedTurn[3], []);
assert.equal(store.getState().input, 'Edited while creation awaits');
assert.equal(store.getState().contexts[0].id, 'later');
store.setState({ input: '', contexts: [] });
assert.deepEqual(calls, ['POST session','POST message']);
assert.equal(store.getState().input, ''); assert.equal(store.getState().selected, 's');
store.setState({ input: 'Existing conversation' }); await actions.send();
assert.deepEqual(calls, ['POST session','POST message','POST message'], 'existing conversation needs no new session');
store.setState({ input: 'Preserved local draft' });
const priorDrafts = store.getState().drafts; actions.newChat();
assert.equal(store.getState().selected, ''); assert.equal(store.getState().input, 'Preserved local draft');
await actions.refresh(); assert.equal(store.getState().selected, '', 'GET refresh must not undo deliberate new conversation or auto-load prior chat');
assert.equal(store.getState().drafts.d.id, priorDrafts.d.id);
const contexts = [{ kind: 'character', id: 'c', fields: ['name'] }];
store.setState({ contexts }); calls.length = 0;
api.createSession = async request_id => { calls.push('POST session'); assert.equal(JSON.parse(memory.get('lore-agent-request-recovery-v1'))[0].request_id, request_id); throw new TypeError('lost create'); };
await actions.send(); await actions.send();
assert.deepEqual(calls, ['POST session']);
assert.equal(store.getState().input, 'Preserved local draft'); assert.deepEqual(store.getState().contexts, contexts);
assert.equal(store.getState().pending[0].kind, 'session');
await actions.refresh();
assert.equal(calls.filter(call => call === 'POST message').length, 0);
assert.equal(store.getState().pending.length, 1);
api.operation = async request_id => ({ request_id, status: 'completed', kind: 'session', result: {}, error: null });
await actions.refresh(); assert.equal(store.getState().pending.length, 1, 'missing exact session receipt must keep journal');
api.operation = async request_id => ({ request_id, status: 'completed', kind: 'session', result: { id: 's' }, error: null });
const priorSessionGet = api.session; api.session = async id => { assert.equal(id, 's'); return priorSessionGet(id); };
await actions.refresh();
assert.equal(store.getState().selected, '', 'GET recovery caches resolved session without changing browsing intent'); assert.equal(store.getState().sessions.s.id, 's'); assert.equal(store.getState().pending.length, 0);
assert.equal(calls.filter(call => call === 'POST message').length, 0, 'resolved create never auto-replays a paid turn');
assert.equal(store.getState().input, 'Preserved local draft'); assert.deepEqual(store.getState().contexts, contexts);
actions.newChat(); api.createSession = async () => { calls.push('POST session'); throw new AgentHttpError(422); };
await actions.send(); assert.equal(store.getState().pending.length, 0); assert.equal(store.getState().input, 'Preserved local draft');
failStorage = true; const noStorageCount = calls.length; await actions.send();
assert.equal(calls.length, noStorageCount, 'creation also fails closed without its journal'); failStorage = false;
console.log('PASS first-Send session creation: one guarded create/message sequence, duplicate suppression, local new chat, input/context retention, unknown create no message, GET-only resolved receipt, rejection and storage failure');

// The real private-IP HTTP origin has no randomUUID (localhost fixtures do).
const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
const nativeCrypto = globalThis.crypto;
try {
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: { getRandomValues: nativeCrypto.getRandomValues.bind(nativeCrypto) } });
  store.setState({ selected: '', newChat: true, pending: [], input: 'Private HTTP first message', contexts: [] }); memory.clear(); calls.length = 0;
  let createdRequest;
  api.createSession = async id => { createdRequest = id; assert.match(id, /^[a-f0-9]{32}$/); calls.push('POST session'); return session; };
  mode = 'completed';
  await actions.send();
  assert.deepEqual(calls, ['POST session', 'POST message']);
  assert.equal(store.getState().pending.length, 0); assert.equal(store.getState().input, '');
  assert.ok(createdRequest);
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: {} });
  store.setState({ selected: '', pending: [], input: 'Keep this when secure randomness is missing' });
  const beforeMissingRng = calls.length; await actions.send();
  assert.equal(calls.length, beforeMissingRng); assert.equal(store.getState().pending.length, 0);
  assert.equal(store.getState().input, 'Keep this when secure randomness is missing');
  assert.ok(store.getState().error);
} finally { if (cryptoDescriptor) Object.defineProperty(globalThis, 'crypto', cryptoDescriptor); }
console.log('PASS private HTTP origin: missing randomUUID uses secure getRandomValues; no RNG fails before POST and retains the draft');

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

// Explicit recovery is independently GET-only, exact-linked and selection-fenced.
const { imageRecoveryTargets, selectedImageJobs } = module.exports;
const recoveryDraft = { ...draft, id: 'paid-draft', kind: 'image_generation', session_id: 'owned', status: 'committed', result: { job_id: 'paid-job' } };
const owned = { ...session, id: 'owned', title: 'Exact image conversation', drafts: [recoveryDraft] };
const other = { ...session, id: 'other', drafts: [] };
const recoveryJob = { id: 'paid-job', status: 'unknown', outputs: [], error: 'Raw provider uncertainty' };
const oldJournal = [{ request_id: 'paid-request', kind: 'commit', draft_id: 'paid-draft' }];
const resetRecovery = () => { store.setState({ selected: '', newChat: true, working: false, sessions: {}, drafts: {}, imageJobs: {}, operations: { 'paid-request': { request_id: 'paid-request', kind: 'image_generation', status: 'unknown', result: { job_id: 'paid-job' } } }, pending: oldJournal, input: 'Keep local composer', recoveryFocus: null, localRecovery: null, recoveryErrors: {} }); memory.set('lore-agent-request-recovery-v1', JSON.stringify(oldJournal)); calls.length = 0; };
api.operation = async id => { calls.push('GET operation'); return { request_id: id, kind: 'image_generation', status: 'unknown', result: { draft_id: recoveryDraft.id, job_id: recoveryJob.id } }; };
api.draft = async id => { calls.push('GET draft'); assert.equal(id, recoveryDraft.id); return recoveryDraft; };
api.sessions = async () => { calls.push('GET sessions'); return [other, owned]; };
api.session = async id => { calls.push('GET session ' + id); return id === owned.id ? owned : other; };
api.imageJob = async id => { calls.push('GET job'); assert.equal(id, recoveryJob.id); return recoveryJob; };
resetRecovery(); store.setState({ working: true });
await actions.openRecovery(imageRecoveryTargets(store.getState())[0]);
assert.equal(store.getState().selected, 'owned'); assert.equal(store.getState().working, true);
assert.equal(store.getState().input, 'Keep local composer'); assert.equal(store.getState().recoveryFocus.jobId, 'paid-job');
assert.deepEqual(store.getState().pending, oldJournal); assert.deepEqual(JSON.parse(memory.get('lore-agent-request-recovery-v1')), oldJournal);
assert.deepEqual(selectedImageJobs(store.getState()).map(j => j.id), ['paid-job']);
actions.selectSession('other'); assert.deepEqual(selectedImageJobs(store.getState()), []);
assert.ok(calls.every(c => c.startsWith('GET')));
// A second explicit same-session click has a new focus token.
store.setState({ working: false }); await actions.openRecovery(imageRecoveryTargets(store.getState())[0]);
const token = store.getState().recoveryFocus.token;
await actions.openRecovery(imageRecoveryTargets(store.getState())[0]); assert.ok(store.getState().recoveryFocus.token > token);
// Job-only recovery must inspect exact draft links, not first listed session.
resetRecovery(); store.setState({ pending: [{ request_id: 'job-only', kind: 'registration', image_job_id: 'paid-job' }], operations: {} });
api.operation = async id => ({ request_id: id, status: 'unknown', kind: 'image_generation', result: null });
await actions.openRecovery(imageRecoveryTargets(store.getState())[0]); assert.equal(store.getState().selected, 'owned');
assert.ok(calls.includes('GET session other')); assert.ok(calls.includes('GET session owned'));
// Ambiguous exact ownership fails closed; no invented session selection.
api.sessions = async () => [owned, { ...other, drafts: [recoveryDraft] }];
api.session = async id => ({ ...(id === owned.id ? owned : other), drafts: [recoveryDraft] });
actions.newChat(); await actions.openRecovery(imageRecoveryTargets(store.getState())[0]);
assert.equal(store.getState().selected, ''); assert.ok(store.getState().recoveryErrors['job-only']);
api.session = async id => id === owned.id ? owned : other;
// Late recovery must not navigate after an explicit history/new-chat intent.
resetRecovery(); let releaseRecovery; let operationReads = 0;
api.operation = async id => { operationReads++; await new Promise(resolve => { releaseRecovery = resolve; }); return { request_id: id, kind: 'image_generation', status: 'unknown', result: null }; };
const recovering = actions.openRecovery(imageRecoveryTargets(store.getState())[0]);
await actions.openRecovery(imageRecoveryTargets(store.getState())[0]); assert.equal(operationReads, 1);
actions.selectSession('other'); releaseRecovery(); await recovering;
assert.equal(store.getState().selected, 'other'); assert.equal(store.getState().recoveryFocus, null);
resetRecovery(); const sameBlank = actions.openRecovery(imageRecoveryTargets(store.getState())[0]); actions.newChat(); releaseRecovery(); await sameBlank;
assert.equal(store.getState().selected, ''); assert.equal(store.getState().recoveryFocus, null);
// 404/network failures retain the request and offer a retry, never POST.
api.operation = async id => ({ request_id: id, kind: 'image_generation', status: 'unknown', result: null });
for (const error of [new AgentHttpError(404), new TypeError('GET failed')]) {
  resetRecovery(); api.draft = async () => { throw error; };
  await actions.openRecovery(imageRecoveryTargets(store.getState())[0]);
  assert.equal(store.getState().selected, ''); assert.equal(store.getState().recoveryErrors['paid-request'], 'agent.recoveryFailed');
  assert.equal(store.getState().pending.length, 1); assert.equal(store.getState().recoveryLoading, '');
}
// An external sessionless draft has an honest local request view, no create.
resetRecovery(); api.draft = async () => ({ ...recoveryDraft, session_id: null });
await actions.openRecovery(imageRecoveryTargets(store.getState())[0]);
assert.equal(store.getState().selected, ''); assert.equal(store.getState().localRecovery.jobId, 'paid-job');
assert.equal(store.getState().recoveryFocus.jobId, 'paid-job'); assert.ok(calls.every(c => c.startsWith('GET')));
console.log('PASS actionable image recovery: exact old-journal draft/session/job, job-only search, ambiguity/404/network errors, same-session focus tokens, stale selection fences, independent singleflight, local external request, preserved composer/journal, zero POST');
// A simultaneous background refresh must not turn an explicit request jump into a dead first click.
resetRecovery();
const freshOwned = { ...owned, messages: [{ id: 'fresh-cache', role: 'assistant', content: 'Newer verified session cache' }] };
api.operation = async id => ({ request_id: id, kind: 'image_generation', status: 'unknown', result: { job_id: 'paid-job' } });
api.draft = async () => recoveryDraft;
api.sessions = async () => [freshOwned];
let releaseOldSession;
api.session = async () => new Promise(resolve => { releaseOldSession = resolve; });
const duringRefresh = actions.openRecovery(imageRecoveryTargets(store.getState())[0]);
for (let i = 0; i < 20 && !releaseOldSession; i++) await Promise.resolve();
assert.ok(releaseOldSession, 'navigation reached held session GET');
await actions.refresh();
assert.equal(store.getState().sessions.owned.messages[0].id, 'fresh-cache');
releaseOldSession(owned); await duringRefresh;
assert.equal(store.getState().selected, 'owned', 'explicit jump survives a newer GET-only cache hydration');
assert.equal(store.getState().sessions.owned.messages[0].id, 'fresh-cache', 'old navigation GET never overwrites fresh messages');
assert.equal(store.getState().recoveryFocus.jobId, 'paid-job');
assert.equal(store.getState().recoveryErrors['paid-request'], '');
assert.deepEqual(store.getState().pending, oldJournal);
assert.ok(calls.every(c => c.startsWith('GET')));
console.log('PASS first-click recovery during background refresh: current exact-linked cache wins without blocking navigation or replaying requests');
