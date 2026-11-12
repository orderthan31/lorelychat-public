import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/assetDecisionRefresh.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { createAssetDecisionRefresh } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function fakeClock() {
  let now = 0, serial = 0;
  const timers = new Map();
  return {
    now: () => now,
    setTimeout(fn, ms) { const id = ++serial; timers.set(id, { at: now + ms, fn }); return id; },
    clearTimeout(id) { timers.delete(id); },
    async advance(ms) {
      const end = now + ms;
      while (true) {
        const next = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
        if (!next || next[1].at > end) break;
        now = next[1].at; timers.delete(next[0]); next[1].fn(); await settle();
      }
      now = end; await settle();
    },
    get size() { return timers.size; },
  };
}
const row = (patch = {}) => ({ id: 'm1', conversation_id: 'A', speaker_id: 'character', content: 'original', assets: [], asset_decision_pending: true, metadata: { raw_body: 'original', note: 'keep' }, tts_audio_url: 'keep.mp3', ...patch });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function fixture(read, initial = [row()], options = {}) {
  const clock = options.clock ?? fakeClock();
  let rows = initial, active = true;
  const calls = [], queued = [];
  const owner = createAssetDecisionRefresh({
    roomId: options.roomId ?? 'A', clock, isCurrent: () => active,
    read(ids) { calls.push(ids); return read(ids, calls.length); },
    merge(update) {
      if (options.deferMerge) queued.push(update);
      else { rows = update(rows); owner.update(rows); }
    },
  });
  owner.update(rows);
  return { owner, clock, calls, queued, get rows() { return rows; }, set rows(value) { rows = value; owner.update(rows); }, cancel() { active = false; owner.stop(); }, flush() { for (const update of queued.splice(0)) rows = update(rows); owner.update(rows); } };
}
let passed = 0;
async function test(name, work) { await work(); passed++; console.log(`ok - ${name}`); }

await test('default browser timers retain the global host receiver', async () => {
  const originalSet = globalThis.setTimeout, originalClear = globalThis.clearTimeout;
  let scheduled = 0, cleared = 0;
  try {
    globalThis.setTimeout = function(callback, ms) {
      assert.equal(this, globalThis, 'native timer must not receive the Clock object');
      assert.equal(typeof callback, 'function'); assert.equal(ms, 500);
      scheduled++; return 7;
    };
    globalThis.clearTimeout = function(timer) {
      assert.equal(this, globalThis); assert.equal(timer, 7); cleared++;
    };
    const owner = createAssetDecisionRefresh({roomId:'A',isCurrent:()=>true,read:async()=>[],merge:()=>{}});
    owner.update([row()]); owner.stop();
    assert.equal(scheduled, 1); assert.equal(cleared, 1);
  } finally { globalThis.setTimeout = originalSet; globalThis.clearTimeout = originalClear; }
});

