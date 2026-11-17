import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, mkdirSync, readFileSync } from 'node:fs';
const messages = JSON.parse(readFileSync('src/i18n/messages.ko.json', 'utf8'));
const t = key => messages[key] || key;
import { createServer } from 'vite';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { chromium, expect } from '@playwright/test';
const artifactDir = process.env.SETTINGS_ARTIFACT_DIR;
if (artifactDir) mkdirSync(artifactDir, { recursive: true });
const external = process.env.SETTINGS_BASE_URL;
const cacheDir = mkdtempSync(join(tmpdir(), 'integration-repair-vite-'));
const server = external ? null : await createServer({ cacheDir, configFile: false, envFile: false, plugins: [{ name: 'settings-fixture', configureServer(server) { server.middlewares.use('/settings-fixture', async (_req, res) => { res.setHeader('Content-Type', 'text/html'); res.end(await server.transformIndexHtml('/settings-fixture', '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/ux-settings.fixture.tsx"></script></body></html>')); }); } }, react(), tailwindcss()], server: { host: '127.0.0.1', port: 0, hmr: false, watch: null }, logLevel: 'error' });
let browser;
try {
  if (server) await server.listen(); browser = await chromium.launch({ headless: true });
  const origin = external || `http://127.0.0.1:${server.httpServer.address().port}`;
  for (const [width, height] of [[390,844],[820,1180],[1024,768],[844,390],[320,740]]) {
    const page = await browser.newPage({ viewport: { width, height } });
    const errors = [], writes = [];
    let failManual = true;
    const runtimeOption = { key: 'chat', label: 'Synthetic chat', provider_account_id: 'account', provider_account_alias: 'Synthetic account' };
    let runtimeValue = { model_key: 'chat', compression_model_key: 'chat', fallback_model_key: null, compression_fallback_model_key: null, thinking_level: null, compression_strategy: 'quality', decision_enabled: false, options: [runtimeOption], compression_options: [runtimeOption] };
    let accounts = [{ id: 'account', alias: 'Synthetic account', provider_type: 'google', configured: true, api_key_status: 'set' }];
    let options = Array.from({ length: 15 }, (_, i) => ({ id: `model-${i}`, key: `model-${i}`, model: `synthetic-${i}`, display_label: `Synthetic ${i}`, label: `Synthetic ${i}`, provider_type: 'google', provider_account_id: 'account', enabled: true, supports_chat: true, supports_compression: true, source: 'fetched' }));
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('lorechat-language', 'ko'));
    await page.route('**/*', route => {
      const req = route.request(), url = new URL(req.url());
      if (url.origin !== origin) return route.abort();
      if (url.pathname.startsWith('/api/')) {
        if (req.method() !== 'GET') writes.push({ path: url.pathname, body: req.postDataJSON() });
        if (url.pathname === '/api/model-provider-accounts') {
          if (req.method() === 'POST') { const { api_key, ...data } = req.postDataJSON(); const account = { ...data, id: 'new-account', configured: true, api_key_status: 'set' }; accounts.push(account); return route.fulfill({ json: account }); }
          return route.fulfill({ json: accounts });
        }
        if (url.pathname.endsWith('/manual')) { if (failManual) { failManual = false; return route.fulfill({ status: 400, json: { detail: 'Synthetic manual failure' } }); } const data = req.postDataJSON(); const option = { ...data, id: 'manual', key: 'manual', display_label: data.label, enabled: true, source: 'manual', provider_type: 'google' }; options.unshift(option); return route.fulfill({ json: option }); }
        if (url.pathname === '/api/model-options') return route.fulfill({ json: options });
        if (url.pathname.startsWith('/api/runtime-settings')) { if (req.method() !== 'GET') runtimeValue = { ...runtimeValue, ...req.postDataJSON() }; return route.fulfill({ json: runtimeValue }); }
        if (req.method() === 'GET') return route.fulfill({ json: url.pathname === '/api/health' || url.pathname === '/api/ready' ? { status: 'ok' } : [] });
        return route.fulfill({ status: 404, json: { detail: 'Synthetic fixture only' } });
      }
      if (req.method() !== 'GET') return route.abort();
      return route.continue();
    });
    await page.goto(`${origin}/${external ? 'settings/providers' : 'settings-fixture#providers'}`);
    await expect(page.locator('[data-provider-account-card]')).toHaveCount(1);
    const providerAdd = page.locator('details').filter({ has: page.locator('summary').filter({ hasText: t('Provider 계정 추가') }) });
    const top = await providerAdd.locator('summary').boundingBox();
    assert.ok(top.y >= 0 && top.height >= 44 && top.y + top.height <= height, `provider add must start in viewport ${width}x${height}: ${JSON.stringify(top)}`);
    if (artifactDir) await page.screenshot({ path: `${artifactDir}/providers-${width}x${height}.png` });
    await providerAdd.locator('summary').click();
    await providerAdd.getByLabel(t('별칭'), { exact: true }).fill('Created synthetic account');
    await providerAdd.getByLabel(t('API Key'), { exact: true }).fill('SYNTHETIC-ONLY');
    // Resizing must not replace the focused draft editor.
    await providerAdd.getByLabel(t('별칭'), { exact: true }).focus();
    await page.setViewportSize({ width: width + 1, height });
    await expect(providerAdd.getByLabel(t('별칭'), { exact: true })).toBeFocused();
    await page.setViewportSize({ width, height });
    await providerAdd.getByRole('button', { name: t('Provider 저장'), exact: true }).click();
    await expect(page.locator('[data-provider-account-card]').filter({ hasText: 'Created synthetic account' })).toHaveCount(1);
    await expect(providerAdd.getByLabel(t('API Key'), { exact: true })).toHaveValue('');
    assert.equal(writes[0].body.alias, 'Created synthetic account');
    if (external) await page.goto(`${origin}/settings/model-catalog`); else await page.evaluate(() => { location.hash = 'models'; });
    await page.evaluate(() => scrollTo(0,0));
    const filterToggle = page.locator('[data-model-filter-toggle]');
    if (await filterToggle.isVisible()) await filterToggle.click();
    await page.locator('[data-model-filter-fields] select').first().selectOption('');
    await expect(page.locator('[data-model-row]')).toHaveCount(10);
    await page.evaluate(() => scrollTo(0,0));
    const manual = page.locator('details').filter({ has: page.locator('summary').filter({ hasText: t('수동 추가') }) });
    const manualTop = await manual.locator('summary').boundingBox();
    assert.ok(manualTop.y >= 0 && manualTop.height >= 44 && manualTop.y + manualTop.height <= height, `manual add starts in viewport ${width}`);
    if (artifactDir) await page.screenshot({ path: `${artifactDir}/models-${width}x${height}.png` });
    await manual.locator('summary').click();
    await manual.getByLabel(t('모델 ID'), { exact: true }).fill('synthetic-manual');
    await manual.getByLabel(t('표시명'), { exact: true }).fill('Created synthetic model');
    await manual.getByRole('button', { name: t('수동 추가'), exact: true }).click();
    await expect(page.locator('[data-page=model-settings]').getByRole('status').filter({ hasText: 'Synthetic manual failure' })).toContainText('Synthetic manual failure');
    await expect(manual.getByLabel(t('모델 ID'), { exact: true })).toHaveValue('synthetic-manual');
    await manual.getByRole('button', { name: t('수동 추가'), exact: true }).click();
    await expect(page.locator('[data-model-row]').filter({ hasText: 'Created synthetic model' })).toHaveCount(1);
    const payload = writes.at(-1).body;
    assert.equal(payload.model, 'synthetic-manual'); assert.equal(payload.supports_chat, true); assert.equal('input_modalities' in payload, false);
    if (external) await page.goto(`${origin}/settings/models`); else await page.evaluate(() => { location.hash = 'runtime'; });
    const runtime = page.locator('[data-settings-section=runtime]');
    const compression = runtime.locator('label').filter({ has: page.getByText(t('압축 방식'), { exact: true }) }).locator('select');
    await expect(compression).toHaveValue('quality');
    await expect(compression.locator('option:checked')).toContainText('≈3× LLM');
    await compression.selectOption('fast');
    await expect(compression.locator('option:checked')).toContainText('≈1× LLM');
    await compression.selectOption('quality');
    await runtime.locator('label').filter({ has: page.getByText(t('대화 길이'), { exact: true }) }).locator('select').selectOption('short');
    await runtime.getByRole('button', { name: t('기본값 저장'), exact: true }).click();
    if (external) await expect.poll(() => writes.filter(w => w.path.startsWith('/api/runtime-settings')).length).toBe(1);
    const saved = external ? runtimeValue : JSON.parse(await page.locator('[data-saved]').textContent());
    assert.equal(saved.model_key, 'chat'); assert.equal(saved.compression_model_key, 'chat'); assert.equal(saved.thinking_level, null); assert.equal(saved.compression_fallback_model_key, null); assert.equal(saved.compression_strategy, 'quality'); assert.equal(saved.response_length_preset, 'short');
    const detail = runtime.locator('.runtime-help');
    await detail.locator('summary').focus(); await page.keyboard.press('Enter');
    await expect(detail).toHaveAttribute('open', '');
    await expect(detail).toContainText(t('보통 1회 LLM 호출로 비용과 대기 시간을 줄입니다. 시간순서나 열린 사건·복선을 놓칠 수 있습니다.'));
    await detail.locator('summary').click();
    await page.evaluate(() => scrollTo(0,0));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    if (artifactDir) await page.screenshot({ path: `${artifactDir}/settings-${width}x${height}.png`, fullPage: true });
    assert.deepEqual(errors, []);
    console.log(`PASS settings actions/readback/value preservation ${width}x${height}; synthetic writes=${writes.length}; no page errors`);
    await page.close();
  }
} finally { await browser?.close(); await server?.close(); rmSync(cacheDir, { recursive: true, force: true }); }
