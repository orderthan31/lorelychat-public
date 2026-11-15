import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const ast = path => ts.createSourceFile(path, read(path), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const compile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React } }).outputText;
const evaluate = (source, env) => new Function('env', `with(env){${compile(`const result = ${source};`)}return result;}`)(env);
function load(path, names, env = {}) {
  const source = ast(path).statements.filter(n => !ts.isImportDeclaration(n) && !ts.isExportDeclaration(n)).map(n => n.getText()).join('\n').replace(/\bexport /g, '').replace(/import\.meta\.env/g, '{}');
  return new Function('env', `with(env){${compile(source).replace(/import\.meta\.env/g, '{}').replace(/export \{\};/g, '')}return {${names.join(',')}};}`)(env);
}
function nodes(node, predicate) {
  if (Array.isArray(node)) return node.flatMap(child => nodes(child, predicate));
  if (!React.isValidElement(node)) return [];
  return [...(predicate(node) ? [node] : []), ...nodes(node.props.children, predicate)];
}
function findAst(root, predicate) {
  const found = [];
  function visit(node) { if (predicate(node)) found.push(node); ts.forEachChild(node, visit); }
  visit(root); return found;
}
const constants = load('../src/constants/domain.ts', ['CAST_ROLE_PRESETS', 'USER_SETTING_PRESETS', 'DEFAULT_RUNTIME_SETTING', 'FALLBACK_RESPONSE_LENGTH_PRESETS']);
const { runtimeModelDisplayName } = load('../src/utils/modelDisplay.ts', ['runtimeModelDisplayName']);
const Field = ({ label, children }) => React.createElement('label', null, label, children);
const Select = props => React.createElement('select', props);
const plain = tag => ({ children }) => React.createElement(tag, null, children);
const EditorHelp = ({ summary, children }) => React.createElement('details', null, React.createElement('summary', null, summary), children);
const env = {
  React, ...constants, runtimeModelDisplayName, Field, Select, EditorHelp,
  useI18n: () => ({ t: key => key, locale: 'ko' }), t: key => key,
  Input: plain('input'), Textarea: plain('textarea'), Switch: plain('span'), Button: plain('button'), Badge: plain('span'),
  FormSection: plain('section'), EditorActionBar: plain('div'), Plus: () => null, Trash2: () => null,
  formatCharacterSlot: n => `캐릭터 ${n}`, formatSelectedCharacters: n => `${n}`,
  cn: (...args) => args.filter(Boolean).join(' '),
  normalizeCompressionStrategy: value => value || 'quality', hasActiveDecisionModel: () => true,
};
const { RuntimeSettingFields } = load('../src/components/organisms/RuntimeSettings.tsx', ['RuntimeSettingFields'], env);
const { ConversationCreateView } = load('../src/views/ConversationCreate/index.tsx', ['ConversationCreateView'], env);
const { ConversationEditView } = load('../src/views/ConversationEdit/index.tsx', ['ConversationEditView'], env);
const fieldSelect = (tree, label) => nodes(tree, n => n.type === Field && n.props.label === label)[0].props.children;
const optionTexts = tree => nodes(tree, n => n.type === 'option').map(n => renderToStaticMarkup(n).replace(/<[^>]*>/g, ''));
const makeOption = (key, name, alias = 'Provider [A]') => ({ key, label: `${alias} · ${name}`, model: `vendor/${key}`, provider_account_alias: alias, provider_account_id: alias, provider: 'openai' });
let passed = 0;
function test(name, fn) { fn(); console.log(`ok ${++passed} - ${name}`); }

test('runtime display removes one exact API wrapper and preserves authored labels and IDs', () => {
  for (const name of ['Model One', 'Provider [A] · authored name', 'openai/gpt-id', 'A · B', '이름 日本語 <tag>']) {
    const option = Object.freeze(makeOption('key', name));
    assert.equal(runtimeModelDisplayName(option), name);
    assert.equal(option.label, `Provider [A] · ${name}`);
  }
  for (const label of ['Other · Model', 'Provider [A]: Model', 'Provider [A] · ', ' Provider [A] · Model']) {
    assert.equal(runtimeModelDisplayName({ label, provider_account_alias: 'Provider [A]' }), label);
  }
  assert.equal(runtimeModelDisplayName({ label: 'Provider · name' }), 'Provider · name');
  assert.equal(runtimeModelDisplayName({ model: 'openai/gpt-id', provider_account_alias: 'openai' }), 'openai/gpt-id');
  assert.equal(runtimeModelDisplayName({ key: 'Provider · opaque-key', provider_account_alias: 'Provider' }), 'Provider · opaque-key');
});

