import assert from 'node:assert/strict';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';
import { chromium } from '@playwright/test';

const plugin = { name: 'command-output-fixture', configureServer(server) {
  server.middlewares.use('/command-test', async (_req, res) => {
    res.setHeader('Content-Type', 'text/html');
    res.end(await server.transformIndexHtml('/command-test', '<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/command-output.fixture.tsx"></script></body></html>'));
  });
} };
const server = await createServer({ configFile: false, envFile: false, plugins: [plugin, react(), tailwind()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
let browser;
let passed = 0;
try {
  await server.listen();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 320, height: 640 } });
  page.setDefaultTimeout(5000);
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  const command = (id, extra = {}) => ({ id, name: id, postprocess_prompt: 'Synthetic instruction', ...extra });
  const records = [command('remote', { postprocess_block_types: ['checklist'] }), command('legacy'), command('subset', { postprocess_block_types: ['note', 'table'] }), command('empty', { postprocess_block_types: [] }), command('future', { postprocess_block_types: ['future_block', 'text'] })];
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  let holdGets = false;
  const heldGets = [];
  const delayOpen = async id => {
    const count = heldGets.length;
    holdGets = true;
    await page.evaluate(id => window.openCommand(id), id);
    const deadline = Date.now() + 5000;
    while (heldGets.length === count && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 5));
    assert.equal(heldGets.length, count + 1, 'uncached visit issued a held GET');
  };
  const release = async (index, json, status = 200) => {
    const before = await page.evaluate(() => window.settledGets);
    await heldGets[index].fulfill({ status, json });
    await page.waitForFunction(before => window.settledGets > before, before);
  };
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname.startsWith('/api/')) {
      if (!/^\/api\/chat-commands(?:\/[^/]+)?$/.test(url.pathname)) return route.abort();
      const method = request.method();
      if (method === 'GET') {
        if (holdGets) { heldGets.push(route); return; }
        return route.fulfill({ json: records });
      }
      const body = method === 'DELETE' ? null : request.postDataJSON();
      requests.push({ method, path: url.pathname, body });
      return route.fulfill({ json: method === 'DELETE' ? {} : { ...body, id: body.id || 'created' } });
    }
    return route.continue();
  });
  await page.addInitScript(records => { window.commands = records.filter(item => item.id !== 'remote'); localStorage.setItem('lorechat-language', 'en'); }, records);
  await page.addInitScript(() => {
    window.settledGets = 0;
    const open = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, ...args) {
      if (method.toUpperCase() === 'GET') {
        // A task after loadend lets Axios and the hook's promise chain settle.
        this.addEventListener('loadend', () => setTimeout(() => { window.settledGets++; }, 0), { once: true });
      }
      return open.call(this, method, ...args);
    };
  });
  await page.goto(`${origin}/command-test`);
  const group = () => page.getByRole('group', { name: 'Postprocess output types', exact: true });
  const selected = () => group().getByRole('checkbox').evaluateAll(nodes => nodes.filter(n => n.checked).map(n => n.value));
  const open = async id => { await page.evaluate(id => window.openCommand(id), id); await page.waitForFunction(id => window.currentDraft.id === id, id); };
  const save = async () => { const before = requests.length; await page.evaluate(() => { window.saved = null; }); await page.getByRole('button', { name: 'Save', exact: true }).click(); await page.waitForFunction(() => window.saved); assert.equal(requests.length, before + 1); return requests.at(-1); };
  const check = async (name, fn) => { await fn(); console.log(`ok ${++passed} - ${name}`); };
  await check('legacy missing becomes empty; nine accessible labeled checkboxes; PATCH includes []', async () => {
    await group().waitFor();
    assert.equal(await group().getByRole('checkbox').count(), 9);
    for (const name of ['Text', 'Note', 'Table', 'Comments', 'Commentary', 'Checklist', 'Timeline', 'Vote', 'Cards']) assert.equal(await group().getByRole('checkbox', { name, exact: true }).count(), 1);
    assert.match(await group().textContent(), /Leave all unchecked to allow all types for compatibility/);
    assert.deepEqual(await selected(), []);
    assert.deepEqual(await page.evaluate(() => window.currentDraft.postprocess_block_types), []);
    const request = await save();
    assert.equal(request.method, 'PATCH'); assert.deepEqual(request.body.postprocess_block_types, []);
  });
  await check('explicit subset reopens, edits and round-trips without adding defaults', async () => {
    await open('subset'); assert.deepEqual(await selected(), ['note', 'table']);
    await group().getByRole('checkbox', { name: 'Cards', exact: true }).check();
    assert.deepEqual((await save()).body.postprocess_block_types, ['note', 'table', 'cards']);
    await open('legacy'); await open('subset'); assert.deepEqual(await selected(), ['note', 'table', 'cards']);
  });
  await check('deselect last type sends explicit [] and reopens empty', async () => {
    for (const name of ['Note', 'Table', 'Cards']) await group().getByRole('checkbox', { name, exact: true }).uncheck();
    assert.deepEqual((await save()).body.postprocess_block_types, []);
    await open('legacy'); await open('subset'); assert.deepEqual(await selected(), []);
    await open('empty'); assert.deepEqual((await save()).body.postprocess_block_types, []);
  });
  await check('uncached edit loads through the production route hook and GET serializer', async () => {
    await open('remote'); assert.deepEqual(await selected(), ['checklist']);
    assert.deepEqual((await save()).body.postprocess_block_types, ['checklist']);
  });
  await check('unknown server values are retained when changing known types', async () => {
    await open('future'); await group().getByRole('checkbox', { name: 'Text', exact: true }).uncheck();
    await group().getByRole('checkbox', { name: 'Vote', exact: true }).check();
    assert.deepEqual((await save()).body.postprocess_block_types, ['future_block', 'vote']);
  });
  await check('new resets selection, POST subset; delete then new resets to []', async () => {
    await page.evaluate(() => window.newCommand());
    await page.waitForFunction(() => !window.currentDraft.id);
    assert.deepEqual(await selected(), []);
    assert.deepEqual(await page.evaluate(() => window.currentDraft.postprocess_block_types), []);
    await page.getByRole('textbox', { name: 'Command name', exact: true }).fill('new-command');
    await page.getByRole('textbox', { name: 'Post-processing prompt', exact: true }).fill('Synthetic instruction');
    await group().getByRole('checkbox', { name: 'Timeline', exact: true }).check();
    const request = await save(); assert.equal(request.method, 'POST'); assert.deepEqual(request.body.postprocess_block_types, ['timeline']);
    await page.getByRole('button', { name: 'Delete', exact: true }).click();
    await page.waitForFunction(() => !window.currentDraft.id);
    assert.equal(requests.at(-1).method, 'DELETE'); assert.deepEqual(await selected(), []);
    assert.deepEqual(await page.evaluate(() => window.currentDraft.postprocess_block_types), []);
  });
  await check('320/360/768px production CSS has no overflow and Save is really clickable', async () => {
    await open('subset');
    for (const width of [320, 360, 768]) {
      await page.setViewportSize({ width, height: 640 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      assert.deepEqual(await group().locator('label').evaluateAll(nodes => nodes.filter(n => n.getBoundingClientRect().right > innerWidth || n.getBoundingClientRect().left < 0).map(n => n.textContent)), []);
      assert.deepEqual((await save()).body.postprocess_block_types, []);
    }
  });
  await check('all nine types toggle by keyboard and round-trip in explicit order', async () => {
    await open('empty');
    const boxes = group().getByRole('checkbox');
    for (let i = 0; i < 9; i++) { await boxes.nth(i).focus(); await page.keyboard.press('Space'); }
    const types = ['text', 'note', 'table', 'comments', 'commentary', 'checklist', 'timeline', 'vote', 'cards'];
    assert.deepEqual(await selected(), types); assert.deepEqual((await save()).body.postprocess_block_types, types);
  });
  for (const result of ['found', 'missing', 'reject']) {
    await check(`delayed A to authored New ignores ${result}; real Save POSTs vote`, async () => {
      const index = heldGets.length;
      await delayOpen('A');
      await page.evaluate(() => window.newCommand());
      await page.waitForFunction(() => !window.currentDraft.id);
      await page.getByRole('textbox', { name: 'Command name', exact: true }).fill('unsaved-new');
      await page.getByRole('textbox', { name: 'Post-processing prompt', exact: true }).fill('new prompt');
      await group().getByRole('checkbox', { name: 'Vote', exact: true }).check();
      await release(index, result === 'reject' ? { detail: 'stale error' } : result === 'missing' ? [] : [command('A', { postprocess_prompt: 'old prompt', postprocess_block_types: ['table'] })], result === 'reject' ? 500 : 200);
      assert.equal(await page.evaluate(() => window.currentDraft.id), undefined);
      assert.equal(await page.getByRole('textbox', { name: 'Command name', exact: true }).inputValue(), 'unsaved-new');
      assert.equal(await page.getByRole('textbox', { name: 'Post-processing prompt', exact: true }).inputValue(), 'new prompt');
      assert.deepEqual(await selected(), ['vote']);
      assert.equal(await page.evaluate(() => window.currentStatus), '');
      const request = await save();
      assert.equal(request.method, 'POST'); assert.equal(request.path, '/api/chat-commands');
      assert.equal(request.body.name, 'unsaved-new'); assert.equal(request.body.postprocess_prompt, 'new prompt');
      assert.deepEqual(request.body.postprocess_block_types, ['vote']);
    });
  }
  await check('pending A to cached B and A/B/A only accepts newest visit', async () => {
    let index = heldGets.length;
    await delayOpen('A'); await open('legacy');
    await release(index, [command('A', { postprocess_block_types: ['table'] })]);
    assert.equal(await page.evaluate(() => window.currentDraft.id), 'legacy');
    index = heldGets.length;
    await delayOpen('A'); await open('legacy'); await delayOpen('A');
    await release(index, [command('A', { name: 'old A', postprocess_block_types: ['table'] })]);
    assert.equal(await page.evaluate(() => window.currentDraft.id), 'legacy');
    await release(index + 1, [command('A', { name: 'latest A', postprocess_block_types: ['vote'] })]);
    await page.waitForFunction(() => window.currentDraft.name === 'latest A');
    assert.deepEqual(await selected(), ['vote']); assert.equal(await page.evaluate(() => window.currentStatus), '');
    holdGets = false;
  });
  await check('Korean and Japanese expose localized group, hint and labels', async () => {
    for (const [locale, heading, text, hint] of [
      ['ko', '후처리 출력 유형', '텍스트', '선택하지 않으면 호환성을 위해 모든 유형을 허용합니다.'],
      ['ja', '後処理の出力形式', 'テキスト', '何も選択しない場合、互換性のためすべての形式を許可します。'],
    ]) {
      // Use the production language switch without reinitializing the fixture.
      await page.evaluate(locale => window.setFixtureLocale(locale), locale);
      const localized = page.getByRole('group', { name: heading, exact: true });
      await localized.waitFor();
      assert.equal(await localized.getByRole('checkbox').count(), 9);
      assert.equal(await localized.getByRole('checkbox', { name: text, exact: true }).count(), 1);
      assert.ok((await localized.textContent()).includes(hint));
    }
  });
  assert.deepEqual(errors, []);
  console.log(`${passed} real-browser command-output regressions passed; zero runtime exceptions`);
} finally {
  await browser?.close(); await server.close();
}
