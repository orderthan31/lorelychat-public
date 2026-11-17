import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createServer } from 'vite';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';
import { chromium, expect } from '@playwright/test';

// All API traffic is synthetic, including writes and read markers.
const out = process.env.UX_ARTIFACT_DIR;
if (out) mkdirSync(out, { recursive: true });
const external = process.env.UX_BASE_URL;
const fixture = { name: 'worldless-loading-app', configureServer(server) {
  server.middlewares.use(async (req, res, next) => {
    if (!/^\/(?:$|conversations(?:\/|$)|world-settings|chat-commands|presets)/.test(req.url)) return next();
    res.setHeader('Content-Type', 'text/html');
    res.end(await server.transformIndexHtml(req.url, '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/hardening-browser.fixture.tsx"></script></body></html>'));
  });
}};
const cacheDir = mkdtempSync(join(tmpdir(), 'integration-repair-vite-'));
const server = external ? null : await createServer({ cacheDir, configFile: false, envFile: false, plugins: [fixture, react(), tailwind()], server: { host: '127.0.0.1', port: 0, watch: null, hmr: false }, logLevel: 'error' });
const errors = [], unexpected = [], cases = [];
let browser;
try {
  if (server) await server.listen();
  const origin = external || `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({ headless: true });
  for (const [width, height, reduced] of [[390, 844, false], [820, 1180, false], [1024, 768, false], [820, 1180, true]]) {
    const page = await browser.newPage({ viewport: { width, height }, reducedMotion: reduced ? 'reduce' : 'no-preference' });
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => { localStorage.setItem('lorechat-language', 'ko'); localStorage.setItem('lorely-chat-theme', 'light'); });
    const characters = [{ id: 'c1', name: 'Synthetic cast', persona: 'Authored persona' }, { id: 'c2', name: 'Second cast', persona: 'Second persona' }];
    const worlds = [{ id: 'w1', title: 'Synthetic lore', world_seed: 'Authored world', genre_mode: 'romance', enabled: true }, { id: 'w2', title: 'Disabled lore', world_seed: 'Disabled world', enabled: false }];
    let room = { id: 'created', title: 'Synthetic worldless room', mode: 'user_character', genre_mode: 'romance', world_setting_id: null, participants: [{ id: 'c1', type: 'character' }], tts_enabled: false };
    const creates = [], patches = [];
    let release; let hold = new Promise(resolve => { release = resolve; });
    await page.route('**/*', async route => {
      const req = route.request(), u = new URL(req.url()), path = u.pathname;
      if (u.origin !== origin) return route.abort();
      if (!path.startsWith('/api/')) return ['GET', 'HEAD'].includes(req.method()) ? route.continue() : route.abort();
      const json = data => route.fulfill({ json: data });
      if (path === '/api/images/prepare') return json({ image_url: '/images/cache/synthetic/preview.webp' });
      if (path.startsWith('/api/images/cache/')) return route.fulfill({ contentType: 'image/webp', body: readFileSync('public/brand/lorely-app-icon-light.webp') });
      if (path === '/api/conversations' && req.method() === 'POST') { const body = req.postDataJSON(); creates.push(body); room = { ...room, ...body, title: body.title || 'Synthetic worldless room' }; return json(room); }
      if (path === '/api/conversations/created' && req.method() === 'PATCH') { const body = req.postDataJSON(); patches.push(body); room = { ...room, ...body }; return json(room); }
      if (path.endsWith('/read') && req.method() === 'POST') return json({});
      if (!['GET', 'HEAD'].includes(req.method())) { unexpected.push(path); return route.abort(); }
      if (path === '/api/conversations/created') { if (hold) await hold; return json(room); }
      if (path === '/api/conversations') return json([room]);
      if (path === '/api/characters' || path === '/api/characters/usage-ranking') return json(characters);
      if (path === '/api/world-settings') return json(u.searchParams.get('paginated') === 'true' ? { items: worlds, total: worlds.length, page: 1, page_size: 20 } : worlds);
      if (path === '/api/chat-commands') { const commands = [{ id: 'cmd1', name: 'scene', description: 'Authored command description', enabled: true }, { id: 'cmd2', name: 'off', description: 'Authored disabled command', enabled: false }]; return json(u.searchParams.get('paginated') === 'true' ? { items: commands, total: commands.length, page: 1, page_size: 20 } : commands); }
      if (path === '/api/presets') return json([{ id: 'p1', title: 'Synthetic preset', content: 'Authored preset content', preset_type: 'user_setting', enabled: true }, { id: 'p2', title: 'Disabled preset', content: 'Authored disabled content', preset_type: 'user_setting', enabled: false }]);
      if (path.endsWith('/participants')) return json(room.participants);
      if (path.endsWith('/generation-jobs/active')) return json({ jobs: [] });
      if (path.endsWith('/context')) return json({ scene: { user_description: 'Authored user persona' }, memories: [] });
      if (path.endsWith('/messages')) return json([]);
      if (path.startsWith('/api/runtime-settings')) return json({});
      if (path === '/api/health' || path === '/api/ready') return json({ status: 'ok' });
      return json([]);
    });
    await page.goto(origin + '/conversations/created');
    const loading = page.locator('[data-chat-loading]');
    await expect(loading).toBeVisible();
    await expect(loading).toHaveAttribute('role', 'status');
    await expect(loading).toHaveAttribute('aria-label', '불러오는 중…');
    assert.equal(await page.locator('[data-detail-skeleton=chat]').count(), 0);
    if (reduced) {
      await expect(page.locator('[data-chat-loading-static]')).toBeVisible();
      assert.equal(await page.locator('[data-chat-loading-animation]').count(), 0);
    } else {
      const drawn = page.locator('[data-chat-loading-animation] svg');
      await expect(drawn).toBeVisible();
      await expect.poll(() => drawn.locator('path,ellipse').count()).toBeGreaterThan(0);
      const frame = await drawn.innerHTML();
      await expect.poll(() => drawn.innerHTML()).not.toBe(frame);
    }
    const rect = await page.locator('.chat-loading-content').boundingBox();
    assert.ok(rect && rect.width > 0 && rect.height > 0, 'loading indicator has visible bounds');
    assert.ok(rect.x >= 0 && rect.y >= 0 && rect.x + rect.width <= width && rect.y + rect.height <= height, 'loading indicator stays within the viewport below the shared header');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth), 0);
    if (out) await page.screenshot({ path: `${out}/loading-${width}-${reduced ? 'reduced' : 'animated'}.png` });
    release(); hold = null;
    await expect(page.locator('.chat-workspace')).toBeVisible(); await expect(loading).toHaveCount(0);
    await page.goto(origin + '/conversations/new');
    const world = page.locator('[data-world-select]'); await expect(world).toHaveValue('');
    assert.equal(await world.getAttribute('aria-required'), null);
    const cast = page.locator('.cast-character select');
    // Ensure exactly one chosen character independent of the app's initial slot count.
    while (await page.locator('.conversation-cast-row').count() > 1) await page.locator('.cast-remove').last().click();
    await cast.first().selectOption('c1');
    const start = page.locator('.app-actions').getByRole('button', { name: '대화 시작', exact: true });
    await expect(start).toBeEnabled();
    await page.locator('.conversation-create textarea').fill('Authored user persona');
    await start.click(); await expect.poll(() => creates.length).toBe(1);
    assert.equal(creates[0].world_setting_id, null);
    assert.equal(creates[0].participants.filter(p => p.type === 'character').length, 1);
    assert.equal(creates[0].scene.user_description, 'Authored user persona');
    assert.ok(!('world_seed' in creates[0].scene));
    await expect(page).toHaveURL(/\/conversations\/created$/); await expect(page.locator('.chat-workspace')).toBeVisible();
    await page.goto(origin + '/conversations/created/edit');
    await expect(world).toHaveValue('');
    await world.selectOption('w1'); await world.selectOption('');
    await page.getByRole('button', { name: '저장', exact: true }).click();
    await expect.poll(() => patches.length).toBe(1); assert.equal(patches[0].world_setting_id, null);
    await page.goto(origin + '/world-settings');
    await expect(page.locator('[data-resource-card]')).toHaveCount(2);
    assert.equal(await page.locator('[data-resource-card]').getByText('활성', { exact: true }).count(), 0);
    await expect(page.locator('[data-resource-card]').filter({ hasText: 'Disabled lore' }).getByText('비활성', { exact: true })).toBeVisible();
    if (out) await page.screenshot({ path: `${out}/world-list-${width}.png` });
    await page.goto(origin + '/chat-commands');
    await expect(page.locator('[data-resource-card]')).toHaveCount(2);
    assert.equal(await page.locator('[data-resource-card]').getByText('활성', { exact: true }).count(), 0);
    assert.equal(await page.locator('[data-resource-card]').getByText(/후처리:/).count(), 0);
    await page.goto(origin + '/presets');
    await expect(page.locator('[data-resource-card]')).toHaveCount(2);
    assert.equal(await page.locator('[data-resource-card]').getByText('활성', { exact: true }).count(), 0);
    cases.push({ width, height, reduced, loading: 'visible within viewport; real local SVG animates or stays static under reduced motion', worldless: 'real create and edit handlers send null, preserve persona, no seed', lists: 'no default active labels; disabled exception retained' });
    await page.close();
  }
  assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
  const result = { status: 'PASS', cases, pageErrors: errors, unexpectedWrites: unexpected, liveApiRequestsForwarded: 0, typography: 'External fonts blocked; fallback font geometry.' };
  if (out) writeFileSync(`${out}/result.json`, JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result));
} finally { await browser?.close(); if (server) await server.close(); rmSync(cacheDir, { recursive: true, force: true }); }
