import { testViteCacheDir } from './isolated-vite-cache.mjs';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium, expect } from '@playwright/test';
const server = await createServer({ cacheDir: testViteCacheDir, configFile: false, envFile: false, plugins: [react(), {
  name: 'router-fixture', configureServer(s) { s.middlewares.use(async (req, res, next) => {
    if (!req.headers.accept?.includes('text/html')) return next();
    res.setHeader('Content-Type', 'text/html');
    res.end(await s.transformIndexHtml(req.url, '<html><body><div id="root"></div><script type="module" src="/tests/router.fixture.tsx"></script></body></html>'));
  }); },
}], server: { host: '127.0.0.1', port: 0, hmr: false, watch: null }, logLevel: 'error' });
let browser;
try {
  await server.listen(); browser = await chromium.launch({ headless: true });
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  const page = await browser.newPage(); const errors = [], blocked = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', r => {
    const url = new URL(r.request().url());
    if (url.origin !== origin || url.pathname.startsWith('/api/')) { blocked.push(url.href); return r.abort(); }
    return r.continue();
  });
  const cases = [
    ['/', { page: 'home' }], ['/characters', { page: 'characters' }],
    ['/characters/new', { page: 'characterNew' }], ['/characters/id', { page: 'characterDetail', id: 'id' }],
    ['/characters/a%2Fb%20%ED%95%9C%25/assets', { page: 'characterAssets', id: 'a/b 한%' }],
    ['/conversations', { page: 'conversations' }], ['/conversations/new', { page: 'conversationNew' }],
    ['/conversations/room', { page: 'conversationDetail', id: 'room' }], ['/conversations/room/edit', { page: 'conversationEdit', id: 'room' }],
    ['/presets', { page: 'presets' }], ['/presets/new', { page: 'presetNew' }], ['/presets/p', { page: 'presetDetail', id: 'p' }],
    ['/chat-commands', { page: 'chatCommands' }], ['/chat-commands/new', { page: 'chatCommandNew' }], ['/chat-commands/c', { page: 'chatCommandDetail', id: 'c' }],
    ['/world-settings', { page: 'worldSettings' }], ['/world-settings/new', { page: 'worldSettingNew' }], ['/world-settings/w', { page: 'worldSettingDetail', id: 'w' }],
    ['/statistics', { page: 'statistics' }], ['/settings', { page: 'settings' }], ['/settings/lore-agent', { page: 'agentSettings' }],
    ['/model-settings', { page: 'modelSettings', section: 'runtime' }], ['/settings/models', { page: 'modelSettings', section: 'runtime' }],
    ['/settings/providers', { page: 'modelSettings', section: 'providers' }], ['/settings/model-catalog', { page: 'modelSettings', section: 'models' }],
    ['/unknown/path', { page: 'home' }], ['/operations', { page: 'home' }], ['/uiux', { page: 'home' }],
  ];
  const routeIs = value => expect(page.locator('[data-route]')).toHaveText(JSON.stringify(value));
  // Every product route/deep link boots the real RouterProvider and adapter.
  for (const [path, expected] of cases) { await page.goto(origin + path); await routeIs(expected); }
  await page.goto(origin + '/settings'); await routeIs({ page: 'settings' });
  await page.getByLabel('persistent draft').fill('IME draft preserved');
  await page.getByRole('button', { name: 'Open drawer', exact: true }).click();
  await expect(page.locator('[data-drawer]')).toHaveText('true');
  const navigate = async path => { await page.getByLabel('destination', { exact: true }).fill(path); await page.getByRole('button', { name: 'Navigate', exact: true }).click(); };
  await navigate('/characters/a%2Fb%20%ED%95%9C%25/assets');
  await routeIs({ page: 'characterAssets', id: 'a/b 한%' });
  await expect(page.locator('[data-drawer]')).toHaveText('false');
  await expect(page.getByLabel('persistent draft')).toHaveValue('IME draft preserved');
  await page.goBack(); await routeIs({ page: 'settings' });
  await page.goForward(); await routeIs({ page: 'characterAssets', id: 'a/b 한%' });
  await expect(page.getByLabel('persistent draft')).toHaveValue('IME draft preserved');
  await navigate('/settings/providers?tab=one#anchor'); await routeIs({ page: 'modelSettings', section: 'providers' });
  await page.reload(); await routeIs({ page: 'modelSettings', section: 'providers' });
  assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
  console.log(`PASS ${cases.length} direct routes, encoded IDs, navigation/back/forward, aliases, unknown/retired fallback, stable root and drawer close`);
} finally { await browser?.close(); await server.close(); }
