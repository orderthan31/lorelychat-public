import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { QueryClient, QueryObserver } from '@tanstack/react-query';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const compile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const load = source => import(`data:text/javascript;base64,${Buffer.from(compile(source)).toString('base64')}`);
function functions(path, owner, names, env) {
  const source = ts.createSourceFile(path, read(path), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const scope = owner ? source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === owner).body : source;
  const nodes = scope.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text));
  assert.equal(nodes.length, names.length, `actual behavior lookup in ${path}`);
  return new Function('env', `with (env) { ${compile(nodes.map(node => node.getText(source).replace(/^export /, '')).join('\n'))}; return { ${names.join(',')} }; }`)(env);
}
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const { parseAssetTags } = await load(read('../src/utils/assetTags.ts'));
const constants = await load(read('../src/constants/domain.ts'));
const { createEmptyWorld, normalizeWorld } = await load(read('../src/utils/worldSettings.ts').replace("import { DEFAULT_GENRE_MODE } from '../constants/domain';", `const DEFAULT_GENRE_MODE = ${JSON.stringify(constants.DEFAULT_GENRE_MODE)};`));
const { RoomWork } = await load(read('../src/utils/roomWork.ts'));
const { queryKeys } = await load(read('../src/api/queryKeys.ts'));
const { refreshAfterMutation } = await load(read('../src/hooks/refreshAfterMutation.ts'));
let passed = 0;
async function test(name, body) { await body(); console.log(`ok ${++passed} - ${name}`); }

