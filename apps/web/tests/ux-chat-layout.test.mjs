import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const read = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
function harness() {
  const slots = []; let cursor = 0; let effects = [];
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const hookEffect = (fn, deps) => {
    const i = cursor++; const old = slots[i];
    if (!old || !same(old.deps, deps)) effects.push(() => { old?.cleanup?.(); slots[i] = { deps, cleanup: fn() }; });
  };
  const react = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial; return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }]; },
    useRef(initial) { const i = cursor++; return slots[i] ||= { current: initial }; },
    useCallback(fn, deps) { const i = cursor++; if (!slots[i] || !same(slots[i].deps, deps)) slots[i] = { deps, fn }; return slots[i].fn; },
    useEffect: hookEffect, useLayoutEffect: hookEffect,
  };
  return { react, render(fn, props) { cursor = 0; effects = []; const result = fn(props); effects.forEach(effect => effect()); return result; }, cleanup() { slots.forEach(slot => slot?.cleanup?.()); } };
}
function load(path, react, extras = {}) {
  const source = read(path);
  const code = ts.transpileModule(source, { reportDiagnostics: true, compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } });
  assert.equal(code.diagnostics?.length || 0, 0, path);
  const exports = {};
  const jsx = (type, props, key) => ({ type, props: props || {}, key });
  const require = name => name === 'react' ? react : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx, Fragment: 'Fragment' } : extras[name] || {};
  new Function('require', 'exports', code.outputText)(require, exports);
  return exports;
}
const listeners = new Map(); const observers = [];
globalThis.window = { addEventListener: (type, fn) => listeners.set(type, fn), removeEventListener: (type, fn) => { if (listeners.get(type) === fn) listeners.delete(type); } };
globalThis.ResizeObserver = class { constructor(fn) { this.fn = fn; observers.push(this); } observe(node) { this.node = node; } disconnect() { this.disconnected = true; } };
const h = harness();
const { useConversationPanelLayout, conversationPanelGeometry, conversationPanelIsOpen } = load('hooks/useConversationPanelLayout.ts', h.react);
for (const width of [0, 320, 390, 768, 820, 959]) assert.equal(conversationPanelGeometry(width).docked, false);
for (const [width, panelWidth] of [[960, 320], [1024, 320], [1180, 320], [1280, 320], [1440, 480], [1920, 960], [2560, 1600]]) {
  assert.deepEqual(conversationPanelGeometry(width), { docked: true, panelWidth });
  assert.ok(width - panelWidth >= 640);
  assert.ok(width - panelWidth <= 960);
}
for (const width of [NaN, Infinity, -1]) assert.equal(conversationPanelGeometry(width).docked, false);
assert.equal(conversationPanelIsOpen(false), false);
let width = 1024;
const element = { getBoundingClientRect: () => ({ width }) };
const render = room => h.render(useConversationPanelLayout, room);
let layout = render('A'); layout.workspaceRef(element); layout = render('A'); layout = render('A');
assert.equal(layout.open, true); assert.equal(layout.docked, true);
layout.setOpen(false); layout = render('A'); assert.equal(layout.open, false);
// Same-room refresh and repeated observer notifications cannot undo manual collapse.
for (let i = 0; i < 3; i++) { observers[0].fn(); layout = render('A'); assert.equal(layout.open, false); }
assert.equal(render('B').open, true); assert.equal(render('A').open, false);
width = 390; observers[0].fn(); layout = render('A'); assert.equal(layout.docked, false); assert.equal(layout.open, false);
layout.setOpen(true); assert.equal(render('A').open, true);
width = 1440; observers[0].fn(); layout = render('A'); assert.equal(layout.open, false);
width = 390; observers[0].fn(); assert.equal(render('A').open, false);
width = 1440; observers[0].fn(); layout = render('A'); layout.setOpen(true); assert.equal(render('A').open, true);
assert.equal(render('').open, false);
h.cleanup(); assert.equal(observers[0].disconnected, true); assert.equal(listeners.size, 0);
console.log('PASS geometry, actual content measurement, collapse/refresh, room identity, narrow transitions, cleanup');

