import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

// Execute the production leaf effects with deferred authenticated-byte requests.
// No DOM layout snapshots, provider requests, or production store mutation.
const jsx = (type, props) => ({ type, props });
const labels = { 'agent.imageLoading': 'Loading image', 'agent.imageFailed': 'Image unavailable', 'agent.generatedImage': 'Generated image' };
const i18n = { useI18n: () => ({ t: key => labels[key] || key }) };
function load(name, imports) {
  const source = readFileSync(new URL(`../src/agent/${name}.tsx`, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  new Function('require', 'exports', code)(key => {
    if (key === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'fragment' };
    if (key === '../i18n/I18nProvider') return i18n;
    assert.ok(key in imports, `unexpected leaf dependency ${key}`); return imports[key];
  }, exports);
  return exports;
}
const { AgentError } = load('AgentError', {});
assert.equal(AgentError({ value: '' }), null);
assert.equal(AgentError({ value: 'agent.imageFailed' }).props.children, 'Image unavailable');
assert.equal(AgentError({ value: 'Authored diagnostic' }).props.children, 'Authored diagnostic');
assert.equal(AgentError({ value: 'agent.imageFailed' }).props.role, 'alert');
const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL;
const created = [], revoked = [], pending = [];
URL.createObjectURL = blob => { created.push(blob); return `blob:synthetic-${created.length}`; };
URL.revokeObjectURL = value => revoked.push(value);
let values = [], cursor = 0, previousId, cleanup, scheduled;
const react = {
  useState(initial) { const index = cursor++; if (!(index in values)) values[index] = initial; return [values[index], value => { values[index] = value; }]; },
  useEffect(effect, [id]) { if (id !== previousId) { cleanup?.(); previousId = id; scheduled = effect; } },
};
const { AgentImage } = load('AgentImage', { react, './AgentError': { AgentError }, './api': { agentApi: { imageBlob: id => new Promise((resolve, reject) => pending.push({ id, resolve, reject })) } } });
const render = id => { cursor = 0; const tree = AgentImage({ id }); if (scheduled) { const effect = scheduled; scheduled = undefined; cleanup = effect(); return render(id); } return tree.props.children; };
const settle = () => new Promise(resolve => setImmediate(resolve));
try {
  let children = render('one'); assert.equal(children[1].props.children, 'Loading image');
  assert.equal(pending[0].id, 'one'); pending[0].resolve(new Blob(['synthetic'])); await settle();
  children = render('one'); assert.equal(children[2].props.src, 'blob:synthetic-1'); assert.equal(children[2].props.alt, 'Generated image');
  render('two'); assert.deepEqual(revoked, ['blob:synthetic-1']);
  pending[1].reject(new Error('private HTTP failure')); await settle();
  children = render('two'); assert.equal(children[0].props.value, 'agent.imageFailed'); assert.ok(!children[2]);
  render('late'); cleanup(); pending[2].resolve(new Blob(['late'])); await settle();
  assert.equal(created.length, 1, 'unmounted late result must not create an object URL');
  render('three'); pending[3].resolve(new Blob(['third'])); await settle();
  children = render('three'); children[2].props.onError(); children = render('three');
  assert.equal(children[0].props.value, 'agent.imageFailed'); assert.ok(!children[2]);
  cleanup(); assert.deepEqual(revoked, ['blob:synthetic-1', 'blob:synthetic-2']);
  console.log('PASS Agent leaf translation, loading/error, byte-only preview, ID-change/unmount URL cleanup and late-result fence');
} finally { URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke; }
