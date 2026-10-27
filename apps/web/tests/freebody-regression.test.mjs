import assert from 'node:assert/strict';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';
import { chromium } from '@playwright/test';

// Real React, real production CSS, actual API serializer. All data is synthetic;
// there is no proxy and no live API, model, DB or service involved.
const fixturePlugin = { name: 'freebody-fixture', configureServer(server) {
  server.middlewares.use('/freebody-test', async (_req, res) => {
    res.setHeader('Content-Type', 'text/html');
    res.end(await server.transformIndexHtml('/freebody-test', '<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/freebody.fixture.tsx"></script></body></html>'));
  });
} };
const server = await createServer({ configFile: false, envFile: false, plugins: [fixturePlugin, react(), tailwind()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
let browser;
let passed = 0;
try {
  await server.listen();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 360, height: 800 }, locale: 'ko-KR' });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== '127.0.0.1' || url.pathname.startsWith('/api/')) return route.abort();
    return route.continue();
  });
  const parts = [ { type: 'action', text: 'Action one' }, { type: 'dialogue', text: '**Dialogue one**' }, { type: 'thought', text: 'Thought one' }, { type: 'dialogue', text: 'Dialogue two\n\nNext paragraph' } ];
  const raw = '<a>Action one</a>**Dialogue one**<t>Thought one</t>Dialogue two\n\nNext paragraph';
  const msg = (id, render_parts = parts, protocol = 'tagged_text') => ({ id, speaker_type: 'character', speaker_id: 'c1', content: 'AGGREGATE dialogue', action: 'AGGREGATE action', thought: 'AGGREGATE thought', metadata: { response_protocol: protocol, raw_body: raw, render_parts } });
  await page.addInitScript(messages => { window.fixtureMessages = messages; Object.defineProperty(navigator, 'clipboard', { value: { writeText: async text => { window.copied = text; } } }); }, [msg('ordered')]);
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/freebody-test`);
  const row = id => page.locator(`[data-chat-row="${id}"]`);
  const menu = async id => { await row(id).getByRole('button', { name: '버블 액션 메뉴' }).click(); };
  const load = async messages => { await page.evaluate(messages => window.setFixtureMessages(messages), messages); await row(messages[0].id).waitFor(); };
  const check = async (name, fn) => { await fn(); console.log(`ok ${++passed} - ${name}`); };
  await row('ordered').waitFor();
  await check('ordered parts exactly once, one bubble, Markdown stays styling', async () => {
    assert.deepEqual(await row('ordered').locator('[data-semantic-part]').evaluateAll(nodes => nodes.map(n => [n.dataset.semanticPart, n.textContent])), [['action','Action one'],['dialogue','Dialogue one'],['thought','Thought one'],['dialogue','Dialogue two\nNext paragraph']]);
    assert.equal(await page.locator('article.bubble').count(), 1);
    assert.equal(await row('ordered').locator('strong').textContent(), 'Dialogue one');
    assert.ok(!(await row('ordered').textContent()).includes('AGGREGATE'));
  });
  await check('clipboard preserves ordered parts without aggregate duplication', async () => {
    await menu('ordered'); await row('ordered').getByRole('button', { name: '복사', exact: true }).click();
    assert.equal(await page.evaluate(() => window.copied), 'Action one\n**Dialogue one**\n(Thought one)\nDialogue two\n\nNext paragraph');
  });
  await check('free-body editor sends raw text + optional emotion only via real PATCH serializer', async () => {
    await page.route('**/api/conversations/fixture/messages/ordered', async route => {
      assert.equal(route.request().method(), 'PATCH');
      const payload = route.request().postDataJSON();
      assert.deepEqual(payload, { raw_body: '  <a>Changed</a>\nNew **dialogue**<t>private</t>  ', emotion: '' });
      await route.fulfill({ json: { id: 'ordered', ...payload } });
    });
    await menu('ordered'); await row('ordered').getByRole('button', { name: '수정', exact: true }).click();
    assert.equal(await row('ordered').locator('textarea').count(), 1);
    assert.equal(await row('ordered').getByRole('textbox', { name: '버블 본문 수정' }).inputValue(), raw);
    assert.equal(await row('ordered').getByRole('textbox', { name: '버블 감정 수정' }).getAttribute('required'), null);
    await row('ordered').getByRole('textbox', { name: '버블 본문 수정' }).fill('   ');
    assert.equal(await row('ordered').getByRole('button', { name: '수정 저장' }).isDisabled(), true);
    await row('ordered').getByRole('textbox', { name: '버블 본문 수정' }).fill('  <a>Changed</a>\nNew **dialogue**<t>private</t>  ');
    await row('ordered').getByRole('button', { name: '수정 저장' }).click();
    await page.waitForFunction(() => window.saved?.id === 'ordered');
    assert.deepEqual(await page.evaluate(() => Object.keys(window.saved).sort()), ['emotion','id','raw_body']);
  });
  await check('dialogue-only free_body_json has no action/thought or empty regions', async () => {
    await load([msg('dialogue', [{ type: 'dialogue', text: 'Only *speech*' }], 'free_body_json')]);
    assert.equal(await row('dialogue').locator('[data-semantic-part]').count(), 1);
    assert.equal(await row('dialogue').locator('em').textContent(), 'speech');
    assert.ok(!(await row('dialogue').textContent()).includes('AGGREGATE'));
    await row('dialogue').getByRole('button', { name: '🔊 음성' }).click();
    assert.deepEqual(await page.evaluate(() => window.ttsRequests), ['dialogue']);
    await menu('dialogue'); await row('dialogue').getByRole('button', { name: '수정', exact: true }).click();
    assert.equal(await row('dialogue').locator('textarea').count(), 1);
  });
  await check('action/thought-only and narration never expose TTS (including stale audio)', async () => {
    for (const [id, parts] of [['action', [{type:'action',text:'Moves'}]], ['thought', [{type:'thought',text:'Thinks'}]], ['both', [{type:'action',text:'Moves'},{type:'thought',text:'Thinks'}]]]) {
      await load([{...msg(id, parts), tts_audio_url: '/stale.mp3'}]);
      assert.equal(await row(id).locator('audio').count(), 0);
      assert.equal(await row(id).locator('[data-semantic-part]').count(), parts.length);
      assert.ok(!(await row(id).textContent()).includes('(빈 응답)'));
      assert.equal(await row(id).getByRole('button', {name: /음성/}).count(), 0);
      await menu(id);
      assert.equal(await row(id).getByRole('button', {name: /음성/}).count(), 0);
    }
    await load([{...msg('narrator', [{type:'dialogue',text:'Narration'}]), speaker_type:'storytelling'}]);
    assert.equal(await row('narrator').getByRole('button', {name: /음성/}).count(), 0);
  });
  await check('legacy editor fallback retains three textareas and legacy payload', async () => {
    await load([{id:'legacy', speaker_type:'user', content:'Old dialogue', action:'Old action', thought:'Old thought', emotion:'calm'}]);
    await page.route('**/api/conversations/fixture/messages/legacy', async route => {
      assert.deepEqual(route.request().postDataJSON(), { content:'Edited', action:'Old action', thought:'Old thought', emotion:'calm' });
      await route.fulfill({json:{id:'legacy'}});
    });
    await menu('legacy'); await row('legacy').getByRole('button', {name:'수정',exact:true}).click();
    assert.equal(await row('legacy').locator('textarea').count(), 3);
    await row('legacy').getByRole('textbox', {name:'버블 대사 수정'}).fill('Edited');
    await row('legacy').getByRole('button', {name:'수정 저장'}).click();
    await page.waitForFunction(() => window.saved?.id === 'legacy');
  });
  await check('unsupported/malicious HTML stays visible and inert; code and emphasis preserved', async () => {
    const literal = '<script>window.attacked=1</script>\n<img src=x onerror="window.attacked=2">\n<unknown>literal</unknown> **bold** *italic* `<a>quoted</a>`';
    await load([msg('unsafe', [{type:'dialogue',text:literal}])]);
    const body = row('unsafe').locator('[data-semantic-part]');
    assert.ok((await body.textContent()).includes('<script>window.attacked=1</script>'));
    assert.ok((await body.textContent()).includes('<img src=x onerror="window.attacked=2">'));
    assert.ok((await body.textContent()).includes('<unknown>literal</unknown>'));
    assert.equal(await body.locator('script,img,unknown,a').count(), 0);
    assert.equal(await body.locator('strong').textContent(), 'bold');
    assert.equal(await body.locator('em').textContent(), 'italic');
    assert.equal(await body.locator('code').textContent(), '<a>quoted</a>');
    assert.equal(await page.evaluate(() => window.attacked), undefined);
    await load([{ id:'legacy-html', speaker_type:'user', content:'<script>hidden</script>\n\n**legacy bold**' }]);
    assert.equal(await row('legacy-html').locator('script').count(), 0);
    assert.ok(!(await row('legacy-html').textContent()).includes('<script>'));
    assert.equal(await row('legacy-html').locator('strong').textContent(), 'legacy bold');
  });
  await check('authoring parts render once, current user is not duplicated', async () => {
    const parsed = await page.evaluate(() => window.parseInputMarkup('Hello **world**'));
    await load([{id:'user',speaker_type:'user',content:parsed.dialogue || 'Hello **world**',metadata:{render_parts:parsed.parts}}]);
    assert.equal(await row('user').count(), 1);
    assert.equal(await row('user').locator('[data-semantic-part]').count(), 1);
  });
  await check('320/360/768/1280px: long literal body and editor have no overflow', async () => {
    await load([msg('long', [{type:'dialogue',text:'<unknown>'+'LongWord'.repeat(100)+'</unknown>'}])]);
    for (const width of [320,360,768,1280]) {
      await page.setViewportSize({width,height:800});
      assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('.bubble, [data-semantic-part], .chat-thread')].filter(n => n.scrollWidth > n.clientWidth + 1).map(n => n.className)), []);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    }
    await page.setViewportSize({width:320,height:800});
    await menu('long'); await row('long').getByRole('button', {name:'수정',exact:true}).click();
    assert.deepEqual(await row('long').locator('form, textarea, label').evaluateAll(nodes => nodes.filter(n => n.getBoundingClientRect().right > innerWidth || n.getBoundingClientRect().left < 0).map(n => n.tagName)), []);
    assert.ok(await row('long').getByRole('button',{name:'수정 저장'}).isVisible());
  });
  assert.deepEqual(errors, []);
  console.log(`${passed} real-browser free-body regressions passed; zero runtime exceptions`);
} finally {
  await browser?.close();
  await server.close();
}
