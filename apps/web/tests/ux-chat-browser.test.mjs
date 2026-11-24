import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';
import { chromium, expect } from '@playwright/test';

// All application API traffic is fulfilled here, even against the live dev UI.
// No participant/runtime write, generation or TTS reaches an actual API.
const out = process.env.UX_ARTIFACT_DIR;
if (out) mkdirSync(out, { recursive: true });
const externalBase = process.env.UX_BASE_URL;
const fixture = { name: 'ux-full-app-fixture', configureServer(server) {
  server.middlewares.use(async (req, res, next) => {
    if (!req.url.startsWith('/conversations/')) return next();
    res.setHeader('Content-Type', 'text/html');
    res.end(await server.transformIndexHtml(req.url, '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/hardening-browser.fixture.tsx"></script></body></html>'));
  });
}};
const server = externalBase ? null : await createServer({ configFile: false, envFile: false, plugins: [fixture, react(), tailwind()], server: { host: '127.0.0.1', port: 0, watch: null, hmr: false }, logLevel: 'error' });
let browser;
const checks = [], errors = [], unexpectedWrites = [], blockedExternal = [];
const requests = [];
const ko = JSON.parse(readFileSync('src/i18n/messages.ko.json', 'utf8'));
const t = key => ko[key] || key;
try {
  if (server) await server.listen();
  const origin = externalBase || `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({ headless: true });
  async function makePage(width, height, theme = 'light') {
    const page = await browser.newPage({ viewport: { width, height } });
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(theme => {
      window.__uxClientErrors = [];
      window.addEventListener('error', event => { if (event.message) window.__uxClientErrors.push(event.message); }, true);
      localStorage.setItem('lorechat-language', 'ko');
      localStorage.setItem('lorely-chat-theme', theme);
    }, theme);
    const state = { invites: 0, patches: 0, hold: null, release: null, failRoom: false, initialDelay: 0, participants: [{ type: 'user', id: 'user' }, { type: 'character', id: 'c1' }], setting: {} };
    await page.route('**/*', async route => {
      const req = route.request(), u = new URL(req.url()), path = u.pathname;
      if (u.origin !== origin) { blockedExternal.push(u.origin); return route.abort(); }
      if (!path.startsWith('/api/')) return ['GET', 'HEAD'].includes(req.method()) ? route.continue() : route.abort();
      requests.push({ path, method: req.method() });
      const json = (data, status = 200) => route.fulfill({ status, json: data });
      if (path === '/api/images/prepare') return json({ image_url: '/images/cache/synthetic/preview.webp' });
      if (path.startsWith('/api/images/cache/')) return route.fulfill({ contentType: 'image/webp', body: readFileSync('public/brand/lorely-app-icon-light.webp') });
      if (req.method() === 'POST' && path === '/api/conversations/A/participants') {
        state.invites++;
        state.participants.push(JSON.parse(req.postData()));
        return json(state.participants);
      }
      if (req.method() === 'PATCH' && path === '/api/runtime-settings/conversations/A') {
        state.patches++;
        state.setting = { ...state.setting, ...JSON.parse(req.postData()) };
        return json(state.setting);
      }
      if (req.method() === 'PATCH' && path === '/api/conversations/A') {
        return json({ id: 'A', title: 'Synthetic UX room', genre_mode: 'romance', mode: 'multi_character', tts_enabled: false, ...JSON.parse(req.postData()) });
      }
      if (req.method() === 'POST' && path.endsWith('/read')) return json({});
      if (!['GET', 'HEAD'].includes(req.method())) { unexpectedWrites.push({ path, method: req.method() }); return route.abort(); }
      if (path === '/api/conversations/A') {
        if (state.initialDelay) { const ms = state.initialDelay; state.initialDelay = 0; await new Promise(r => setTimeout(r, ms)); }
        if (state.hold) await state.hold;
        if (state.failRoom) return json({ detail: 'Synthetic refresh unavailable' }, 503);
        return json({ id: 'A', title: 'Synthetic UX room', genre_mode: 'romance', mode: 'multi_character', tts_enabled: false });
      }
      if (path === '/api/conversations/A/messages') return json(Array.from({ length: 40 }, (_, i) => ({ id: `m-${i}`, conversation_id: 'A', speaker_type: 'user', content: `Synthetic history ${i}. ` + 'Retained conversation text. '.repeat(12), created_at: `2026-11-14T00:${String(i).padStart(2, '0')}:00Z`, ...([0, 19, 39].includes(i) ? { assets: [{ id: `asset-${i}`, label: `Synthetic scene ${i}`, image_url: `/api/images/cache/synthetic/scene-${i}.webp` }] } : {}) })));
      if (path.endsWith('/participants')) return json(state.participants);
      if (path.includes('/generation-jobs/active')) return json({ jobs: [] });
      if (path.endsWith('/context')) return json({ scene: { summary: 'Synthetic story context' }, memories: [] });
      if (path === '/api/conversations') return json([{ id: 'A', title: 'Synthetic UX room' }]);
      if (path === '/api/characters') return json(['c1', 'c2', 'c3'].map((id, i) => ({ id, name: `Synthetic cast ${i + 1}`, persona: 'Fixture persona' })));
      if (path.startsWith('/api/runtime-settings')) return json(state.setting);
      if (path === '/api/health' || path === '/api/ready') return json({ status: 'ok' });
      if (path === '/api/world-settings' || path === '/api/presets' || path === '/api/chat-commands' || path.includes('/tts/') || path === '/api/model-options' || path === '/api/model-provider-accounts' || path === '/api/characters/usage-ranking') return json([]);
      return json({});
    });
    return { page, state };
  }
  const input = page => page.getByRole('textbox', { name: '메시지 입력 · Enter 줄바꿈 · Ctrl+Enter 전송', exact: true });
  const overflow = async page => assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth), 0);
  for (const [width, height] of [[320, 568], [390, 844], [820, 1180], [1024, 768], [1180, 820], [1440, 1000], [1920, 1080], [2560, 1440]]) {
    const { page, state } = await makePage(width, height, width === 1024 ? 'dark' : 'light');
    state.initialDelay = 600;
    await page.goto(origin + '/conversations/A');
    await expect(page.locator('[data-chat-loading]')).toBeVisible();
    await expect(input(page)).toBeVisible();
    let original = await input(page).elementHandle();
    await input(page).fill('Unsent draft retained across controls and resize');
    const geometry = await page.locator('.chat-workspace').evaluate(node => ({ width: node.getBoundingClientRect().width, docked: node.classList.contains('has-docked-panel') }));
    assert.equal(geometry.docked, geometry.width >= 960);
    if (geometry.docked) {
      await expect(page.locator('.context-drawer--docked')).toBeVisible();
      assert.equal(await page.locator('.context-drawer').getAttribute('role'), null);
      const chat = await page.locator('.chat-shell').boundingBox();
      const panel = await page.locator('.context-drawer').boundingBox();
      assert.ok(chat.x + chat.width <= panel.x + 1, 'panel does not cover chat');
      assert.ok(chat.width >= 640, 'reading pane minimum preserved');
      assert.ok(chat.width <= 960, 'reading pane stops growing');
      assert.ok(Math.abs(panel.width - Math.max(320, geometry.width - 960)) < 1, 'panel absorbs the remaining width');
    }
    await page.locator('.chat-settings-button').click();
    await expect(page.locator('.chat-settings-embedded')).toBeVisible();
    assert.equal(await page.locator('.chat-settings-popover').count(), 0, 'no duplicate popover');
    const fieldGeometry = await page.locator('.chat-settings-embedded .runtime-setting-fields').evaluate(node => ({ width: node.getBoundingClientRect().width, tracks: getComputedStyle(node).gridTemplateColumns.split(' ').length }));
    assert.equal(fieldGeometry.tracks, fieldGeometry.width <= 560 ? 1 : 2, 'settings follow panel content width, not viewport width');
    if (out) await page.screenshot({ path: `${out}/chat-panel-open-${width}.png` });
    const save = page.getByRole('button', { name: '이 방 설정 저장', exact: true });
    await save.click();
    await expect.poll(() => state.patches).toBe(1);
    await expect(save).toBeEnabled();
    await expect(page.locator('.ux-operation-toast')).toHaveCount(0);
    if (geometry.docked) await page.locator('.drawer-close').click();
    else {
      await expect(page.locator('.context-drawer')).toHaveAttribute('aria-modal', 'true');
      await page.keyboard.press('Escape');
      await expect(page.locator('.chat-settings-button')).toBeFocused();
      assert.equal(await page.locator('.context-drawer').count(), 0);
      assert.equal(await page.evaluate(() => document.body.style.overflow), '');
    }
    await expect(input(page)).toHaveValue('Unsent draft retained across controls and resize');
    assert.equal(await original.evaluate(node => node === document.querySelector('textarea[aria-label="메시지 입력 · Enter 줄바꿈 · Ctrl+Enter 전송"]')), true);
    await overflow(page);
    if ([390, 820, 1024, 1180].includes(width)) {
      const toggle = page.getByRole('button', { name: t('chat.sceneView') });
      await expect(page.locator('.chat-thread')).toHaveAttribute('data-chat-view', 'bubble');
      await toggle.click();
      await expect(page.locator('.chat-thread')).toHaveAttribute('data-chat-view', 'scene');
      const art = page.locator('.scene-art-background');
      const thread = page.locator('.chat-thread');
      await expect(art).toHaveAttribute('src', /scene-39\.webp$/);
      await expect(thread.locator('.bubble-assets')).toHaveCount(0);
      const move = (fraction) => thread.evaluate((node, f) => { node.scrollTop = (node.scrollHeight - node.clientHeight) * f; }, fraction);
      // Move in stages so the real virtualizer measures both sides before checking farther away.
      await move(.5);
      await expect(art).toHaveAttribute('src', /scene-19\.webp$/);
      await move(0);
      await expect(art).toHaveAttribute('src', /scene-0\.webp$/);
      await move(1);
      await expect(art).toHaveAttribute('src', /scene-39\.webp$/);
      await expect(art).toHaveAttribute('data-image-state', 'ready');
      const background = await art.boundingBox();
      assert.ok(background.width === width && background.height === height, 'art spans the chat workspace');
      await page.getByRole('button', { name: t('chat.viewArtwork') }).click();
      const viewer = page.getByRole('dialog', { name: t('chat.viewArtwork') });
      await expect(viewer).toBeVisible();
      await expect(viewer.getByRole('img', { name: 'Synthetic scene 39' })).toHaveCSS('object-fit', 'contain');
      const size = await viewer.boundingBox();
      assert.ok(size.x === 0 && size.y === 0 && size.width === width && size.height === height);
      assert.equal(await viewer.evaluate(node => node.matches(':modal')), true);
      assert.equal(await page.evaluate(() => {
        const input = document.querySelector('.composer textarea');
        const { x, y, width, height } = input.getBoundingClientRect();
        return Boolean(document.elementFromPoint(x + width / 2, y + height / 2)?.closest('.scene-art-full'));
      }), true, 'artwork covers the composer without unmounting it');
      await viewer.getByRole('button', { name: t('chat.showChat') }).click();
      await expect(input(page)).toHaveValue('Unsent draft retained across controls and resize');
      await expect(page.getByRole('button', { name: t('chat.viewArtwork') })).toBeFocused();
      await page.reload();
      await expect(page.locator('.chat-thread')).toHaveAttribute('data-chat-view', 'scene');
      await page.getByRole('button', { name: t('chat.bubbleView') }).click();
      await expect(page.locator('.chat-thread')).toHaveAttribute('data-chat-view', 'bubble');
      await expect(page.locator('.bubble-assets').last()).toBeVisible();
      await input(page).fill('Unsent draft retained across controls and resize');
      original = await input(page).elementHandle();
      await overflow(page);
    }
    if (width >= 1280) {
      await expect.poll(() => page.locator('.chat-shell').evaluate(node => Math.round(node.getBoundingClientRect().width))).toBe(960);
      const closed = await page.locator('.chat-shell').boundingBox();
      assert.ok(Math.abs(closed.x + closed.width / 2 - width / 2) < 1, 'collapsed desktop reading column stays centered');
    }
    if (out) await page.screenshot({ path: `${out}/chat-${width}.png` });
    checks.push({ case: 'cold centered loader, adaptive geometry, embedded save, close/focus, mounted draft', width, height, geometry });
    await page.setViewportSize({ width: width < 800 ? 1440 : 390, height });
    await expect(input(page)).toHaveValue('Unsent draft retained across controls and resize');
    assert.equal(await original.evaluate(node => node === document.querySelector('textarea[aria-label="메시지 입력 · Enter 줄바꿈 · Ctrl+Enter 전송"]')), true);
    await overflow(page);
    checks.push({ case: 'width transition preserves actual composer node and draft', width });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    errors.push(...await page.evaluate(() => window.__uxClientErrors));
    await page.close();
  }

  const { page, state } = await makePage(1024, 768);
  await page.goto(origin + '/conversations/A');
  await expect(input(page)).toBeVisible();
  await expect(page.locator('.context-drawer--docked')).toBeVisible();
  await input(page).fill('Draft during participant refresh');
  const composer = await input(page).elementHandle();
  const thread = page.locator('.chat-thread');
  await thread.evaluate(node => { node.scrollTop = 320; node.dispatchEvent(new Event('scroll', { bubbles: true })); });
  await page.waitForTimeout(150);
  const scroll = await thread.evaluate(node => node.scrollTop);
  state.hold = new Promise(resolve => { state.release = resolve; });
  await page.getByRole('combobox', { name: '초대할 캐릭터 선택', exact: true }).selectOption('c2');
  await page.locator('.drawer-invite-row').evaluate(form => { form.requestSubmit(); form.requestSubmit(); });
  await expect.poll(() => state.invites).toBe(1);
  await page.waitForTimeout(250);
  await expect(input(page)).toHaveValue('Draft during participant refresh');
  assert.equal(await composer.evaluate(node => node.isConnected), true);
  assert.equal(await page.locator('[data-chat-loading]').count(), 0);
  assert.ok(Math.abs((await thread.evaluate(node => node.scrollTop)) - scroll) < 3);
  state.release(); state.hold = null;
  await expect(page.locator('.ux-operation-toast')).toContainText(t('캐릭터 초대 완료'));
  await expect(page.locator('.role-card-list')).toContainText('Synthetic cast 2');
  await expect(input(page)).toHaveValue('Draft during participant refresh');
  assert.ok(Math.abs((await thread.evaluate(node => node.scrollTop)) - scroll) < 3);
  checks.push({ case: 'single-flight invitation retains mounted conversation, draft and reading position while pending and settled' });

  state.failRoom = true;
  await page.getByRole('combobox', { name: '초대할 캐릭터 선택', exact: true }).selectOption('c3');
  await page.getByRole('button', { name: '초대', exact: true }).click();
  await expect(page.locator('.ux-detail-error')).toContainText(t('캐릭터 초대 완료'));
  await expect(page.locator('.ux-detail-error')).toContainText('새로고침 실패');
  await expect(input(page)).toHaveValue('Draft during participant refresh');
  assert.equal(state.invites, 2);
  state.failRoom = false;
  await page.getByRole('button', { name: '재시도', exact: true }).click();
  await expect(page.locator('.ux-detail-error')).toHaveCount(0);
  await expect(page.locator('.role-card-list')).toContainText('Synthetic cast 3');
  assert.equal(state.invites, 2, 'Retry reads state, never repeats invitation');
  checks.push({ case: 'accepted invitation plus failed refresh is honest, retained and recoverable by reads only' });
  await overflow(page);
  if (out) await page.screenshot({ path: `${out}/chat-refresh-retained.png` });
  errors.push(...await page.evaluate(() => window.__uxClientErrors));
  await page.close();
  assert.deepEqual(unexpectedWrites, []);
  assert.deepEqual(errors, []);
  const result = { status: 'PASS', checks, pageErrors: errors, unexpectedWrites, blockedExternal: [...new Set(blockedExternal)], syntheticRequests: requests.length, liveApiRequestsForwarded: 0, target: externalBase ? 'dev frontend with fully synthetic API' : 'isolated App fixture', typography: 'external fonts blocked; fallback-font geometry' };
  if (out) writeFileSync(`${out}/result.json`, JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result));
} finally {
  await browser?.close();
  if (server) await server.close();
}