await test('delayed pending -> ready merges only assets and pending, retaining all history', async () => {
  const result = deferred();
  const old = row({ id: 'older', asset_decision_pending: false });
  const f = fixture(() => result.promise, [old, row()]);
  await f.clock.advance(500);
  assert.deepEqual(f.calls, [['m1']]);
  f.rows = [...f.rows, row({ id: 'newer', asset_decision_pending: false })];
  result.resolve([row({ assets: [{ id: 'real', image_url: '/real.png' }], asset_decision_pending: false, tts_audio_url: 'wrong.mp3', metadata: { raw_body: 'original', note: 'wrong' } })]);
  await settle();
  assert.equal(f.rows.length, 3);
  assert.equal(f.rows[0], old);
  assert.equal(f.rows[1].assets[0].id, 'real');
  assert.equal(f.rows[1].metadata.note, 'keep');
  assert.equal(f.rows[1].tts_audio_url, 'keep.mp3');
  await f.clock.advance(20000);
  assert.equal(f.calls.length, 1);
});
await test('pending response retries then no selection completes without invented images', async () => {
  const f = fixture((ids, count) => Promise.resolve([row({ asset_decision_pending: count === 1 })]));
  await f.clock.advance(1000);
  assert.equal(f.calls.length, 2);
  assert.deepEqual(f.rows[0].assets, []);
  assert.equal(f.rows[0].asset_decision_pending, false);
  await f.clock.advance(20000);
  assert.equal(f.calls.length, 2);
});
await test('request failure retries quietly within a fixed budget, rerenders cannot reset it', async () => {
  const f = fixture(() => Promise.reject(new Error('offline')));
  for (let i = 0; i < 40; i++) { await f.clock.advance(500); f.owner.update([...f.rows]); }
  assert.ok(f.calls.length > 1 && f.calls.length <= 30);
  const count = f.calls.length;
  await f.clock.advance(20000);
  assert.equal(f.calls.length, count);
  assert.equal(f.clock.size, 0);
  assert.equal(f.rows[0].failed, undefined);
});
await test('hung response expires at 15 seconds and cannot write afterward', async () => {
  const d = deferred(); const f = fixture(() => d.promise);
  await f.clock.advance(15000);
  assert.equal(f.calls.length, 1); assert.equal(f.clock.size, 0);
  d.resolve([row({ assets: [{ id: 'too-late' }], asset_decision_pending: false })]);
  await settle(); assert.deepEqual(f.rows[0].assets, []);
});
await test('always-pending results stop, but a newly generated target gets its own budget', async () => {
  const f = fixture(ids => Promise.resolve(ids.map(id => row({ id }))));
  await f.clock.advance(20000);
  const count = f.calls.length;
  assert.ok(count <= 30);
  f.rows = [...f.rows, row({ id: 'new' })];
  await f.clock.advance(500);
  assert.deepEqual(f.calls.at(-1), ['new']);
  f.cancel();
});
for (const change of ['removed', 'edited', 'raw-body-edited', 'failed', 'speaker-changed', 'room-changed']) {
  await test(`${change} target cannot be overwritten or resurrected`, async () => {
    const d = deferred(); const f = fixture(() => d.promise);
    await f.clock.advance(500);
    f.rows = change === 'removed' ? [] : [row(change === 'edited' ? { content: 'edited' } : change === 'raw-body-edited' ? { metadata: { raw_body: 'edited' } } : change === 'failed' ? { failed: true } : change === 'speaker-changed' ? { speaker_id: 'different' } : { conversation_id: 'B' })];
    const expected = f.rows;
    d.resolve([row({ assets: [{ id: 'stale' }], asset_decision_pending: false })]);
    await settle(); assert.deepEqual(f.rows, expected);
    await f.clock.advance(20000); assert.equal(f.calls.length, 1);
  });
}
await test('A -> B -> A isolates old success, rejection and finally from new visit', async () => {
  const clock = fakeClock(), old = deferred(), fresh = deferred(), lateError = deferred();
  const a = fixture(() => old.promise, [row()], { clock });
  await clock.advance(500); a.cancel();
  const b = fixture(() => lateError.promise, [row({ conversation_id: 'B' })], { clock, roomId: 'B' });
  await clock.advance(500); b.cancel();
  const nextA = fixture(() => fresh.promise, [row()], { clock });
  await clock.advance(500);
  old.resolve([row({ assets: [{ id: 'old' }], asset_decision_pending: false })]);
  lateError.reject(new Error('late B'));
  await settle(); assert.deepEqual(nextA.rows[0].assets, []);
  fresh.resolve([row({ assets: [{ id: 'new' }], asset_decision_pending: false })]);
  await settle(); assert.equal(nextA.rows[0].assets[0].id, 'new');
  assert.deepEqual(a.rows[0].assets, []);
  await clock.advance(20000); assert.equal(nextA.calls.length, 1);
});
await test('hidden tab cancels writes and resumes only the remaining original budget', async () => {
  const d = deferred(); const f = fixture((ids, count) => count === 1 ? d.promise : Promise.resolve([row({ asset_decision_pending: false })]));
  await f.clock.advance(500); f.owner.setVisible(false);
  d.resolve([row({ assets: [{ id: 'hidden' }], asset_decision_pending: false })]);
  await f.clock.advance(1000); assert.deepEqual(f.rows[0].assets, []); assert.equal(f.calls.length, 1);
  f.owner.setVisible(true); await f.clock.advance(500); assert.equal(f.calls.length, 2);
  const expired = fixture(() => Promise.resolve([row()]));
  expired.owner.setVisible(false); await expired.clock.advance(16000); expired.owner.setVisible(true);
  await expired.clock.advance(1000); assert.equal(expired.calls.length, 0);
});
await test('deferred React updater works after finally, but rechecks edits and cancelled visit', async () => {
  const f = fixture(() => Promise.resolve([row({ assets: [{ id: 'real' }], asset_decision_pending: false })]), [row()], { deferMerge: true });
  await f.clock.advance(500); f.flush(); assert.equal(f.rows[0].assets[0].id, 'real');
  for (const cancel of [false, true]) {
    const g = fixture(() => Promise.resolve([row({ assets: [{ id: 'stale' }], asset_decision_pending: false })]), [row()], { deferMerge: true });
    await g.clock.advance(500);
    if (cancel) g.cancel(); else g.rows = [row({ content: 'edited' })];
    g.flush(); assert.deepEqual(g.rows[0].assets, []);
  }
});
await test('unrequested, mismatched and failed response rows are ignored; no unrelated network', async () => {
  const f = fixture(() => Promise.resolve([row({ id: 'other', assets: [{ id: 'bad' }] }), row({ content: 'server-edited', assets: [{ id: 'bad' }] }), row({ failed: true, assets: [{ id: 'bad' }] })]));
  await f.clock.advance(500); assert.deepEqual(f.rows[0].assets, []); assert.deepEqual(f.calls, [['m1']]); f.cancel();
  const empty = fixture(() => { throw new Error('must not read'); }, [row({ asset_decision_pending: false }), row({ id: 'failed', failed: true }), row({ id: 'local_1' }), row({ id: 'wrong-room', conversation_id: 'B' })]);
  await empty.clock.advance(20000); assert.deepEqual(empty.calls, []);
});