await test('world defaults are fresh, normalized and retain authored fields', () => {
  const first = createEmptyWorld(), second = createEmptyWorld();
  first.tags.push('edited'); first.title = 'unsaved';
  assert.equal(second.title, ''); assert.deepEqual(second.tags, []);
  assert.deepEqual(normalizeWorld(), second);
  assert.equal(normalizeWorld({ title: 'draft', genre_mode: '' }).genre_mode, constants.DEFAULT_GENRE_MODE);
  const authored = normalizeWorld({ title: 'draft', genre_mode: 'battle', tags: ['Keep Case'], enabled: false });
  assert.equal(authored.genre_mode, 'battle'); assert.equal(authored.enabled, false); assert.deepEqual(authored.tags, ['Keep Case']);
});
await test('asset tags preserve backend case, spaces, ordering and duplicates', () => {
  for (const [input, expected] of [
    ['\u0085Tag\u001f,\ufeffKeep BOM\ufeff', ['Tag', '\ufeffKeep BOM\ufeff']],
    [undefined, []], ['', []], [' ,\n  ', []],
    ['  Happy Face, Happy Face\n한글 태그,UPPER_case ', ['Happy Face', 'Happy Face', '한글 태그', 'UPPER_case']],
    [' a\tb , C  D\r\n next ', ['a\tb', 'C  D', 'next']],
  ]) assert.deepEqual(parseAssetTags(input), expected);
});
function ownership(routeWork, writes) {
  const { useOperationOwnership } = functions('../src/hooks/useOperationOwnership.ts', null, ['useOperationOwnership'], { useRef: value => ({ current: value }) });
  return useOperationOwnership(routeWork, value => writes.push(['busy', value]), value => writes.push(['status', value]));
}
await test('shared feedback ownership fences old visits, same-operation completion and late finally', () => {
  const routeWork = new RoomWork(); routeWork.enter('A'); const writes = [];
  const guard = ownership(routeWork, writes);
  const old = guard('upload', { setDraft: value => writes.push(['draft', value]) }); old.setBusy(true);
  const current = guard('upload', { setDraft: value => writes.push(['draft', value]) }); current.setBusy(true);
  old.setDraft('stale'); old.setBusy(false); old.setStatus('stale');
  assert.deepEqual(writes, [['busy', true], ['busy', true]]);
  const other = guard('save', {}); other.setBusy(true); current.setBusy(false);
  assert.equal(writes.length, 3); other.setBusy(false); assert.deepEqual(writes.at(-1), ['busy', false]);
  routeWork.enter('B'); routeWork.enter('A'); current.setDraft('stale A'); current.setStatus('stale A');
  assert.equal(writes.length, 4);
});
const assetPath = '../src/views/CharacterAssets/useAssetEditor.ts';
function assetFixture() {
  const routeWork = new RoomWork(); routeWork.enter('characterAssets:A'); const writes = [], payloads = [], proposals = [];
  const guardUI = ownership(routeWork, writes);
  const env = {
    routeId: 'A', routeIdRef: { current: 'A' }, routeWork, editorCharacterId: 'A', ordinaryCharacterId: 'A',
    characterReview: false, proposal: undefined, characterAssets: [{ id: 'asset-A', image_url: '/old' }],
    assetDraft: { label: ' label ', description: '', image_url: ' /url ', tags: ' Happy Face, Happy Face ', mood_tags: 'UPPER', scene_tags: '', outfit_tags: '', pose_tags: '', expression_tags: '', priority: 50, enabled: true, is_default: true },
    parseAssetTags, t: text => text, locale: 'en', formatDangerConfirmation: () => 'delete?', confirmLocalizedDanger: async () => true,
    ownUI: operation => guardUI(operation, { setAssetDraft: value => writes.push(['draft', value]), setCharacterAssets: value => writes.push(['assets', value]), setCharacterAssetsLoading: value => writes.push(['loading', value]) }),
    loadCharacterAssets: async () => writes.push(['reload', 'A']),
    characterMutations: { createAsset: { mutateAsync: async input => { payloads.push(input); return { image_url: '/created' }; } }, uploadAsset: { mutateAsync: async input => { payloads.push(input); return { image_url: '/uploaded' }; } }, patchAsset: { mutateAsync: async input => payloads.push(input) } },
  };
  const fns = functions(assetPath, 'useAssetEditor', ['normalizeAssetDraft', 'createCharacterAsset', 'uploadCharacterAsset', 'patchCharacterAsset', 'setDefaultCharacterAsset'], env);
  return { env, fns, writes, payloads, proposals };
}
await test('asset URL create and file create submit the same normalized tag array', async () => {
  const { env, fns, payloads } = assetFixture();
  await fns.createCharacterAsset(); await fns.uploadCharacterAsset(new Blob(['fixture'], { type: 'image/png' }));
  assert.deepEqual(payloads[0].payload.tags, ['Happy Face', 'Happy Face']);
  assert.deepEqual(JSON.parse(payloads[1].formData.get('tags')), payloads[0].payload.tags);
  assert.equal(payloads[0].payload.label, 'label'); assert.equal(payloads[0].payload.image_url, '/url');
  env.editorCharacterId = 'B'; await fns.createCharacterAsset(); assert.equal(payloads.length, 2);
});
await test('native asset proposal chooses only draft avatar and never ordinary default', async () => {
  const { env, fns, payloads, proposals } = assetFixture();
  env.characterReview = true; env.proposal = { token: 'review-1', characterId: 'A', isCurrent: token => token === 'review-1', chooseAvatar: url => proposals.push(url) };
  await fns.createCharacterAsset(); assert.equal(payloads[0].payload.is_default, false); assert.deepEqual(proposals, ['/created']);
  await fns.setDefaultCharacterAsset('asset-A'); assert.deepEqual(proposals, ['/created', '/old']);
  assert.equal(payloads.length, 1);
});
await test('late asset completion cannot reset a foreign draft or apply a stale proposal', async () => {
  const { env, fns, writes, proposals } = assetFixture(); const pending = deferred();
  env.characterReview = true; env.proposal = { token: 'review-1', isCurrent: () => true, chooseAvatar: url => proposals.push(url) };
  env.characterMutations.createAsset.mutateAsync = () => pending.promise;
  const work = fns.createCharacterAsset(); env.routeWork.enter('characterAssets:B'); pending.resolve({ image_url: '/late' }); await work;
  assert.deepEqual(writes, [['busy', true]]); assert.deepEqual(proposals, []);
});
await test('asset patch rejects foreign asset identity before sending', async () => {
  const { env, fns, payloads } = assetFixture();
  await fns.patchCharacterAsset('foreign', {}); assert.equal(payloads.length, 0);
  env.ordinaryCharacterId = 'B'; await fns.patchCharacterAsset('asset-A', {}); assert.equal(payloads.length, 0);
});