function walk(node) {
  if (!node || typeof node !== 'object') return [];
  return [node, ...[node.props?.children].flat(Infinity).flatMap(walk)];
}
const ch = harness(); let gearCalls = 0; let legacyCalls = 0; let resizes = 0; let textareaWidth = 500;
const resizeFrames = new Map(); let resizeFrameId = 0;
globalThis.requestAnimationFrame = callback => { resizeFrames.set(++resizeFrameId, callback); return resizeFrameId; };
globalThis.cancelAnimationFrame = id => resizeFrames.delete(id);
const textarea = { getBoundingClientRect: () => ({ width: textareaWidth }) };
const atoms = Object.fromEntries(['Button', 'Card', 'Select', 'Textarea'].map(name => [name, name]));
const i18n = { useI18n: () => ({ t: value => value, locale: 'ko' }) };
const { ConversationComposer } = load('views/ConversationDetail/ConversationComposer.tsx', ch.react, {
  '../../components/organisms': { ChatSettingsBubble: 'ChatSettingsBubble' }, '../../components/atoms': atoms, '../../i18n/I18nProvider': i18n,
});
const props = { compose: 'unchanged\nmultiline draft', composerTextareaRef: { current: textarea }, resizeComposerTextarea: () => { resizes++; }, speakerOptions: [], onOpenPanelSettings: () => { gearCalls++; }, setChatSettingsOpen: () => { legacyCalls++; } };
let tree = ch.render(ConversationComposer, props);
let nodes = walk(tree); nodes.find(node => node.props.className === 'chat-settings-button').props.onClick();
assert.equal(gearCalls, 1); assert.equal(legacyCalls, 0); assert.equal(nodes.filter(node => node.type === 'ChatSettingsBubble').length, 0);
assert.equal(nodes.find(node => node.type === 'Textarea').props.value, props.compose);
const composerObserver = observers.at(-1); composerObserver.fn(); assert.equal(resizes, 1);
textareaWidth = 280; composerObserver.fn(); assert.equal(resizes, 1, 'no observed DOM height write inside ResizeObserver delivery');
for (const [id, callback] of resizeFrames) { resizeFrames.delete(id); callback(); }
assert.equal(resizes, 2);
tree = ch.render(ConversationComposer, { ...props, onOpenPanelSettings: undefined }); nodes = walk(tree);
assert.equal(nodes.filter(node => node.type === 'ChatSettingsBubble').length, 1);
nodes.find(node => node.props.className === 'chat-settings-button').props.onClick(); assert.equal(legacyCalls, 1);
textareaWidth = 300; composerObserver.fn(); assert.equal(resizeFrames.size, 1);
ch.cleanup(); assert.equal(composerObserver.disconnected, true); assert.equal(resizeFrames.size, 0, 'unmount cancels deferred textarea write');
console.log('PASS gear single settings owner, legacy mobile fallback, unchanged draft and width-only resizing');

const dh = harness();
const { ConversationInfoDrawer } = load('components/organisms/ConversationInfoDrawer.tsx', dh.react, {
  '../atoms': atoms, '../atoms/WebpImage': { WebpImage: 'WebpImage' }, '../../i18n/I18nProvider': i18n,
  '../../stores/uiStore': { useUiStore: fn => fn({ theme: 'light' }) },
  '../../constants/domain': { CAST_ROLE_PRESETS: [], RELATIONSHIP_ARCHETYPE_PRESETS: [] },
  '../../i18n/core': { formatStoryArcLimit: String },
});
const drawerProps = { open: true, docked: true, participants: [], characters: [], participantName: String, conversation: { id: 'A', title: 'A' }, activeTab: 'settings', settingsContent: { type: 'SettingsSentinel', props: {} } };
nodes = walk(dh.render(ConversationInfoDrawer, drawerProps));
assert.equal(nodes.filter(n => n.type === 'aside').length, 1);
assert.equal(nodes.find(n => n.type === 'aside').props.role, undefined);
assert.equal(nodes.filter(n => n.props.className === 'context-drawer-backdrop').length, 0);
assert.equal(nodes.filter(n => n.type === 'SettingsSentinel').length, 1);
nodes = walk(dh.render(ConversationInfoDrawer, { ...drawerProps, docked: false }));
assert.equal(nodes.find(n => n.type === 'aside').props.role, 'dialog');
assert.equal(nodes.find(n => n.type === 'aside').props['aria-modal'], true);
assert.equal(nodes.filter(n => n.props.className === 'context-drawer-backdrop').length, 1);
assert.equal(nodes.filter(n => n.type === 'SettingsSentinel').length, 1);
nodes = walk(dh.render(ConversationInfoDrawer, { ...drawerProps, activeTab: 'battle' }));
assert.ok(nodes.some(n => n.props['aria-label'] === '대화방 관리'));
assert.equal(dh.render(ConversationInfoDrawer, { ...drawerProps, open: false }), null);
console.log('PASS one panel, dock/nonmodal and narrow/dialog semantics, optional settings and conditional battle');

