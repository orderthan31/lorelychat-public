import assert from 'node:assert/strict';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from '@playwright/test';

const plugin = { name: 'message-actions-fixture', configureServer(server) {
  server.middlewares.use('/message-actions-test', async (_req, res) => {
    res.setHeader('Content-Type', 'text/html');
    res.end(await server.transformIndexHtml('/message-actions-test', '<html><body><script type="module" src="/tests/message-actions.fixture.tsx"></script></body></html>'));
  });
} };
const server = await createServer({ configFile: false, envFile: false, plugins: [plugin, react()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
let browser;
try {
  await server.listen(); browser = await chromium.launch({ headless: true });
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  const page = await browser.newPage(); const errors = []; const blocked = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/*', (route) => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== origin || url.pathname.startsWith('/api') || !['GET', 'HEAD'].includes(request.method())) { blocked.push(request.url()); return route.abort(); }
    return route.continue();
  });
  await page.goto(`${origin}/message-actions-test`);
  await page.waitForFunction(() => typeof window.runMessageActionTests === 'function');
  const results = await page.evaluate(() => window.runMessageActionTests());
  for (const result of results) console.log(`${result.error ? 'FAIL' : 'PASS'} ${result.name}${result.error ? ` — ${result.error}` : ''}`);
  console.log(JSON.stringify({ passed: results.filter((r) => !r.error).length, total: results.length, errors, blocked }));
  assert.deepEqual(errors, []); assert.deepEqual(blocked, []); assert.equal(results.filter((r) => r.error).length, 0);
} finally { await browser?.close(); await server.close(); }