await test('asset route cleanup prevents old A query from hydrating a new A visit', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const source = ts.createSourceFile(assetPath, read(assetPath), ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const hook = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'useAssetEditor');
  const effect = hook.body.statements.find(node => ts.isExpressionStatement(node) && ts.isCallExpression(node.expression) && node.expression.expression.getText(source) === 'useLayoutEffect');
  let cleanup;
  new Function('useLayoutEffect', 'queryClient', 'queryKeys', 'routeId', 'routePage', compile(effect.getText(source)))(fn => { cleanup = fn(); }, client, queryKeys, 'A', 'characterAssets');
  const old = deferred(), fresh = deferred(), key = queryKeys.characterAssets('A');
  const first = client.fetchQuery({ queryKey: key, queryFn: () => old.promise }).catch(() => undefined);
  cleanup();
  const second = client.fetchQuery({ queryKey: key, queryFn: () => fresh.promise });
  fresh.resolve([{ id: 'new-A' }]); await second;
  old.resolve([{ id: 'old-A' }]); await first; await flush();
  assert.deepEqual(client.getQueryData(key), [{ id: 'new-A' }]); client.clear();
});
await test('canonical conversation list sorts once and preserves bounded participants/unread', async () => {
  const calls = [];
  const rows = Array.from({ length: 14 }, (_, i) => ({ id: `room-${i}`, updated_at: String(i).padStart(2, '0'), has_unread: true, unread_count: 2 }));
  const { loadConversationList } = functions('../src/api/resources.ts', null, ['loadConversationList'], { api: async path => {
    calls.push(path); if (path === '/conversations') return rows;
    if (path.includes('room-12/')) throw new Error('participant unavailable');
    return [{ id: path.split('/')[2], type: 'character' }];
  } });
  const result = await loadConversationList();
  assert.equal(calls.filter(path => path === '/conversations').length, 1); assert.equal(calls.length, 13);
  assert.equal(result[0].id, 'room-13'); assert.deepEqual(result[0].participants, [{ id: 'room-13', type: 'character' }]);
  assert.deepEqual(result[1].participants, []); assert.equal(result[0].unread_count, 2); assert.equal(result[0].has_unread, true);
  assert.equal(result[12].participants, undefined); assert.equal(rows[0].id, 'room-0');
});
await test('character save refreshes the active Query list once, with no manual duplicate GET', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity } } });
  let reads = 0, saves = 0; const saved = { id: 'A', name: 'saved', persona: 'persona' };
  const characterApi = { save: async () => { saves++; return saved; } };
  client.setQueryData(queryKeys.characters, [{ id: 'A', name: 'old' }]);
  const observer = new QueryObserver(client, { queryKey: queryKeys.characters, queryFn: async () => { reads++; return [saved]; } });
  const unsubscribe = observer.subscribe(() => {});
  try {
    const useMutation = ({ mutationFn, onSuccess }) => ({ mutateAsync: async value => { const result = await mutationFn(value); await onSuccess?.(result); return result; } });
    const { useCharacterMutations } = functions('../src/hooks/useCharacterMutations.ts', null, ['useCharacterMutations'], { useQueryClient: () => client, queryKeys, useMutation, characterApi, refreshAfterMutation });
    const navigations = [];
    const { saveCharacter } = functions('../src/app/App.tsx', 'App', ['saveCharacter'], {
      editorCharacterDraft: { id: 'A', name: 'edited', persona: 'persona' }, route: { page: 'characterDetail' }, routeId: 'A', characterReview: false,
      ownUI: () => ({ setBusy() {}, setStatus() {}, navigate: path => navigations.push(path) }), t: text => text,
      characterMutations: useCharacterMutations(), normalizeCharacterForApi: value => value,
      loadCharacters: async () => { throw new Error('redundant manual GET'); },
    });
    await saveCharacter(); assert.equal(saves, 1); assert.equal(reads, 1);
    assert.deepEqual(client.getQueryData(queryKeys.characters), [saved]); assert.deepEqual(navigations, ['/characters/A']);
  } finally { unsubscribe(); client.clear(); }
});
await test('post-commit refresh failure identifies the completed write without automatic replay', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity } } });
  let reads = 0;
  client.setQueryData(queryKeys.characters, []);
  const observer = new QueryObserver(client, { queryKey: queryKeys.characters, queryFn: async () => { reads++; throw new Error('offline'); } });
  const unsubscribe = observer.subscribe(() => {});
  try {
    await assert.rejects(refreshAfterMutation(client, queryKeys.characters), /Mutation completed;.*offline.*Reload instead of submitting again/);
    assert.equal(reads, 1);
  } finally { unsubscribe(); client.clear(); }
});
await test('generation receipt is returned even if background list refresh fails', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity } } });
  client.setQueryData(queryKeys.conversations, []);
  const observer = new QueryObserver(client, { queryKey: queryKeys.conversations, queryFn: async () => { throw new Error('offline list'); } });
  const unsubscribe = observer.subscribe(() => {});
  try {
    const receipt = { job: { id: 'job-A' }, incoming_message: { id: 'source' } }; let posts = 0;
    const useMutation = ({ mutationFn, onSuccess }) => ({ mutateAsync: async value => { const result = await mutationFn(value); await onSuccess?.(result); return result; } });
    const { useConversationMutations } = functions('../src/hooks/useConversationMutations.ts', null, ['useConversationMutations'], {
      useQueryClient: () => client, useMutation, queryKeys, refreshAfterMutation,
      conversationApi: { createMessageJob: async () => { posts++; return receipt; } },
    });
    assert.equal(await useConversationMutations().createMessageJob.mutateAsync({ conversationId: 'A', payload: {} }), receipt);
    assert.equal(posts, 1);
  } finally { unsubscribe(); client.clear(); }
});
await test('Query owns reversed refresh completions and room read patches without losing previews', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity } } });
  const old = deferred(), fresh = deferred(); let calls = 0;
  client.setQueryData(queryKeys.conversations, [{ id: 'A', has_unread: true, participants: [{ id: 'character' }] }]);
  const observer = new QueryObserver(client, { queryKey: queryKeys.conversations, queryFn: () => (++calls === 1 ? old.promise : fresh.promise) });
  const unsubscribe = observer.subscribe(() => {});
  try {
    const first = client.invalidateQueries({ queryKey: queryKeys.conversations }); await flush();
    const second = client.invalidateQueries({ queryKey: queryKeys.conversations }); await flush();
    fresh.resolve([{ id: 'A', title: 'fresh', has_unread: true, participants: [{ id: 'character' }] }]); await second;
    old.resolve([{ id: 'A', title: 'old' }]); await first; await flush();
    const setConversations = update => client.setQueryData(queryKeys.conversations, current => update(current));
    let selected = { id: 'B', has_unread: true };
    const { markConversationRead } = functions('../src/views/ConversationDetail/hooks/useConversationSession.ts', 'useConversationSession', ['markConversationRead'], {
      conversationApi: { markRead: async () => ({}) }, setConversations, setConversation: update => { selected = update(selected); },
    });
    await markConversationRead('A');
    const [room] = client.getQueryData(queryKeys.conversations);
    assert.equal(room.title, 'fresh'); assert.deepEqual(room.participants, [{ id: 'character' }]); assert.equal(room.has_unread, false); assert.equal(room.unread_count, 0);
    assert.equal(selected.has_unread, true); assert.equal(calls, 2);
  } finally { unsubscribe(); client.clear(); }
});
console.log(`feature controllers: ${passed} behavioral cases passed`);
