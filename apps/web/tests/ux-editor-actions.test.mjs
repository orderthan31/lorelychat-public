import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const read = path => readFileSync(`src/${path}`, 'utf8');
const forms = [
  'components/organisms/CharacterForm.tsx', 'components/organisms/PresetForm.tsx',
  'components/organisms/RuntimeSettings.tsx', 'views/WorldSettings/index.tsx',
  'views/ChatCommands/index.tsx', 'views/ConversationCreate/index.tsx', 'views/ConversationEdit/index.tsx',
];
for (const path of forms) {
  const source = read(path);
  if (path === 'views/ConversationCreate/index.tsx') {
    assert.doesNotMatch(source, /<EditorActionBar[\s>]/, 'Start is owned by AppShell, not a competing Add toolbar');
    assert.match(source, /conversation-participants[\s\S]*action=\{<Button/, 'Add remains beside its participant section');
  } else assert.match(source, /<EditorActionBar[\s>]/, `${path}: actions use the shared persistent owner`);
  const parsed = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  assert.equal(parsed.parseDiagnostics.length, 0, `${path}: valid TSX`);
}
const character = read(forms[0]);
assert.equal((character.match(/type="submit"/g) || []).length, 1, 'one character submit');
assert.match(character, /\['images', t\('이미지'\)\]/, 'all five character sections retained');
assert.equal((read(forms[1]).match(/type="submit"/g) || []).length, 1, 'one preset submit');
const runtime = read(forms[2]);
assert.match(runtime, /embedded\?: boolean/);
assert.match(runtime, /if \(!open\) return null/);
assert.match(runtime, /embedded \? 'chat-settings-embedded' : 'chat-settings-popover'/);
assert.match(runtime, /scope="room" inheritedThinkingLevel=\{inheritedThinkingLevel\}/);
assert.match(read(forms[4]), /context_sources: value/, 'context only updated by its own control');
const actions = read('components/molecules/EditorActionBar.tsx');
assert.match(actions, /ResizeObserver/, 'measure actual shell header rather than guessing its height');
const styles = read('components/molecules/EditorActionBar.scss');
assert.match(styles, /position: sticky/);
assert.match(styles, /min-height: 44px/);
assert.doesNotMatch(styles, /position: fixed/, 'no keyboard-obscuring fixed footer');
assert.match(read('components/molecules/EditorHelp.tsx'), /<details/);
assert.match(read('components/molecules/EditorHelp.tsx'), /<summary>/);
console.log('editor action ownership, TSX, embedding and disclosure contracts passed');