test('global and room chat/fallback/compression/decision selectors use model names, stable values and provider choices', () => {
  const chat = makeOption('chat', 'Chat'); const fallback = makeOption('fallback', 'Fallback');
  const compression = makeOption('compression', 'Summary'); const decision = makeOption('decision', 'Decision');
  const value = { model_key: chat.key, fallback_model_key: fallback.key, compression_model_key: compression.key, decision_model_option_key: decision.key, options: [chat, fallback], compression_options: [compression, fallback], decision_options: [decision] };
  const original = JSON.stringify(value);
  for (const scope of ['global', 'room']) {
    let changed;
    const tree = RuntimeSettingFields({ value, scope, onChange: next => { changed = next; } });
    for (const [label, expected, key] of [['채팅 모델', 'Chat', 'chat'], ['장애 시 보조 모델', 'Fallback', 'fallback'], ['압축 모델', 'Summary', 'compression'], ['의사결정 모델', 'Decision', 'decision']]) {
      const select = fieldSelect(tree, label);
      assert.equal(select.props.value, key);
      assert.ok(optionTexts(select).includes(expected));
      assert.ok(optionTexts(select).every(text => !text.includes('Provider [A]')));
    }
    assert.ok(optionTexts(fieldSelect(tree, '채팅 Provider')).includes('Provider [A]'));
    assert.deepEqual(optionTexts(fieldSelect(tree, '압축 폴백 모델')), ['채팅 폴백 상속 · Fallback', 'Provider [A] · Fallback']);
    fieldSelect(tree, '채팅 모델').props.onChange({ target: { value: 'fallback' } });
    assert.equal(changed.model_key, 'fallback'); assert.equal(changed.fallback_model_key, null);
    assert.equal(Object.hasOwn(changed, 'thinking_level'), false);
    fieldSelect(tree, '압축 폴백 모델').props.onChange({ target: { value: '' } });
    assert.equal(changed.compression_fallback_model_key, null);
  }
  assert.equal(JSON.stringify(value), original);
});

test('unavailable fallback IDs, null inheritance and empty options survive rendering', () => {
  const value = { fallback_model_key: 'missing/provider/id', compression_fallback_model_key: 'missing/summary', chat_fallback_status: 'unavailable', compression_fallback_status: 'unavailable', thinking_level: null };
  let changed;
  const tree = RuntimeSettingFields({ value, scope: 'room', onChange: next => { changed = next; } });
  assert.ok(optionTexts(fieldSelect(tree, '장애 시 보조 모델')).includes('missing/provider/id · unavailable'));
  assert.ok(optionTexts(fieldSelect(tree, '압축 폴백 모델')).includes('missing/summary · unavailable'));
  assert.equal(fieldSelect(tree, '채팅 모델').props.disabled, true);
  assert.equal(nodes(tree, n => n.props['data-fallback-unavailable']).length, 2);
  fieldSelect(tree, '대화 길이').props.onChange({ target: { value: 'long' } });
  assert.equal(changed.thinking_level, null); assert.equal(changed.fallback_model_key, value.fallback_model_key);
});

const createProps = { scene: {}, multiCharacterIds: ['character-1'], multiCharacterRoles: [''], selectOptions: React.createElement('option', { value: 'character-1' }, 'Authored name') };
function assertRoles(tree) {
  const options = nodes(tree, n => n.type === 'option' && constants.CAST_ROLE_PRESETS.some(p => p.key === n.props.value));
  for (const preset of constants.CAST_ROLE_PRESETS) {
    assert.ok(options.some(n => n.props.value === preset.key && n.props.children === preset.label), preset.key || 'none');
  }
  for (const option of options) assert.ok(!renderToStaticMarkup(option).includes('캐릭터 기본 말맛'));
  const helps = nodes(tree, n => n.type === EditorHelp);
  assert.equal(helps.length, 1);
  const markup = renderToStaticMarkup(helps[0]);
  for (const preset of constants.CAST_ROLE_PRESETS) assert.ok(markup.includes(preset.hint));
  assert.ok(markup.startsWith('<details>')); assert.ok(!markup.includes('<details open'));
}

test('create and edit role selects use short labels with full definitions in disclosure', () => {
  assertRoles(ConversationCreateView(createProps));
  assertRoles(ConversationEditView({ roomEditDraft: { participants: [{ id: 'character-1', type: 'character', role: null }] }, characters: [] }));
});

