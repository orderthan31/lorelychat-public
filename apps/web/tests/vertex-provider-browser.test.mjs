import assert from 'node:assert/strict';
import { mkdirSync, readFileSync } from 'node:fs';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { chromium, expect } from '@playwright/test';
const artifactDir = process.env.VERTEX_ARTIFACT_DIR;
if (artifactDir) mkdirSync(artifactDir, { recursive: true });
const plugin = { name: 'vertex-fixture', configureServer(server) {
  server.middlewares.use('/vertex-fixture', async (_req, res) => {
    res.setHeader('Content-Type', 'text/html');
    res.end(await server.transformIndexHtml('/vertex-fixture', '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/vertex-provider.fixture.tsx"></script></body></html>'));
  });
} };
const server = await createServer({ configFile: false, envFile: false, plugins: [plugin, react(), tailwindcss()], server: { host: '127.0.0.1', port: 0, hmr: false, watch: null }, logLevel: 'error' });
let browser;
try {
  await server.listen(); browser = await chromium.launch({ headless: true });
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  for (const locale of ['ko', 'en', 'ja']) {
    const messages = JSON.parse(readFileSync(`src/i18n/messages.${locale}.json`, 'utf8'));
    const t = key => messages[key] || key;
    const page = await browser.newPage({ viewport: { width: locale === 'en' ? 360 : 390, height: 844 } });
    const errors = [], writes = [];
    let account = { id: 'synthetic-vertex', provider_type: 'vertex_ai', alias: 'Synthetic Vertex', enabled: true, configured: true, api_key_status: 'set', api_key_hint: 'MUST-NOT-RENDER-HINT', vertex_project: 'synthetic-project', vertex_location: 'global', vertex_auth_mode: 'service_account', last_test_status: 'success' };
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(locale => localStorage.setItem('lorechat-language', locale), locale);
    await page.route('**/*', route => {
      const req = route.request(), url = new URL(req.url());
      if (url.origin !== origin) return route.abort();
      if (url.pathname.startsWith('/api/')) {
        if (req.method() !== 'GET') writes.push({ path: url.pathname, body: req.postDataJSON() });
        if (url.pathname.endsWith('/test')) return route.fulfill({ json: { status: account.configured ? 'success' : 'failed', message: 'Synthetic configuration check only' } });
        if (url.pathname === '/api/model-provider-accounts/synthetic-vertex' && req.method() === 'PATCH') {
          const payload = req.postDataJSON();
          account = { ...account, configured: payload.vertex_auth_mode === 'adc' || (!payload.clear_api_key && account.configured), ...Object.fromEntries(Object.entries(payload).filter(([key]) => !['api_key', 'clear_api_key'].includes(key))), api_key_status: payload.clear_api_key ? 'missing' : account.api_key_status };
          return route.fulfill({ json: account });
        }
        if (url.pathname === '/api/model-provider-accounts') return route.fulfill({ json: req.method() === 'POST' ? { ...account, id: 'synthetic-created' } : [account] });
        if (url.pathname === '/api/model-options') return route.fulfill({ json: [] });
        if (url.pathname.endsWith('/manual')) return route.fulfill({ json: {} });
        return route.fulfill({ status: 404, json: { detail: 'Synthetic fixture only' } });
      }
      return route.continue();
    });
    await page.goto(`${origin}/vertex-fixture#providers`);
    const card = page.locator('[data-provider-account-card]');
    await expect(card).toContainText('Synthetic Vertex');
    assert.ok(!(await page.locator('body').innerText()).includes('MUST-NOT-RENDER-HINT'));
    await card.locator('summary').click();
    const check = process.env.VERTEX_RED_CASE;
    if (!check || check === 'sync') await expect(card.locator('[data-provider-refresh-account]')).toHaveCount(0);
    if (!check || check === 'adc') {
      await card.locator('label').filter({ has: page.getByText(t('인증 방식'), { exact: true }) }).locator('select').selectOption('adc');
      await expect(card).toContainText(t('ADC로 저장하면 기존 JSON이 제거되고 서버에 구성된 ADC를 사용합니다.'));
      if (artifactDir) await card.screenshot({ path: `${artifactDir}/vertex-adc-${locale}.png` });
      await card.locator('label').filter({ has: page.getByText(t('인증 방식'), { exact: true }) }).locator('select').selectOption('service_account');
    }
    if (!check || check === 'pending') {
      await page.evaluate(() => {
        const original = File.prototype.text;
        File.prototype.text = function () { return new Promise((resolve, reject) => {
          window.releaseVertexRead = () => original.call(this).then(resolve, reject);
        }); };
        window.restoreVertexRead = () => { File.prototype.text = original; };
      });
      await card.locator('input[type=file]').setInputFiles({ name: 'delayed.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({ type: 'service_account', client_email: 'delayed@example.invalid', private_key: 'SYNTHETIC-DELAYED' })) });
      await expect(card.getByRole('button', { name: t('Vertex 설정 저장'), exact: true })).toBeDisabled();
      await expect(card.getByRole('button', { name: t('저장된 JSON 제거'), exact: true })).toBeDisabled();
      // Unmount the owner before completion, then ensure the next visit has no late secret.
      await page.locator('[role=tab][id$="-trigger-models"]').click();
      await page.evaluate(async () => { await window.releaseVertexRead(); window.restoreVertexRead(); });
      await page.locator('[role=tab][id$="-trigger-providers"]').click();
      await card.locator('summary').click();
      await expect(card).toContainText(t('파일 내용과 이름은 표시하지 않습니다. 기존 인증 정보는 새 파일을 저장할 때만 교체됩니다.'));
      assert.equal(writes.length, 0);
    }
    if (check) { await page.close(); break; }
    await card.getByLabel(t('Google Cloud 프로젝트 ID')).fill('changed-project');
    await card.getByRole('button', { name: t('Vertex 설정 저장'), exact: true }).click();
    await expect(card.getByRole('button', { name: t('Vertex 설정 저장'), exact: true })).toBeEnabled();
    assert.equal(writes.at(-1).body.vertex_project, 'changed-project');
    assert.equal('api_key' in writes.at(-1).body, false);
    await card.locator('input[type=file]').setInputFiles({ name: 'invalid.json', mimeType: 'application/json', buffer: Buffer.from('{}') });
    await expect(card.getByRole('alert')).toContainText(t('유효한 서비스 계정 JSON 파일을 선택하세요. 최대 32KB입니다.'));
    const fixture = JSON.stringify({ type: 'service_account', client_email: 'fixture@example.invalid', private_key: 'SYNTHETIC-SECRET-NOT-REAL' });
    await card.locator('input[type=file]').setInputFiles({ name: 'too-large.json', mimeType: 'application/json', buffer: Buffer.from(fixture.padEnd(32769)) });
    await expect(card.getByRole('alert')).toContainText(t('유효한 서비스 계정 JSON 파일을 선택하세요. 최대 32KB입니다.'));
    await card.locator('input[type=file]').setInputFiles({ name: 'boundary.json', mimeType: 'application/json', buffer: Buffer.from(fixture.padEnd(32768)) });
    await expect(card).toContainText(t('새 인증 파일이 준비되었습니다. 저장해야 적용됩니다.'));
    await expect(card.getByRole('alert')).toHaveCount(0);
    await card.locator('input[type=file]').setInputFiles({ name: 'PRIVATE-FILENAME.json', mimeType: 'application/json', buffer: Buffer.from(fixture) });
    await expect(card).toContainText(t('새 인증 파일이 준비되었습니다. 저장해야 적용됩니다.'));
    assert.ok(!(await card.innerText()).includes('PRIVATE-FILENAME'));
    assert.ok(!(await card.innerText()).includes('SYNTHETIC-SECRET'));
    if (artifactDir) await card.screenshot({ path: `${artifactDir}/vertex-service-account-${locale}.png` });
    await card.getByRole('button', { name: t('Vertex 설정 저장'), exact: true }).click();
    await expect(card).toContainText(t('파일 내용과 이름은 표시하지 않습니다. 기존 인증 정보는 새 파일을 저장할 때만 교체됩니다.'));
    assert.equal(writes.at(-1).body.api_key, fixture);
    await card.getByRole('button', { name: t('저장된 JSON 제거'), exact: true }).click();
    await expect(page.locator('.swal2-html-container')).toContainText(t('저장된 서비스 계정 JSON을 제거합니다. 이 계정의 모델이 비활성화됩니다. 계속할까요?'));
    await page.locator('.danger-confirm-button').click();
    await expect(card.getByRole('button', { name: t('저장된 JSON 제거'), exact: true })).toBeDisabled();
    assert.deepEqual(writes.at(-1).body, { clear_api_key: true });
    await card.getByRole('button', { name: t('설정 확인 (호출 없음)'), exact: true }).click();
    await expect(page.locator('header').first()).toContainText(t('프로젝트, 위치 및 인증 정보를 확인하세요.'));
    await card.locator('label').filter({ has: page.getByText(t('인증 방식'), { exact: true }) }).locator('select').selectOption('adc');
    await expect(card.locator('input[type=file]')).toHaveCount(0);
    await card.getByRole('button', { name: t('Vertex 설정 저장'), exact: true }).click();
    await expect(card.getByRole('button', { name: t('Vertex 설정 저장'), exact: true })).toBeEnabled();
    assert.equal(writes.at(-1).body.vertex_auth_mode, 'adc');
    assert.equal('api_key' in writes.at(-1).body, false);
    await card.getByRole('button', { name: t('설정 확인 (호출 없음)'), exact: true }).click();
    await expect(page.locator('header').first()).toContainText(t('설정 확인 완료. 실제 인증 및 모델 호출은 검증하지 않았습니다.'));
    const create = page.locator('details').filter({ has: page.locator('summary').filter({ hasText: t('Provider 계정 추가') }) });
    await create.locator('summary').click();
    await create.locator('label').filter({ has: page.getByText(t('Provider'), { exact: true }) }).locator('select').selectOption('vertex_ai');
    await expect(create.locator('input[type=password]')).toHaveCount(0);
    await expect(create.getByRole('button', { name: t('Provider 저장'), exact: true })).toBeDisabled();
    await create.getByLabel(t('별칭'), { exact: true }).fill('Synthetic new Vertex');
    await create.getByLabel(t('Google Cloud 프로젝트 ID')).fill('synthetic-create');
    await create.locator('label').filter({ has: page.getByText(t('인증 방식'), { exact: true }) }).locator('select').selectOption('adc');
    await expect(create.getByRole('button', { name: t('Provider 저장'), exact: true })).toBeEnabled();
    await create.scrollIntoViewIfNeeded();
    if (artifactDir) await page.screenshot({ path: `${artifactDir}/vertex-mobile-${locale}.png`, fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, JSON.stringify(await page.locator('body *').evaluateAll(nodes => nodes.filter(n => n.getBoundingClientRect().right > innerWidth).slice(0, 12).map(n => ({ tag: n.tagName, cls: n.className, width: n.getBoundingClientRect().width })))));
    await create.getByRole('button', { name: t('Provider 저장'), exact: true }).click();
    await expect(create.getByLabel(t('별칭'), { exact: true })).toHaveValue('');
    const createPayload = writes.find(write => write.path === '/api/model-provider-accounts').body;
    assert.equal(createPayload.vertex_auth_mode, 'adc');
    assert.equal('api_key' in createPayload, false); assert.equal('base_url' in createPayload, false);
    await page.locator('[role=tab][id$="-trigger-models"]').click();
    const manual = page.locator('details').filter({ has: page.locator('summary').filter({ hasText: t('수동 추가') }) });
    await manual.locator('summary').click();
    await manual.locator('label').filter({ has: page.getByText(t('Provider 계정'), { exact: true }) }).locator('select').selectOption('synthetic-vertex');
    await expect(manual.locator('label').filter({ has: page.getByText(t('용도'), { exact: true }) }).locator('select').locator('option')).toHaveCount(1);
    await expect(manual.getByLabel(t('모델 ID'), { exact: true })).toBeEnabled();
    await manual.getByLabel(t('모델 ID'), { exact: true }).fill('gemini-synthetic-fixture');
    await manual.getByRole('button', { name: t('수동 추가'), exact: true }).click();
    await expect(manual.getByLabel(t('모델 ID'), { exact: true })).toHaveValue('');
    assert.equal(writes.at(-1).path, '/api/model-options/manual');
    assert.equal(writes.at(-1).body.model_family, 'gemini');
    assert.equal(writes.at(-1).body.supports_chat, true);
    assert.equal(writes.at(-1).body.supports_compression, true);
    assert.equal(writes.at(-1).body.supports_decision, false);
    assert.equal(writes.at(-1).body.supports_tts, false);
    assert.equal(writes.some(write => write.path.endsWith('/sync-models')), false);
    assert.deepEqual(errors, []);
    console.log(`PASS ${locale}: mobile form, private upload/replacement/clear, omitted secret, ADC create, configuration-only check and manual capability restrictions; ${writes.length} synthetic writes`);
    await page.close();
  }
} finally { await browser?.close(); await server.close(); }