// Execute the actual modal effect against a synthetic DOM: no browser or service.
const drawerAst = ts.createSourceFile('drawer.tsx', read('components/organisms/ConversationInfoDrawer.tsx'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let modalCallback;
function visit(node) {
  if (ts.isCallExpression(node) && node.expression.getText(drawerAst) === 'useLayoutEffect') modalCallback = node.arguments[0].getText(drawerAst);
  ts.forEachChild(node, visit);
}
visit(drawerAst); assert.ok(modalCallback);
const modalJs = ts.transpileModule(`const effect = ${modalCallback};`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const events = new Map(); let modalCloses = 0;
class Element {
  constructor(name) { this.name = name; this.children = []; this.inert = false; this.tabIndex = 0; this.isConnected = true; this.classList = { contains: value => value === name }; }
  add(...nodes) { nodes.forEach(node => { node.parentElement = this; this.children.push(node); }); }
  focus() { document.activeElement = this; }
  getClientRects() { return [{}]; }
  matches() { return false; }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  querySelectorAll() { return this.children; }
}
const body = new Element('body'); body.style = { overflow: 'auto' };
const root = new Element('root'); const workspace = new Element('workspace'); const background = new Element('chat');
const backdrop = new Element('context-drawer-backdrop'); const panel = new Element('panel'); const first = new Element('first'); const last = new Element('last'); const trigger = new Element('trigger');
body.add(root); root.add(workspace); workspace.add(background, backdrop, panel); background.add(trigger); panel.add(first, last);
globalThis.HTMLElement = Element;
globalThis.document = { body, activeElement: trigger, querySelector: () => null, addEventListener: (key, fn) => events.set(key, fn), removeEventListener: key => events.delete(key) };
const runModal = new Function('env', `with (env) { ${modalJs}; return effect(); }`);
const cleanupModal = runModal({ panelRef: { current: panel }, open: true, docked: false, closeRef: { current: () => { modalCloses++; } } });
assert.equal(background.inert, true); assert.equal(backdrop.inert, false); assert.equal(document.activeElement, first); assert.equal(body.style.overflow, 'hidden');
const key = (name, shiftKey = false) => { const event = { key: name, shiftKey, preventDefault() { this.prevented = true; }, stopImmediatePropagation() { this.stopped = true; } }; events.get('keydown')(event); return event; };
last.focus(); assert.equal(key('Tab').prevented, true); assert.equal(document.activeElement, first);
assert.equal(key('Tab', true).prevented, true); assert.equal(document.activeElement, last);
const escape = key('Escape'); assert.equal(modalCloses, 1); assert.equal(escape.stopped, true);
cleanupModal(); assert.equal(background.inert, false); assert.equal(body.style.overflow, 'auto'); assert.equal(document.activeElement, trigger); assert.equal(events.size, 0);
assert.equal(runModal({ panelRef: { current: panel }, open: true, docked: true }), undefined);
console.log('PASS modal focus entry, Tab/Shift+Tab containment, Escape, inert background and focus/scroll restoration');
