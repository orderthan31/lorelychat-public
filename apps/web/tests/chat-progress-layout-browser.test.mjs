import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';
import { chromium, expect } from '@playwright/test';

// Exercise the real dev App with every API request intercepted. No paid calls.
const externalBase = process.env.UX_BASE_URL;
const fixture = { name: 'chat-progress-fixture', configureServer(server) {
  server.middlewares.use(async (req, res, next) => {
    if (!req.url.startsWith('/conversations/')) return next();
    res.setHeader('Content-Type', 'text/html');
    res.end(await server.transformIndexHtml(req.url, '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/hardening-browser.fixture.tsx"></script></body></html>'));
  });
}};
const server = externalBase ? null : await createServer({ configFile: false, envFile: false, cacheDir: mkdtempSync(join(tmpdir(), 'lorechat-progress-vite-')), plugins: [fixture, react(), tailwind()], server: { host: '127.0.0.1', port: 0, watch: null, hmr: false }, logLevel: 'error' });
if (server) await server.listen();
const origin = externalBase || `http://127.0.0.1:${server.httpServer.address().port}`;
const out = process.env.UX_ARTIFACT_DIR;
const ko = JSON.parse(readFileSync('src/i18n/messages.ko.json', 'utf8'));
const t = key => ko[key] || key;
if (out) mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ headless: true });
const checks = [], errors = [], writes = [];
try {
  for (const [width, height] of [[1280, 800], [1920, 1080], [390, 844]]) {
    const page = await browser.newPage({ viewport: { width, height } });
    page.on('pageerror', error => errors.push(error.name));
    await page.addInitScript(() => {
      localStorage.setItem('lorechat-language', 'ko');
      localStorage.setItem('lorely-chat-theme', 'light');
      const sources = [];
      window.EventSource = class {
        callbacks = {}; closed = false;
        constructor() { sources.push(this); queueMicrotask(() => this.onopen?.({})); }
        addEventListener(name, fn) { (this.callbacks[name] ||= []).push(fn); }
        close() { this.closed = true; }
      };
      window.__emitGeneration = status => {
        const data = JSON.stringify({ event_id: status === 'queued' ? 1 : status === 'running' ? 2 : 3, job_id: 'J', conversation_id: 'A', state_version: 1, status, created_at: '2026-11-15T00:00:00Z', generated_message_ids: [] });
        for (const source of sources) if (!source.closed) for (const fn of source.callbacks[status] || []) fn({ data });
      };
    });
    let acceptPost; const accepted = new Promise(resolve => { acceptPost = resolve; });
    let postSeen = false, terminal = false;
    const job = () => ({ id: 'J', conversation_id: 'A', incoming_message_id: 'incoming', status: terminal ? 'completed' : 'running', state_version: 1, generated_message_ids: [] });
    const room = { id: 'A', title: 'Synthetic layout room', mode: 'multi_character', genre_mode: 'romance', tts_enabled: false };
    await page.route('**/*', async route => {
      const req = route.request(), u = new URL(req.url()), path = u.pathname;
      if (u.origin !== origin) return route.abort();
      if (!path.startsWith('/api/')) return ['GET', 'HEAD'].includes(req.method()) ? route.continue() : route.abort();
      const json = (data, status = 200) => route.fulfill({ status, json: data });
      if (path === '/api/images/prepare') return json({ image_url: '/images/cache/synthetic/preview.webp' });
      if (path.startsWith('/api/images/cache/')) return route.fulfill({ contentType: 'image/webp', body: readFileSync('public/brand/lorely-app-icon-light.webp') });
      if (path === '/api/conversations/A/messages/jobs' && req.method() === 'POST') {
        postSeen = true; writes.push({ kind: 'synthetic generation receipt', width }); await accepted;
        return json({ job: job(), incoming_message: { id: 'incoming', conversation_id: 'A', speaker_type: 'user', content: 'Synthetic input', created_at: '2026-11-15T00:00:00Z' } });
      }
      if (path === '/api/conversations/A' && req.method() === 'PATCH') return json({ detail: 'Synthetic save rejected' }, 400);
      if (path.endsWith('/read') && req.method() === 'POST') return json({});
      if (!['GET', 'HEAD'].includes(req.method())) throw Error('Unexpected fixture mutation: ' + path);
      if (path === '/api/conversations/A') return json(room);
      if (path === '/api/conversations') return json([room]);
      if (path.endsWith('/messages')) return json([{ id: 'old', conversation_id: 'A', speaker_type: 'user', content: 'Synthetic history', created_at: '2026-11-14T00:00:00Z' }]);
      if (path.endsWith('/participants')) return json([{ type: 'user', id: 'user_001' }, { type: 'character', id: 'c1' }]);
      if (path.endsWith('/generation-jobs/active')) return json({ jobs: [] });
      if (path.endsWith('/generation-jobs/J')) return json(job());
      if (path.endsWith('/context')) return json({ scene: { summary: 'Synthetic summary' }, memories: [] });
      if (path === '/api/characters' || path === '/api/characters/usage-ranking') return json([{ id: 'c1', name: 'Synthetic cast', persona: 'Fixture' }]);
      if (path.startsWith('/api/runtime-settings')) return json({});
      if (path === '/api/health' || path === '/api/ready') return json({ ready: true });
      return json([]);
    });
    await page.goto(origin + '/conversations/A');
    const composer = page.locator('.composer textarea'); await expect(composer).toBeVisible();
    const panel = page.locator('.context-drawer');
    const toggle = page.locator('.chat-context-button');
    const docked = width >= 960;
    if (!docked) await toggle.click();
    await expect(panel.locator('.drawer-tab-bar')).toBeVisible();
    const geometry = () => panel.locator('.drawer-tab-bar').evaluate(nav => ({ height: nav.getBoundingClientRect().height, top: nav.getBoundingClientRect().top, buttons: [...nav.querySelectorAll('button')].map(b => ({ width: b.getBoundingClientRect().width, height: b.getBoundingClientRect().height })) }));
    const idle = await geometry();
    if (!docked) await panel.getByRole('button', { name: '대화 정보 닫기', exact: true }).click();
    await composer.fill('Synthetic layout input'); await page.locator('.composer button[type=submit]').click();
    await expect.poll(() => postSeen).toBe(true);
    if (!docked) await toggle.click();
    await expect(page.locator('.chat-title-block p')).toContainText(t('대화 생성 job 생성 중…'));
    assert.equal(await panel.getByText(t('대화 생성 job 생성 중…'), { exact: true }).count(), 0, 'progress belongs to chat header, never information panel');
    const assertStable = async phase => {
      const now = await geometry();
      assert.ok(Math.abs(now.height - idle.height) < 1 && Math.abs(now.top - idle.top) < 1, `${phase}: tabs keep their row`);
      assert.ok(now.buttons.every(b => b.height >= 44 && b.width >= 44), `${phase}: targets are not squashed`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth), 0);
    };
    await assertStable('submitting'); acceptPost();
    await expect.poll(() => page.evaluate(() => typeof window.__emitGeneration)).toBe('function');
    // Resume owns the SSE after the synthetic POST acknowledgement.
    for (const [status, label] of [['queued', '대기 중'], ['running', '응답 작성 중'], ['retrying', '응답 재시도 중']]) {
      await expect(async () => { await page.evaluate(status => window.__emitGeneration(status), status); await expect(page.locator('.chat-title-block p')).toContainText(t(label), { timeout: 500 }); }).toPass({ timeout: 5000 });
      assert.equal(await panel.getByText(`${t('대화 생성 중…')} ${t(label)}`, { exact: true }).count(), 0);
      await assertStable(status);
    }
    if (out) await page.screenshot({ path: `${out}/generation-tabs-${width}.png` });
    terminal = true; await page.evaluate(() => window.__emitGeneration('completed'));
    await expect(page.locator('.composer button[type=submit]')).toHaveText('전송');
    const name = panel.getByRole('textbox', { name: '대화방명 편집', exact: true });
    await name.fill('Synthetic changed title'); await panel.locator('.drawer-edit-row button[type=submit]').click();
    await expect(panel.locator('.drawer-tab-content')).toContainText('Synthetic save rejected');
    await assertStable('save failure');
    checks.push({ width, docked, progressInHeaderOnly: true, submittingQueuedRunningRetryingTabsStable: true, saveFailurePreservedInScrollablePanel: true });
    await page.close();
  }
  assert.deepEqual(errors, []);
  const result = { status: 'PASS', checks, pageErrors: errors, syntheticGenerationRequests: writes.length, liveApiRequestsForwarded: 0, paidCalls: 0 };
  if (out) writeFileSync(`${out}/result.json`, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} catch (error) {
  if (out) writeFileSync(`${out}/result.json`, JSON.stringify({ status: 'FAIL', checks, error: error.message, liveApiRequestsForwarded: 0 }, null, 2));
  throw error;
} finally { await browser.close(); if (server) await server.close(); }
