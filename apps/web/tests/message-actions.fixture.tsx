import React, { useLayoutEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { useMessageActions } from '../src/views/ConversationDetail/hooks/useMessageActions';
import { RoomWork } from '../src/utils/roomWork';
import type { ChatMessage } from '../src/components/organisms/BubbleList';

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const assert = (ok: unknown, label: string) => { if (!ok) throw new Error(label); };
const equal = (a: unknown, b: unknown, label: string) => assert(JSON.stringify(a) === JSON.stringify(b), `${label}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`);

export async function runMessageActionTests() {
  const results: { name: string; error?: string }[] = [];
  async function scenario(name: string, test: (f: ReturnType<typeof setup>) => Promise<void>) {
    const fixture = setup();
    try { await test(fixture); results.push({ name }); }
    catch (error) { results.push({ name, error: String(error) }); }
    finally { fixture.unmount(); }
  }
  function setup() {
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host);
    const request = deferred<ChatMessage>();
    let transport = request;
    const confirmation = deferred<boolean>();
    const context = deferred<void>();
    const reveal = deferred<void>();
    const listing = deferred<void>();
    const reveals: unknown[] = [], lists: string[] = [];
    const requests: unknown[] = [], contexts: string[] = [], writes: string[] = [];
    const readyVisitRef: { current: ReturnType<RoomWork['capture']> | null } = { current: null };
    const roomWork = new RoomWork();
    let live!: ReturnType<typeof useMessageActions> & { messages: ChatMessage[]; busy: boolean; status: string; room: string; setRoom: (room: string) => void; setBusy: (busy: boolean) => void; setStatus: (status: string) => void; setMessages: (messages: ChatMessage[]) => void; scroll: string; retype: () => void };
    let mounted = true;
    function Fixture() {
      const [room, setRoom] = useState('A');
      const [busy, setBusy] = useState(false);
      const [status, setStatus] = useState('ready');
      const [messages, setMessages] = useState<ChatMessage[]>([{ id: 'shared', content: 'original' }, { id: 'local_failed', failed: true }]);
      const [, setDraft] = useState('');
      const nextScrollModeRef = useRef('idle');
      useLayoutEffect(() => { const visit = roomWork.enter(room); readyVisitRef.current = visit; return () => visit.cancel(); }, [room]);
      const actions = useMessageActions({
        roomWork, readyVisitRef, selectedConversationId: room, busy,
        setBusy: (value) => { writes.push('busy'); setBusy(value); },
        setMessages: (value) => { writes.push('messages'); setMessages(value); },
        setStatus: (value) => { writes.push('status'); setStatus(value); },
        nextScrollModeRef, contextDrawerOpen: true,
        loadConversationContext: async (id) => { contexts.push(id); await context.promise; },
        loadBattleState: async (id) => { contexts.push(id); await context.promise; },
        confirmLocalizedDanger: () => confirmation.promise,
        locale: 'ko', t: (key) => key,
        updateMessage: (id, messageId, draft) => { requests.push(['edit', id, messageId, draft]); return transport.promise; },
        deleteMessage: (input) => { requests.push(['delete', input]); return transport.promise; },
        bulkDeleteMessages: (input) => { requests.push(['bulk', input]); return transport.promise; },
        regeneration: {
          regenerateMessage: (input) => { requests.push(['regenerate', input]); return transport.promise.then((message) => [message]); },
          revealServerMessages: (messages, id, visit) => { reveals.push([messages, id, visit.roomId]); return reveal.promise; },
          loadConversations: () => { lists.push('load'); return listing.promise; },
        },
      });
      live = { ...actions, messages, busy, status, room, setRoom, setBusy, setStatus, setMessages, scroll: nextScrollModeRef.current, retype: () => setDraft(String(Math.random())) };
      return <output>{JSON.stringify({ room, messages, busy, status, selected: actions.selectedMessageIds })}</output>;
    }
    flushSync(() => root.render(<Fixture />));
    function act(callback: () => void) { flushSync(callback); }
    function navigate(room: string) { act(() => live.setRoom(room)); act(() => { live.setMessages([{ id: 'shared', content: `new ${room}` }]); live.setStatus(`room ${room}`); live.setBusy(false); live.clearMessageSelection(); }); }
    function start(kind: string) {
      let promise!: Promise<unknown>;
      if (kind === 'bulk') act(() => { live.enterMessageSelectionMode('shared'); live.toggleMessageSelection('local_failed'); });
      act(() => { promise = kind === 'regenerate' ? live.regenerateFromMessage({ id: 'shared' }) : kind === 'edit' ? live.updateMessageBubble({ id: 'shared' }, { content: 'edited', emotion: 'calm' }) : kind === 'bulk' ? live.deleteSelectedMessages() : live.deleteMessageBubble({ id: 'shared' }); });
      return promise.catch((error) => error);
    }
    return { get live() { return live; }, nextTransport() { transport = deferred<ChatMessage>(); return transport; }, roomWork, readyVisitRef, request, confirmation, context, reveal, listing, reveals, lists, requests, contexts, writes, act, navigate, start,
      unmount() { if (mounted) { flushSync(() => root.unmount()); host.remove(); mounted = false; } },
      snapshot() { return JSON.stringify({ messages: live.messages, busy: live.busy, status: live.status, selected: live.selectedMessageIds, selection: live.messageSelectionMode }); },
    };
  }
  for (const kind of ['edit', 'delete', 'bulk']) {
    await scenario(`${kind}: normal success and captured payload`, async (f) => {
      const work = f.start(kind); f.confirmation.resolve(true); await tick();
      assert(f.live.busy, 'busy while request pending');
      equal(f.requests[0], kind === 'edit' ? ['edit', 'A', 'shared', { content: 'edited', emotion: 'calm' }] : [kind, kind === 'bulk' ? { conversationId: 'A', messageIds: ['shared'] } : { conversationId: 'A', messageId: 'shared' }], 'payload');
      f.request.resolve({ id: 'shared', content: 'edited' }); f.context.resolve(); await work; await tick();
      assert(!f.live.busy, 'busy released');
      assert(kind === 'edit' ? f.live.messages[0].content === 'edited' : !f.live.messages.some((m) => m.id === 'shared'), 'mutation visible');
      if (kind === 'bulk') assert(!f.live.messageSelectionMode && !f.live.selectedMessageIds.length, 'selection cleared');
    });
    await scenario(`${kind}: error preserved`, async (f) => {
      const work = f.start(kind); f.confirmation.resolve(true); await tick(); f.request.reject(new Error('fixture failure')); const result = await work; await tick();
      assert(f.live.status.includes('fixture failure'), 'error status'); assert(!f.live.busy, 'error releases busy'); assert(f.live.messages[0].content === 'original', 'failed mutation preserves messages');
      if (kind === 'edit') assert(result instanceof Error, 'edit form receives rejection');
    });
    for (const route of ['B', 'ABA', 'unmount']) for (const outcome of ['resolve', 'reject']) {
      await scenario(`${kind}: ${route} stale ${outcome} does not write or release newer work`, async (f) => {
        const work = f.start(kind); f.confirmation.resolve(true); await tick();
        if (route === 'unmount') f.unmount(); else { f.navigate('B'); if (route === 'ABA') f.navigate('A'); f.act(() => { f.live.setBusy(true); f.live.enterMessageSelectionMode('new-selection'); }); }
        const before = f.snapshot(), writes = f.writes.length;
        if (outcome === 'resolve') f.request.resolve({ id: 'shared', content: 'STALE' }); else f.request.reject(new Error('STALE'));
        f.context.resolve(); await work; await tick();
        equal(f.snapshot(), before, 'new visit state'); equal(f.writes.length, writes, 'no stale setter calls'); equal(f.contexts, [], 'no stale context requests');
      });
    }
    if (kind !== 'edit') {
      for (const route of ['B', 'ABA', 'unmount', 'busy']) await scenario(`${kind}: delayed confirmation after ${route} cannot submit`, async (f) => {
        const work = f.start(kind);
        if (route === 'unmount') f.unmount(); else if (route === 'busy') f.act(() => f.live.setBusy(true)); else { f.navigate('B'); if (route === 'ABA') f.navigate('A'); }
        const before = f.snapshot(), writes = f.writes.length;
        f.confirmation.resolve(true); f.request.resolve({ id: 'shared' }); f.context.resolve(); await work; await tick();
        equal(f.requests, [], 'no POST after stale confirmation'); equal(f.snapshot(), before, 'state untouched'); equal(f.writes.length, writes, 'no writes');
      });
      await scenario(`${kind}: cancelled confirmation`, async (f) => { const work = f.start(kind); const before = f.snapshot(); f.confirmation.resolve(false); await work; await tick(); equal(f.requests, [], 'cancel has no transport'); equal(f.snapshot(), before, 'cancel leaves state'); });
      await scenario(`${kind}: context await cannot set late status/finally`, async (f) => {
        const work = f.start(kind); f.confirmation.resolve(true); await tick(); f.request.resolve({ id: 'shared' }); await tick();
        equal(f.contexts, ['A', 'A'], 'context is explicitly room scoped'); f.navigate('B'); f.act(() => f.live.setBusy(true)); const before = f.snapshot(), writes = f.writes.length;
        f.context.resolve(); await work; await tick(); equal(f.snapshot(), before, 'late context preserves state'); equal(f.writes.length, writes, 'late context no setters');
      });
    }
  }
  for (const kind of ['edit', 'delete', 'bulk']) {
    await scenario(`${kind}: not-ready or mismatched visit rejects work`, async (f) => {
      f.readyVisitRef.current = null;
      const work = f.start(kind); f.confirmation.resolve(true); f.request.resolve({ id: 'shared' }); f.context.resolve(); await work; await tick();
      equal(f.requests, [], 'unready room cannot mutate old displayed messages');
      f.roomWork.enter('different-room'); await f.start(kind); equal(f.requests, [], 'mismatched room cannot mutate');
    });
  }
  for (const kind of ['delete', 'bulk']) await scenario(`${kind}: confirmation cannot overtake completed newer mutation`, async (f) => {
    const waiting = f.start(kind);
    const newer = f.start('edit'); f.request.resolve({ id: 'shared', content: 'newer edit' }); await newer; await tick();
    const before = f.snapshot(); f.confirmation.resolve(true); f.context.resolve(); await waiting; await tick();
    equal(f.requests.length, 1, 'only newer edit submitted'); equal(f.snapshot(), before, 'older confirmation cannot change newer state');
  });
  await scenario('same-tick duplicate edits reserve busy synchronously', async (f) => {
    const first = f.start('edit'), second = f.start('edit');
    equal(f.requests.length, 1, 'single reservation'); f.request.resolve({ id: 'shared', content: 'once' }); await Promise.all([first, second]);
  });
  await scenario('route entry releases owned busy and newer hook work owns its finally', async (f) => {
    const old = f.start('edit'); f.act(() => f.live.setRoom('B'));
    assert(!f.live.busy, 'new visit releases old hook busy without waiting for response');
    const response = f.nextTransport();
    const newer = f.start('edit'); equal(f.requests.length, 2, 'new room may submit while old transport is still pending');
    f.request.resolve({ id: 'shared', content: 'obsolete' }); await old; await tick();
    assert(f.live.busy, 'old finally cannot release newer actual hook work');
    response.resolve({ id: 'shared', content: 'newest' }); await newer; await tick();
    equal(f.live.status, '버블 수정 완료', 'new work succeeds'); assert(!f.live.busy, 'new work releases its reservation');
  });
  await scenario('regenerate: normal payload/reveal/context and busy release', async (f) => {
    const work = f.start('regenerate'); equal(f.requests, [['regenerate', { conversationId: 'A', messageId: 'shared' }]], 'regenerate payload');
    f.request.resolve({ id: 'new-reply' }); await tick();
    equal(f.reveals, [[[null, { id: 'new-reply' }], 'regen_shared', 'A']], 'existing reveal contract');
    f.reveal.resolve(); f.listing.resolve(); f.context.resolve(); await work; await tick();
    assert(!f.live.busy, 'release regenerate busy'); equal(f.live.status, '재생성 완료', 'success status'); equal(f.contexts, ['A', 'A'], 'room context');
  });
  for (const stage of ['transport', 'reveal', 'listing', 'context']) for (const route of ['B', 'ABA', 'unmount']) {
    await scenario(`regenerate: ${stage} late completion after ${route}`, async (f) => {
      const work = f.start('regenerate');
      if (stage !== 'transport') { f.request.resolve({ id: 'new-reply' }); await tick(); }
      if (['listing', 'context'].includes(stage)) { f.reveal.resolve(); await tick(); }
      if (stage === 'context') { f.listing.resolve(); await tick(); }
      if (route === 'unmount') f.unmount(); else { f.navigate('B'); if (route === 'ABA') f.navigate('A'); f.act(() => f.live.setBusy(true)); }
      const before = f.snapshot(), writes = f.writes.length, calls = [f.reveals.length, f.lists.length, f.contexts.length];
      f.request.resolve({ id: 'late' }); f.reveal.resolve(); f.listing.resolve(); f.context.resolve(); await work; await tick();
      equal(f.snapshot(), before, 'stale regeneration does not write'); equal(f.writes.length, writes, 'no stale setters'); equal([f.reveals.length, f.lists.length, f.contexts.length], calls, 'no next-stage calls');
    });
  }
  await scenario('regenerate: current error visible, stale error silent', async (f) => {
    const work = f.start('regenerate'); f.request.reject(new Error('regenerate failure')); await work; await tick();
    assert(f.live.status.includes('regenerate failure') && !f.live.busy, 'normal regeneration error');
    const stale = f.start('regenerate'); f.navigate('B'); const before = f.snapshot(); await stale; await tick(); equal(f.snapshot(), before, 'stale error suppressed');
  });
  await scenario('delete captures concrete message ID before confirmation', async (f) => {
    const message = { id: 'shared' };
    let work!: Promise<void>;
    f.act(() => { work = f.live.deleteMessageBubble(message); }); message.id = 'changed-after-click';
    f.confirmation.resolve(true); await tick();
    equal(f.requests, [['delete', { conversationId: 'A', messageId: 'shared' }]], 'captured ID cannot be redirected');
    f.request.resolve({ id: 'shared' }); f.context.resolve(); await work;
  });
  await scenario('local-only bulk confirmation removes locally without transport', async (f) => {
    f.act(() => f.live.enterMessageSelectionMode('local_failed'));
    const work = f.live.deleteSelectedMessages(); f.confirmation.resolve(true); f.context.resolve(); await work; await tick();
    equal(f.requests, [], 'local-only bulk has no transport'); equal(f.live.messages.map((m) => m.id), ['shared'], 'only local selection removed');
  });
  await scenario('local bubbles dismiss, persisted failed bubbles require confirmation and DELETE', async (f) => {
    f.act(() => f.live.setMessages([{ id: 'local_only' }, { id: 'failed-server', failed: true }]));
    await f.live.deleteMessageBubble({ id: 'local_only' }); await tick();
    equal(f.live.messages.map(m => m.id), ['failed-server'], 'only local row dismissed'); equal(f.requests, [], 'local dismissal has no transport');
    const work = f.live.deleteMessageBubble({ id: 'failed-server', failed: true }); await tick();
    equal(f.requests, [], 'persisted deletion waits for confirmation');
    f.confirmation.resolve(true); await tick();
    equal(f.requests, [['delete', { conversationId: 'A', messageId: 'failed-server' }]], 'persisted failed source uses DELETE');
    f.request.resolve({ id: 'failed-server' }); f.context.resolve(); await work; await tick();
    equal(f.live.messages, [], 'confirmed persisted source removed');
  });
  await scenario('clear selection restores status without overwriting newer status', async (f) => {
    f.act(() => f.live.enterMessageSelectionMode('shared')); f.act(() => f.live.clearMessageSelection());
    assert(f.live.status !== '복사하거나 삭제할 버블을 선택하세요', 'instruction cleared');
    f.act(() => f.live.enterMessageSelectionMode('shared')); f.act(() => f.live.setStatus('new result')); f.act(() => f.live.clearMessageSelection()); equal(f.live.status, 'new result', 'newer status retained');
  });
  await scenario('callbacks remain stable on composer rerender', async (f) => {
    const callbacks = [f.live.updateMessageBubble, f.live.deleteMessageBubble, f.live.deleteSelectedMessages, f.live.enterMessageSelectionMode, f.live.toggleMessageSelection, f.live.clearMessageSelection];
    f.act(() => f.live.retype()); equal(callbacks.map((cb, i) => cb === [f.live.updateMessageBubble, f.live.deleteMessageBubble, f.live.deleteSelectedMessages, f.live.enterMessageSelectionMode, f.live.toggleMessageSelection, f.live.clearMessageSelection][i]), [true, true, true, true, true, true], 'callback identities');
  });
  return results;
}
Object.assign(window, { runMessageActionTests });
