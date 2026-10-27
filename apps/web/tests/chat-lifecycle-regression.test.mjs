import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const transpile = (source) => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const load = (source) => import(`data:text/javascript;base64,${Buffer.from(transpile(source)).toString('base64')}`);
const { RoomWork, RoomWorkCancelledError, GenerationOwners } = await load(readFileSync(new URL('../src/utils/roomWork.ts', import.meta.url), 'utf8'));
const lifecycle = await load(readFileSync(new URL('../src/api/generationJobEvents.ts', import.meta.url), 'utf8').replace("import { API_BASE } from '../constants/domain';", "const API_BASE = '/api';"));
// Execute the actual App functions, not a second implementation or regex assertions.
const source = ts.createSourceFile('App.tsx', readFileSync(new URL('../src/app/App.tsx', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const app = source.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === 'App');
const names = ['fetchConversation', 'loadOlderMessages', 'revealServerMessages', 'pollGenerationJob', 'waitForGenerationJob', 'loadGeneratedJobMessages', 'resumeGeneration', 'submitConversationPayload'];
const functions = app.body.statements.filter((node) => ts.isFunctionDeclaration(node) && names.includes(node.name?.text)).map((node) => node.getText(source)).join('\n');
assert.equal(app.body.statements.filter((node) => ts.isFunctionDeclaration(node) && names.includes(node.name?.text)).length, names.length);
const instantiate = new Function('env', `with (env) { ${transpile(functions)}; return { ${names.join(',')} }; }`);
const flush = async () => { for (let n = 0; n < 30; n++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };

class Clock {
  now = 0; next = 1; timers = new Map();
  set = (fn, ms) => { const id = this.next++; this.timers.set(id, { fn, at: this.now + ms }); return id; };
  clear = (id) => this.timers.delete(id);
  async tick(ms) {
    const end = this.now + ms;
    await flush();
    while (true) {
      const due = [...this.timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      this.now = due[1].at; this.timers.delete(due[0]); due[1].fn(); await flush();
    }
    this.now = end; await flush();
  }
}
class FakeEventSource {
  static instances = [];
  listeners = new Map(); closed = false;
  constructor(url) { this.url = url; FakeEventSource.instances.push(this); }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  close() { this.closed = true; }
  emit(status, extra = {}) { this.listeners.get(status)?.({ data: JSON.stringify({ status, job_id: 'job-A', conversation_id: 'A', ...extra }) }); }
}
const original = { window: globalThis.window, EventSource: globalThis.EventSource, setTimeout, clearTimeout, requestAnimationFrame: globalThis.requestAnimationFrame };
const originalDateNow = Date.now;
let clock;
function setup() {
  clock = new Clock(); FakeEventSource.instances = [];
  Date.now = () => clock.now;
  globalThis.setTimeout = clock.set; globalThis.clearTimeout = clock.clear;
  globalThis.window = { location: { origin: 'https://fixture.test' }, setTimeout: clock.set, clearTimeout: clock.clear };
  globalThis.EventSource = FakeEventSource;
  globalThis.requestAnimationFrame = (fn) => clock.set(fn, 16);
  const roomWork = new RoomWork(); const visit = roomWork.enter('A');
  const env = {
    ...lifecycle, RoomWorkCancelledError,
    roomWork, readyVisitRef: { current: visit }, generationOwners: new GenerationOwners(),
    activeGenerationSubscriptionRef: { current: null },
    selectedConversationId: 'A', selectedConversationIdRef: { current: 'A' },
    messages: [{ id: 'A-current' }], loadingOlderMessages: false, hasOlderMessages: true,
    statuses: [], nextScrollModeRef: { current: 'idle' }, skipNextAutoScrollRef: { current: false }, lastMessageTailRef: { current: {} },
    chatThreadRef: { current: { scrollHeight: 500, scrollTop: 100, querySelector: () => null } }, conversation: { id: 'A', genre_mode: 'battle' },
    t: (value) => value, locale: 'en', formatUiCount: (count) => String(count), normalizeRuntimeSetting: (value) => value,
    JOB_POLL_INTERVAL_MS: 1200, JOB_POLL_MAX_ATTEMPTS: 3, JOB_PENDING_TIMEOUT_MESSAGE: 'pending', BUBBLE_REVEAL_DELAY_MS: 50,
    newClientRequestId: () => 'request-1', contextDrawerOpen: false,
    markConversationRead: async () => {}, loadConversations: async () => {}, loadBattleState: async () => {},
    conversationApi: { generationJob: async () => ({ status: 'completed', generated_message_ids: ['A-reply'] }), messages: async () => [{ id: 'A-incoming' }, { id: 'A-reply' }], get: async (id) => ({ id }), participants: async () => [], runtimeSetting: async () => ({}) },
    conversationMutations: { createMessageJob: { mutateAsync: async () => ({ job: { id: 'job-A' }, incoming_message: { id: 'A-incoming' } }) } },
  };
  env.setMessages = (update) => { env.messages = typeof update === 'function' ? update(env.messages) : update; };
  env.setStatus = (status) => env.statuses.push(status);
  env.setLoadingOlderMessages = (value) => { env.loadingOlderMessages = value; };
  env.setHasOlderMessages = (value) => { env.hasOlderMessages = value; };
  for (const key of ['setOlderPagingReady', 'setMessageSelectionMode', 'setSelectedMessageIds', 'setShowJumpToLatest', 'setConversation', 'setParticipants', 'setConversationRuntimeSetting', 'setConversationContext', 'setBattleState', 'setBattleControlDraft', 'setSpeakerId']) env[key] = () => {};
  env.EMPTY_BATTLE_CONTROL_DRAFT = {}; env.USER_ID = 'user';
  const fns = instantiate(env);
  const enter = (id, messages = [{ id: `${id}-current` }]) => {
    const visit = roomWork.enter(id); env.selectedConversationId = id; env.selectedConversationIdRef.current = id;
    env.readyVisitRef.current = visit; env.messages = messages; env.statuses = []; env.loadingOlderMessages = false;
    return visit;
  };
  return { env, fns, enter };
}
let passed = 0;
async function test(name, fn) { await fn(); passed++; console.log(`ok ${passed} - ${name}`); }
try {
  await test('silent-open SSE reaches durable polling at the absolute deadline', async () => {
    const { env, fns } = setup(); let polls = 0;
    env.conversationApi.generationJob = async () => { polls++; return { status: 'completed' }; };
    const result = fns.waitForGenerationJob('A', 'job-A');
    FakeEventSource.instances[0].onopen();
    await clock.tick(14999); assert.equal(polls, 0);
    await clock.tick(1); assert.equal((await result).status, 'completed'); assert.equal(polls, 1);
    assert.equal(FakeEventSource.instances[0].closed, true); assert.equal(clock.timers.size, 0);
  });
  await test('error/open flapping cannot reset the fallback error budget', async () => {
    const { fns } = setup(); const result = fns.waitForGenerationJob('A', 'job-A');
    const source = FakeEventSource.instances[0]; source.onopen(); source.onerror(); source.onopen(); source.onerror();
    await flush(); assert.equal((await result).status, 'completed'); assert.equal(source.closed, true); assert.equal(clock.timers.size, 0);
  });
  await test('malformed/wrong-room/replayed frames cannot bypass ownership or deadline', async () => {
    setup(); let statuses = 0;
    const sub = lifecycle.subscribeGenerationJobLifecycle({ conversationId: 'A', jobId: 'job-A', onStatus: () => statuses++ });
    const outcome = sub.promise.catch((error) => error);
    const source = FakeEventSource.instances[0]; source.onopen();
    source.emit('completed', { conversation_id: 'B' }); source.listeners.get('completed')({ data: 'bad-json' });
    source.emit('running'); await clock.tick(15000);
    assert.match((await outcome).message, /reconciliation/); assert.equal(statuses, 1);
    source.emit('completed'); assert.equal(statuses, 1); assert.equal(clock.timers.size, 0);
  });
  await test('route cancellation stops SSE and does not start a foreign poll', async () => {
    const { env, fns, enter } = setup(); let polls = 0;
    env.conversationApi.generationJob = async () => { polls++; return {}; };
    const result = fns.waitForGenerationJob('A', 'job-A').catch((error) => error);
    enter('B'); await flush(); assert.ok(await result instanceof RoomWorkCancelledError);
    assert.equal(polls, 0); assert.deepEqual(env.statuses, []); assert.equal(FakeEventSource.instances[0].closed, true); assert.equal(clock.timers.size, 0);
  });
  await test('A→B→A re-entry reconciles retained job and releases send guard exactly once', async () => {
    const { env, fns, enter } = setup(); let completed = false;
    env.conversationApi.generationJob = async () => ({ status: completed ? 'completed' : 'running', generated_message_ids: ['A-reply'] });
    const submit = fns.submitConversationPayload({ content: 'fixture' }, 'local_A'); await flush();
    assert.equal(env.generationOwners.pending.size, 1);
    enter('B'); await flush(); await submit;
    completed = true; enter('A');
    await fns.fetchConversation('A');
    // Visibility and entry race on the same actual runner/reveal.
    const a = fns.resumeGeneration(); const b = fns.resumeGeneration();
    await clock.tick(100); await Promise.all([a, b]);
    assert.equal(env.generationOwners.pending.size, 0);
    assert.equal(env.messages.filter((message) => message.id === 'A-reply').length, 1);
    assert.equal(env.messages.some((message) => message.is_typing), false);
  });
  await test('late POST after navigation retains server ownership; duplicate submit is blocked', async () => {
    const { env, fns, enter } = setup(); const post = deferred(); let posts = 0;
    env.conversationMutations.createMessageJob.mutateAsync = () => { posts++; return post.promise; };
    const first = fns.submitConversationPayload({ content: 'fixture' }, 'local_A');
    await fns.submitConversationPayload({ content: 'duplicate' }, 'local_duplicate'); assert.equal(posts, 1);
    enter('B'); post.resolve({ job: { id: 'job-A' }, incoming_message: { id: 'A-incoming' } }); await first;
    assert.deepEqual(env.messages, [{ id: 'B-current' }]); assert.deepEqual(env.statuses, []);
    enter('A'); const resume = fns.resumeGeneration(); await clock.tick(100); await resume;
    assert.equal(env.generationOwners.pending.size, 0); assert.equal(posts, 1);
  });
  await test('visibility forces GET while single-flight prevents duplicate reveal', async () => {
    const { env, fns } = setup(); let completed = false;
    env.conversationApi.generationJob = async () => ({ status: completed ? 'completed' : 'running', generated_message_ids: ['A-reply'] });
    const submit = fns.submitConversationPayload({ content: 'fixture' }, 'local_A'); await flush();
    completed = true; env.activeGenerationSubscriptionRef.current.reconcile();
    const second = fns.resumeGeneration(); await clock.tick(100); await Promise.all([submit, second]);
    assert.equal(env.messages.filter((message) => message.id === 'A-reply').length, 1);
    assert.equal(env.generationOwners.pending.size, 0);
  });
  await test('failed terminal job unlocks its room and marks its incoming bubble', async () => {
    const { env, fns } = setup(); env.conversationApi.generationJob = async () => ({ status: 'failed', error_message: 'fixture failure' });
    await fns.submitConversationPayload({ content: 'fixture' }, 'local_A');
    assert.equal(env.generationOwners.pending.size, 0); assert.equal(env.messages.find((message) => message.id === 'local_A').failed, true);
  });
  await test('battle control is synchronized before waiting for generation completion', async () => {
    const { env, fns } = setup(); const order = [];
    env.loadBattleState = async () => { order.push('battle'); };
    env.conversationApi.generationJob = async () => { order.push('job-read'); return { status: 'running' }; };
    const submit = fns.submitConversationPayload({ content: 'fixture', battle_control: { action: 'start' } }, 'local_A');
    await flush(); assert.equal(order[0], 'battle'); assert.equal(order[1], 'job-read');
    env.roomWork.enter('B'); await submit;
  });
  await test('transient reconciliation GET failure falls back without dropping ownership', async () => {
    const { env, fns } = setup(); let reads = 0;
    env.conversationApi.generationJob = async () => { if (++reads === 1) throw new Error('offline'); return { status: 'completed', generated_message_ids: ['A-reply'] }; };
    const submit = fns.submitConversationPayload({ content: 'fixture' }, 'local_A'); await clock.tick(100); await submit;
    assert.equal(reads, 2); assert.equal(env.generationOwners.pending.size, 0);
    assert.equal(env.messages.filter((message) => message.id === 'A-reply').length, 1);
  });
  await test('poll budget exhaustion remains recoverable on re-entry, not a permanent lock', async () => {
    const { env, fns, enter } = setup(); env.conversationApi.generationJob = async () => ({ status: 'running' });
    const submit = fns.submitConversationPayload({ content: 'fixture' }, 'local_A'); await flush();
    env.activeGenerationSubscriptionRef.current.reconcile(); await clock.tick(4000); await submit;
    assert.equal(env.generationOwners.pending.size, 1);
    enter('B'); enter('A'); env.conversationApi.generationJob = async () => ({ status: 'completed', generated_message_ids: ['A-reply'] });
    const resume = fns.resumeGeneration(); await clock.tick(100); await resume;
    assert.equal(env.generationOwners.pending.size, 0);
  });
  await test('late history cannot write messages/status/loading/scroll even after A→B→A', async () => {
    const { env, fns, enter } = setup(); const response = deferred(); env.conversationApi.messages = () => response.promise;
    const result = fns.loadOlderMessages(); enter('B'); enter('A', [{ id: 'A-new-visit' }]); env.loadingOlderMessages = true;
    response.resolve([{ id: 'A-old-response' }]); await result; await clock.tick(50);
    assert.deepEqual(env.messages, [{ id: 'A-new-visit' }]); assert.deepEqual(env.statuses, []);
    assert.equal(env.loadingOlderMessages, true); assert.equal(env.chatThreadRef.current.scrollTop, 100);
  });
  await test('pending reveal cancels immediately without inserting into another room', async () => {
    const { env, fns, enter } = setup();
    const result = fns.revealServerMessages([{ id: 'A-incoming' }, { id: 'A-one' }, { id: 'A-two' }], 'local_A').catch((error) => error);
    enter('B'); await flush(); assert.ok(await result instanceof RoomWorkCancelledError); await clock.tick(500);
    assert.deepEqual(env.messages, [{ id: 'B-current' }]); assert.deepEqual(env.statuses, []); assert.equal(clock.timers.size, 0);
  });
  await test('in-flight poll and its timeout are cancelled on room change', async () => {
    const { env, fns, enter } = setup(); const response = deferred(); env.conversationApi.generationJob = () => response.promise;
    const result = fns.pollGenerationJob('A', 'job-A').catch((error) => error);
    enter('B'); response.resolve({ status: 'running' }); await flush();
    assert.ok(await result instanceof RoomWorkCancelledError); assert.deepEqual(env.statuses, []); assert.equal(clock.timers.size, 0);
  });
  await test('late initial room fetch cannot overwrite a newer visit to the same ID', async () => {
    const { env, fns, enter } = setup(); const response = deferred(); env.conversationApi.messages = () => response.promise;
    const result = fns.fetchConversation('A'); enter('B'); enter('A', [{ id: 'A-new-visit' }]);
    response.resolve([{ id: 'A-old-visit' }]); await result;
    assert.deepEqual(env.messages, [{ id: 'A-new-visit' }]); assert.deepEqual(env.statuses, []);
  });
  await test('room-owned HTTP waits have a finite timeout and release their timer', async () => {
    const { env } = setup(); const result = env.roomWork.capture().wait(new Promise(() => {}), 25).catch((error) => error);
    await clock.tick(25); assert.match((await result).message, /timed out/); assert.equal(clock.timers.size, 0);
  });
  await test('polling has a wall-clock bound even if a GET never settles', async () => {
    const { env, fns } = setup(); env.conversationApi.generationJob = () => new Promise(() => {});
    const result = fns.pollGenerationJob('A', 'job-A').catch((error) => error);
    await clock.tick(5000); assert.equal((await result).message, 'pending'); assert.equal(clock.timers.size, 0);
  });
} finally {
  Object.assign(globalThis, original);
  Date.now = originalDateNow;
}
console.log(`${passed} chat lifecycle behavior regressions passed`);