test('room-info production management panel keeps short options and exact role callback', () => {
  const source = ast('../src/components/organisms/ConversationInfoDrawer.tsx');
  const declaration = findAst(source, n => ts.isVariableDeclaration(n) && n.name.getText(source) === 'managementPanel')[0];
  let changed;
  const tree = evaluate(declaration.initializer.getText(source), { ...env, titleDraft: 'Room', submitTitle: () => {}, setTitleDraft: () => {}, characterParticipants: [{ id: 'character-1', role: '' }], characterById: new Map(), participantName: () => 'Authored name', avatarUrlFor: () => '', theme: 'light', WebpImage: () => null, busy: false, onRemoveCharacter: () => {}, onUpdateCharacterRole: (...args) => { changed = args; }, submitInvite: () => {}, inviteCharacterId: '', setInviteCharacterId: () => {}, inviteOptions: [] });
  assertRoles(tree);
  const select = nodes(tree, n => n.type === Select && n.props['aria-label'] === 'Authored name · 캐릭터 역할')[0];
  select.props.onChange({ target: { value: 'silent' } }); assert.deepEqual(changed, ['character-1', 'silent']);
});

test('edit role changes only selected character draft; clearing and unknown authored role are preserved', () => {
  const draft = { title: 'Authored room', world_setting_id: null, custom: 'untouched', participants: [{ id: 'user', type: 'user', role: 'user' }, { id: 'a', type: 'character', role: 'custom authored role', order_index: 4 }, { id: 'b', type: 'character', role: 'rival' }] };
  const original = JSON.stringify(draft); let changed;
  const tree = ConversationEditView({ roomEditDraft: draft, setRoomEditDraft: value => { changed = value; }, characters: [] });
  const selects = nodes(tree, n => n.type === Field && n.props.label === '캐릭터 역할').map(n => n.props.children);
  assert.ok(optionTexts(selects[0]).includes('custom authored role'));
  selects[1].props.onChange({ target: { value: 'silent' } });
  assert.equal(changed.participants[2].role, 'silent');
  assert.equal(changed.participants[0], draft.participants[0]); assert.equal(changed.participants[1], draft.participants[1]);
  assert.equal(changed.title, draft.title); assert.equal(changed.world_setting_id, null);
  selects[0].props.onChange({ target: { value: '' } }); assert.equal(changed.participants[1].role, null);
  assert.equal(changed.participants[1].order_index, 4); assert.equal(JSON.stringify(draft), original);
});

test('catalog renders raw authored name and quiet provider metadata; internal model ID remains in closed details', () => {
  const source = ast('../src/views/ModelSettings/index.tsx');
  const map = findAst(source, n => ts.isCallExpression(n) && n.expression.getText(source) === 'visibleModelOptions.map')[0];
  const row = map.arguments[0].body.getText(source);
  const option = Object.freeze({ id: 'id', key: 'stable-key', label: 'Provider [A] · authored title', display_label: 'Provider [A] · Provider [A] · authored title', provider_account_alias: 'Provider [A]', provider_type: 'openai', model: 'internal/vendor/model-id', source: 'fetched', supports_chat: true, enabled: true });
  const tree = evaluate(row, { ...env, option, loading: false, toggleModelOption: () => {} });
  assert.equal(nodes(tree, n => n.type === 'h3')[0].props.children, option.label);
  const paragraphs = nodes(tree, n => n.type === 'p');
  assert.equal(paragraphs[0].props.children, option.provider_account_alias);
  assert.match(paragraphs[0].props.className, /text-\[11px\].*text-muted-foreground/);
  assert.ok(paragraphs.every(n => n.props.children !== option.model));
  const details = nodes(tree, n => n.type === 'details')[0];
  assert.equal(details.props.open, undefined);
  assert.equal(nodes(details, n => n.type === 'code')[0].props.children, option.model);
  // Search still indexes internal IDs and the existing manual editor still owns ID/label inputs.
  assert.match(read('../src/views/ModelSettings/index.tsx'), /option\.model,\s*option\.provider_type/);
  assert.match(read('../src/views/ModelSettings/index.tsx'), /model: manualModel\.trim\(\)/);
  assert.match(read('../src/views/ModelSettings/index.tsx'), /label: manualLabel\.trim\(\) \|\| manualModel\.trim\(\)/);
});
console.log(`Passed ${passed} compact-label regression tests (synthetic only; no browser, API or provider calls).`);
