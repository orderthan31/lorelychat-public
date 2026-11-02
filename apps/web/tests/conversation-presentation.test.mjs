import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';
import { chromium, expect } from '@playwright/test';

const plugin = { name: 'conversation-presentation-fixture', enforce: 'pre',
  // Optional archived, unmodified production source for a reproducible red run.
  transform(code, id) {
    if (process.env.LORECHAT_PRESENTATION_BASELINE && id.endsWith('/src/views/ConversationDetail/index.tsx')) return readFileSync(process.env.LORECHAT_PRESENTATION_BASELINE, 'utf8');
  },
  configureServer(server) {
    server.middlewares.use('/presentation-test', async (_req, res) => {
      res.setHeader('Content-Type', 'text/html');
      res.end(await server.transformIndexHtml('/presentation-test', '<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/conversation-presentation.fixture.tsx"></script></body></html>'));
    });
  },
};
const server = await createServer({ configFile: false, envFile: false, plugins: [plugin, react(), tailwind()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
let browser;
try {
  await server.listen(); browser = await chromium.launch({ headless: true });
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.setDefaultTimeout(6000);
  const errors = [], blocked = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/*', (route) => {
    const request = route.request(), url = new URL(request.url());
    // The production stylesheet imports a font; keep that request synthetic too.
    if (request.url() === 'https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css') return route.fulfill({ status: 200, contentType: 'text/css', body: '' });
    if (url.origin !== origin || url.pathname.startsWith('/api') || !['GET', 'HEAD'].includes(request.method())) { blocked.push(request.url()); return route.abort(); }
    return route.continue();
  });
  await page.goto(`${origin}/presentation-test`);
  const input = page.locator('.composer-input-wrap > textarea');
  await expect(input).toHaveValue('first line\nsecond line\nthird line\nfourth line');
  const height = await input.evaluate((node) => node.style.height);
  assert.ok(parseFloat(height) > 50, 'multiline initially sized');
  await page.evaluate(() => window.presentation.enterMessageSelectionMode('m1'));
  await expect(page.locator('.message-selection-toolbar')).toBeVisible(); await expect(input).toHaveCount(0);
  await page.keyboard.press('Escape'); await expect(input).toBeVisible();
  assert.equal(await input.evaluate((node) => node.style.height), height, 'selection cancellation restores unchanged multiline draft height');
  assert.ok(!(await page.locator('.chat-title-block').innerText()).includes('복사하거나 삭제할 버블을 선택하세요'));
  console.log('PASS draft text, multiline size and instruction restored after selection');

  await page.getByRole('button', { name: '메시지 크게 쓰기', exact: true }).click();
  const expanded = page.locator('.composer-editor-modal textarea'); await expect(expanded).toBeVisible();
  await expanded.fill('Expanded\nretained');
  await page.evaluate(() => window.presentation.enterMessageSelectionMode('m1'));
  await expect(expanded).toHaveCount(0);
  await page.evaluate(() => window.presentation.setBusy(true));
  await expect(page.locator('.message-selection-toolbar button').last()).toBeDisabled();
  await page.keyboard.press('Escape'); await expect(page.locator('.message-selection-toolbar')).toBeVisible();
  await page.evaluate(() => window.presentation.setBusy(false));
  await expect(page.locator('.message-selection-toolbar button').last()).toBeEnabled();
  await page.keyboard.press('Escape'); await expect(expanded).toHaveValue('Expanded\nretained');
  await page.getByRole('button', { name: '접기', exact: true }).click();
  await input.fill('!lo'); await page.locator('.command-autocomplete-item').click(); await expect(input).toHaveValue('!look ');
  await input.fill('Keyboard'); await input.press('Enter'); await expect(input).toHaveValue('Keyboard\n'); await input.press('Control+Enter');
  assert.deepEqual((await page.evaluate(() => window.presentation.events)).at(-1), ['send', 'Keyboard\n', 'u1']);
  await page.getByRole('button', { name: 'Look 커맨드 해제', exact: true }).click();
  console.log('PASS expanded editor state, busy Escape, command autocomplete and keyboard send');

  await page.getByLabel('배틀 컨트롤 열기', { exact: true }).click();
  await page.locator('.battle-control-panel > select').selectOption('start');
  await expect(page.locator('.battle-control-fields select').nth(0)).toHaveValue('c1');
  await expect(page.locator('.battle-control-fields select').nth(1)).toHaveValue('c2');
  await page.evaluate(() => window.presentation.enterMessageSelectionMode('m1'));
  await expect(page.locator('.message-selection-toolbar')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.battle-control-panel')).toBeVisible();
  await page.getByRole('button', { name: '배틀 컨트롤 닫기', exact: true }).click();
  console.log('PASS battle action, automatic participants and open-panel state survive selection');

  const first = page.locator('[data-chat-row="m1"]');
  await first.getByRole('button', { name: '버블 액션 메뉴' }).click(); await first.getByRole('button', { name: '수정', exact: true }).click();
  await first.getByLabel('버블 대사 수정').fill('Edited speech'); await first.getByLabel('버블 행동 수정').fill('Edited action'); await first.getByLabel('버블 속마음 수정').fill('Edited thought');
  await first.getByLabel('버블 감정 수정').fill('calm');
  await first.getByRole('button', { name: '수정 저장', exact: true }).click(); await expect(first.getByLabel('버블 대사 수정')).toHaveCount(0);
  assert.deepEqual((await page.evaluate(() => window.presentation.events)).at(-1), ['edit', 'fixture', 'm1', { content: 'Edited speech', action: 'Edited action', thought: 'Edited thought', emotion: 'calm' }]);
  await first.getByRole('button', { name: '버블 액션 메뉴' }).click(); await first.getByRole('button', { name: '음성', exact: true }).click();
  await first.getByRole('button', { name: '버블 액션 메뉴' }).click(); await first.getByRole('button', { name: '이후 재생성', exact: true }).click();
  await page.getByRole('button', { name: '현재 대화로 ↓' }).click();
  await page.getByRole('button', { name: '대화방 목록으로' }).click();
  const events = await page.evaluate(() => window.presentation.events);
  for (const event of [['tts', 'm1'], ['regenerate', 'm1'], ['latest'], ['navigate', '/conversations'], ['clear-command', 'cmd']]) assert.ok(events.some((value) => JSON.stringify(value) === JSON.stringify(event)), `forwarded ${event}`);
  console.log('PASS semantic edit uses actual production action hook, TTS/regenerate/navigation forwarding');
  assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
  console.log(JSON.stringify({ errors, blocked, viewport: 390, result: 'passed' }));
} finally { await browser?.close(); await server.close(); }