// Exercise the actual App owner wiring with synthetic hooks/API, not a parallel implementation.
await test('endpoint batches stay bounded and fairly refresh all loaded targets', async () => {
  const rows = Array.from({ length: 125 }, (_, index) => row({ id: `m${index}` }));
  const f = fixture(ids => Promise.resolve(ids.map(id => row({ id }))), rows);
  await f.clock.advance(1000);
  assert.equal(f.calls.length, 2);
  assert.ok(f.calls.every(ids => ids.length <= 100));
  assert.equal(new Set(f.calls.flat()).size, 125);
  f.cancel();
});
await test('App effects use the targeted endpoint and current loaded messages without status/busy writes', async () => {
  const app = readFileSync(new URL('../src/app/App.tsx', import.meta.url), 'utf8');
  const start = app.indexOf('  useLayoutEffect(() => {\n    if (!selectedConversationId) return;');
  const end = app.indexOf('  const { messageSelectionMode', start);
  assert.ok(start > 0 && end > start);
  const integration = app.slice(start, end);
  assert.doesNotMatch(integration, /setBusy|setStatus|conversationApi\.messages\(/);
  const effectJs = ts.transpileModule(integration, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const effects = [], calls = [], visit = { current: true }, refreshRef = { current: null };
  const listeners = new Map();
  const doc = { visibilityState: 'visible', addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) };
  let updated;
  new Function('useLayoutEffect', 'selectedConversationId', 'roomWork', 'createAssetDecisionRefresh', 'conversationApi', 'setMessages', 'assetDecisionRefreshRef', 'document', 'readyVisitRef', 'messages', effectJs)(
    fn => effects.push(fn), 'A', { capture: () => visit }, opts => { calls.push(opts); return { stop() {}, setVisible() {}, update(rows) { updated = rows; } }; },
    { messagesByIds: async (room, ids) => { calls.push({ room, ids }); return []; } }, () => {}, refreshRef, doc, { current: visit }, [row()],
  );
  const cleanup = effects[0](); effects[1]();
  assert.equal(updated[0].id, 'm1'); await calls[0].read(['m1']); assert.deepEqual(calls[1], { room: 'A', ids: ['m1'] });
  visit.current = false; assert.equal(calls[0].isCurrent(), false);
  cleanup(); assert.equal(refreshRef.current, null); assert.equal(listeners.size, 0);
});
console.log(`asset decision refresh: ${passed} tests passed`);
