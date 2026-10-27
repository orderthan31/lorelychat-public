import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

// Exercise the actual App handlers without mounting unrelated resources or
// reaching any API. AST extraction keeps the tests tied to production behavior.
const source = readFileSync(new URL('../src/app/App.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const compile = text => ts.transpileModule(text, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const model = await import(`data:text/javascript;base64,${Buffer.from(compile(readFileSync(new URL('../src/types/chatCommands.ts', import.meta.url), 'utf8'))).toString('base64')}`);
const { normalizeChatCommandDraft, chatCommandSavePayload } = model;
function handler(name, dependencies) {
  let found;
  function visit(node) { if (ts.isFunctionDeclaration(node) && node.name?.text === name) found = node.getText(ast); ts.forEachChild(node, visit); }
  visit(ast); assert.ok(found, `Production handler ${name} exists`);
  return new Function(...Object.keys(dependencies), `${compile(found)}; return ${name};`)(...Object.values(dependencies));
}
assert.match(source, /\[chatCommandDraft, setChatCommandDraft\] = useState<any>\(normalizeChatCommandDraft\(\)\)/);
for (const selection of [undefined, [], ['note', 'table'], ['future_block', 'cards']]) {
  const draft = { name: '!synthetic', postprocess_prompt: 'Synthetic instruction', ...(selection === undefined ? {} : { postprocess_block_types: selection }) };
  assert.deepEqual(normalizeChatCommandDraft(draft).postprocess_block_types, selection ?? []);
  assert.deepEqual(chatCommandSavePayload(draft).postprocess_block_types, selection ?? []);
  for (const id of [undefined, 'synthetic']) {
    let submitted, nextDraft, route;
    const busy = [];
    const dependencies = {
      chatCommandDraft: { ...draft, id }, chatCommandSavePayload, normalizeChatCommandDraft,
      setStatus: () => {}, t: key => key, setBusy: value => busy.push(value),
      settingsMutations: { saveChatCommand: { mutateAsync: async input => { submitted = input; return { ...input.payload, id: 'synthetic' }; } } },
      loadChatCommands: async () => {}, setChatCommandDraft: value => { nextDraft = value; }, navigate: value => { route = value; },
    };
    await handler('saveChatCommand', dependencies)();
    assert.equal(submitted.id, id ?? null);
    assert.deepEqual(submitted.payload.postprocess_block_types, selection ?? []);
    assert.deepEqual(nextDraft.postprocess_block_types, selection ?? []);
    assert.equal(route, '/chat-commands/synthetic'); assert.deepEqual(busy, [true, false]);
  }
}
let deleted, reset, navigated;
await handler('deleteChatCommand', {
  confirmLocalizedDanger: async () => true, formatDangerConfirmation: () => '', locale: 'en',
  settingsMutations: { deleteChatCommand: { mutateAsync: async id => { deleted = id; } } },
  loadChatCommands: async () => {}, setChatCommandDraft: value => { reset = value; }, normalizeChatCommandDraft,
  setStatus: () => {}, t: key => key, navigate: value => { navigated = value; },
})({ id: 'synthetic', name: 'synthetic', postprocess_block_types: ['vote'] });
assert.equal(deleted, 'synthetic'); assert.deepEqual(reset.postprocess_block_types, []); assert.equal(reset.id, undefined); assert.equal(navigated, '/chat-commands');
// Output-type normalization must not reinterpret authored prompt fields.
const authored = { name: 'synthetic', prompt: 'Synthetic instruction', generation_prompt: '', postprocess_prompt: '' };
assert.equal(normalizeChatCommandDraft(authored).generation_prompt, '');
assert.equal(normalizeChatCommandDraft(authored).postprocess_prompt, '');
const first = normalizeChatCommandDraft(); first.postprocess_block_types.push('text');
assert.deepEqual(normalizeChatCommandDraft().postprocess_block_types, []);
console.log('Command-output model + actual App create/update/delete handlers passed (legacy, empty, subset, unknown values; fresh resets)');

// Execute the production hook with React's dependency/cleanup semantics and
// deferred requests. Each effect invocation owns its own completion, not an ID.
const hookSource = readFileSync(new URL('../src/hooks/useRouteResourceSync.ts', import.meta.url), 'utf8');
const hookAst = ts.createSourceFile('hook.ts', hookSource, ts.ScriptTarget.Latest, true);
const hookText = hookAst.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'useRouteResourceSync').getText(hookAst).replace(/^export /, '');
function harness() {
  let cleanup, previous, draft, status = '';
  const pending = [];
  const t = key => key;
  const hook = new Function('useEffect', 'useI18n', 'settingsApi', 'normalizeChatCommandDraft', `${compile(hookText)}; return useRouteResourceSync;`)(
    (effect, deps) => {
      if (previous && deps.every((value, index) => Object.is(value, previous[index]))) return;
      cleanup?.(); previous = deps; cleanup = effect();
    }, () => ({ t }), { chatCommands: () => new Promise((resolve, reject) => pending.push({ resolve, reject })) }, normalizeChatCommandDraft);
  return {
    pending, get draft() { return draft; }, get status() { return status; },
    edit(value) { draft = value; },
    visit(id, cached = []) { hook({ route: id ? { page: 'chatCommandDetail', id } : { page: 'chatCommandNew' }, characters: [], presets: [], chatCommands: cached, setChatCommandDraft: value => { draft = value; }, setStatus: value => { status = value; } }); },
    unmount() { cleanup?.(); },
  };
}
const settle = () => new Promise(resolve => setImmediate(resolve));
const oldA = { id: 'A', name: 'A', postprocess_prompt: 'old prompt', postprocess_block_types: ['table'] };
const authoredNew = { name: 'unsaved-new', postprocess_prompt: 'new prompt', postprocess_block_types: ['vote'] };
let hookPassed = 0;
for (const result of ['found', 'missing', 'reject']) {
  const h = harness(); h.visit('A'); h.visit(); h.edit({ ...authoredNew });
  if (result === 'reject') h.pending[0].reject(new Error('stale error'));
  else h.pending[0].resolve(result === 'found' ? [oldA] : []);
  await settle(); assert.deepEqual(h.draft, authoredNew); assert.equal(h.status, '');
  let submitted;
  await handler('saveChatCommand', {
    chatCommandDraft: h.draft, chatCommandSavePayload, normalizeChatCommandDraft,
    setStatus: () => {}, t: key => key, setBusy: () => {},
    settingsMutations: { saveChatCommand: { mutateAsync: async input => { submitted = input; return { ...input.payload, id: 'new' }; } } },
    loadChatCommands: async () => {}, setChatCommandDraft: () => {}, navigate: () => {},
  })();
  assert.equal(submitted.id, null); assert.deepEqual(submitted.payload.postprocess_block_types, ['vote']);
  console.log(`ok hook ${++hookPassed} - A to authored New ignores stale ${result}; actual App save creates`);
}
{
  const h = harness(), b = { id: 'B', name: 'B', postprocess_block_types: [] };
  h.visit('A'); h.visit('B', [b]); h.pending[0].resolve([oldA]); await settle();
  assert.equal(h.draft.id, 'B'); assert.deepEqual(h.draft.postprocess_block_types, []);
  console.log(`ok hook ${++hookPassed} - pending A cannot overwrite cached B`);
}
{
  const h = harness(); h.visit('A'); h.visit('B'); h.visit('A');
  h.pending[0].resolve([oldA]); h.pending[1].reject(new Error('stale B')); await settle();
  assert.equal(h.draft, undefined); assert.equal(h.status, '');
  h.pending[2].resolve([{ ...oldA, name: 'latest A', postprocess_block_types: ['future_block', 'vote'] }]); await settle();
  assert.equal(h.draft.name, 'latest A'); assert.deepEqual(h.draft.postprocess_block_types, ['future_block', 'vote']);
  console.log(`ok hook ${++hookPassed} - A/B/A only newest visit owns completion`);
}
for (const result of ['missing', 'reject']) {
  const h = harness(); h.visit('A');
  if (result === 'reject') h.pending[0].reject(new Error('current error')); else h.pending[0].resolve([]);
  await settle(); assert.equal(h.status, result === 'reject' ? 'current error' : '커맨드를 찾을 수 없습니다.');
  console.log(`ok hook ${++hookPassed} - active ${result} still reports status`);
}
{
  const h = harness(); h.visit('A'); h.unmount(); h.pending[0].resolve([oldA]); await settle();
  assert.equal(h.draft, undefined); assert.equal(h.status, '');
  console.log(`ok hook ${++hookPassed} - unmounted visit ignores completion`);
}
console.log(`${hookPassed} deferred production-hook regressions passed`);
