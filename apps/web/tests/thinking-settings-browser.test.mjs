import assert from 'node:assert/strict';
import { mkdirSync, readFileSync } from 'node:fs';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { chromium, expect } from '@playwright/test';
import { normalizeRuntimeSettingUpdatePayload } from '../src/utils/runtimeSettings.ts';
import ts from 'typescript';
import vm from 'node:vm';

// Exercise the actual field-update owner composed with the real PATCH
// serializer: missing, null and explicit preferences must remain distinct.
const fieldSource = ts.createSourceFile('RuntimeSettings.tsx', readFileSync('src/components/organisms/RuntimeSettings.tsx', 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let updateExpression;
function findUpdate(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(fieldSource) === 'update' && node.initializer) updateExpression = node.initializer.getText(fieldSource);
  ts.forEachChild(node, findUpdate);
}
findUpdate(fieldSource);
assert.ok(updateExpression, 'production runtime field update exists');
const fieldDefaults = { response_length_preset: 'medium', thinking_level: 'low' };
const updateCode = ts.transpileModule(`globalThis.applyPatch = ${updateExpression};`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
for (const scope of ['global', 'room']) {
  for (const preference of [undefined, null, 'low', 'medium', 'high', 'xhigh', 'max']) {
    const value = { response_length_preset: 'long', compression_strategy: 'fast', ...(preference === undefined ? {} : { thinking_level: preference }) };
    let submitted;
    const context = vm.createContext({ DEFAULT_RUNTIME_SETTING: fieldDefaults, scope, value, onChange: updated => { submitted = normalizeRuntimeSettingUpdatePayload(updated, fieldDefaults); } });
    vm.runInContext(updateCode, context);
    context.applyPatch({ safety_preset: 'high' });
    assert.equal('thinking_level' in submitted, preference !== undefined, `${scope}: unrelated edit preserves omission`);
    assert.equal(submitted.thinking_level, preference, `${scope}: unrelated edit preserves preference`);
    assert.equal(submitted.response_length_preset, 'long');
    assert.equal(submitted.compression_strategy, 'fast');
    context.applyPatch({ thinking_level: null });
    assert.equal(submitted.thinking_level, null, `${scope}: explicit reset remains null`);
    context.applyPatch({ thinking_level: 'xhigh' });
    assert.equal(submitted.thinking_level, 'xhigh');
  }
}

const defaults = { response_length_preset: 'medium', thinking_level: 'low' };
assert.equal('thinking_level' in normalizeRuntimeSettingUpdatePayload({}, defaults), false);
for (const level of [null, 'low', 'medium', 'high', 'xhigh', 'max']) {
  const before = { model_key: 'chat', fallback_model_key: 'backup', compression_model_key: 'compress', compression_fallback_model_key: 'compress-backup', default_tts_model_option_key: 'tts', decision_enabled: false, decision_model_option_key: 'decision', safety_preset: 'high', response_length_preset: 'long', compression_strategy: 'fast' };
  assert.deepEqual(normalizeRuntimeSettingUpdatePayload({ ...before, thinking_level: level, effective_thinking_level: 'max' }, defaults), { ...before, thinking_level: level });
}
const artifactDir = process.env.THINKING_ARTIFACT_DIR;
if (artifactDir) mkdirSync(artifactDir, { recursive: true });
const plugin = { name: 'thinking-fixture', configureServer(server) {
  server.middlewares.use('/thinking-fixture', async (_req, res) => {
    res.setHeader('Content-Type', 'text/html');
    res.end(await server.transformIndexHtml('/thinking-fixture', '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/thinking-settings.fixture.tsx"></script></body></html>'));
  });
} };
const server = await createServer({ configFile: false, envFile: false, plugins: [plugin, react(), tailwindcss()], server: { host: '127.0.0.1', port: 0, hmr: false, watch: null }, logLevel: 'error' });
let browser;
try {
  await server.listen(); browser = await chromium.launch({ headless: true });
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  for (const locale of ['ko', 'en', 'ja']) {
    const translations = JSON.parse(readFileSync(`src/i18n/messages.${locale}.json`, 'utf8'));
    const t = key => translations[key] || key;
    for (const key of ['추론 깊이', '공통 설정 상속', '추론 낮음', '추론 보통', '추론 높음', '추론 매우 높음', '추론 최대']) assert.ok(translations[key], `missing ${locale}: ${key}`);
    const page = await browser.newPage({ viewport: { width: 360, height: 844 } });
    const errors = [], writes = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(locale => localStorage.setItem('lorechat-language', locale), locale);
    const option = { key: 'synthetic-model', label: 'Synthetic model', provider_account_id: 'synthetic-provider', provider_account_alias: 'Synthetic provider' };
    const base = { model_key: option.key, fallback_model_key: null, compression_model_key: option.key, compression_fallback_model_key: null, default_tts_model_option_key: null, decision_enabled: false, decision_model_option_key: null, safety_preset: 'medium', response_length_preset: 'long', compression_strategy: 'fast', options: [option], compression_options: [option] };
    let common = { ...base, thinking_level: 'low' }, room = { ...base, thinking_level: null };
    await page.route('**/*', route => {
      const req = route.request(), url = new URL(req.url());
      if (url.origin !== origin) return route.abort();
      if (!url.pathname.startsWith('/api/')) return route.continue();
      const global = url.pathname === '/api/runtime-settings/default';
      if (global || url.pathname === '/api/runtime-settings/conversations/synthetic-room') {
        if (req.method() === 'PATCH') {
          const payload = req.postDataJSON(); writes.push({ global, payload });
          assert.equal('effective_thinking_level' in payload, false);
          const { thinking_level, ...rest } = payload;
          const { options, compression_options, ...expected } = base;
          assert.deepEqual(rest, expected, 'unrelated fields preserved');
          if (global) common = { ...common, ...payload }; else room = { ...room, ...payload };
        } else assert.equal(req.method(), 'GET');
        const record = global ? common : room;
        return route.fulfill({ json: { ...record, effective_thinking_level: record.thinking_level ?? common.thinking_level } });
      }
      if (req.method() !== 'GET') throw Error(`Unexpected synthetic mutation ${req.method()} ${url.pathname}`);
      return route.fulfill({ json: [] });
    });
    const select = () => page.locator('label').filter({ has: page.getByText(t('추론 깊이'), { exact: true }) }).locator('select');
    const overflow = async () => assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${locale} horizontal overflow`);
    await page.goto(`${origin}/thinking-fixture#runtime`);
    await expect(select()).toHaveValue('low');
    await expect(select().locator('option')).toHaveCount(5);
    await select().selectOption('high');
    await page.getByRole('button', { name: t('기본값 저장'), exact: true }).click();
    await expect.poll(() => common.thinking_level).toBe('high');
    await page.reload(); await expect(select()).toHaveValue('high');
    await select().scrollIntoViewIfNeeded(); await overflow();
    if (artifactDir) await page.screenshot({ path: `${artifactDir}/thinking-global-${locale}.png`, fullPage: true });
    await page.goto(`${origin}/thinking-fixture?room`);
    const open = () => page.getByRole('button', { name: t('채팅 설정'), exact: true }).click();
    await open(); await expect(select()).toHaveValue('');
    await expect(select().locator('option').first()).toHaveText(`${t('공통 설정 상속')} · ${t('추론 높음')}`);
    await expect(select().locator('option')).toHaveCount(6);
    for (const level of ['medium', 'xhigh', 'max']) { await select().selectOption(level); await expect(select()).toHaveValue(level); }
    await page.getByRole('button', { name: t('이 방 설정 저장'), exact: true }).click();
    await expect.poll(() => room.thinking_level).toBe('max');
    await open(); await expect(select()).toHaveValue('max');
    // A subsequent common update must not pin an inherited room to the old level.
    common = { ...common, thinking_level: 'medium' };
    await page.reload(); await open();
    await expect(select().locator('option').first()).toHaveText(`${t('공통 설정 상속')} · ${t('추론 보통')}`);
    await select().selectOption('');
    await page.getByRole('button', { name: t('이 방 설정 저장'), exact: true }).click();
    await expect.poll(() => room.thinking_level).toBe(null);
    await page.reload(); await open(); await expect(select()).toHaveValue('');
    await expect(select().locator('option').first()).toHaveText(`${t('공통 설정 상속')} · ${t('추론 보통')}`);
    await select().scrollIntoViewIfNeeded(); await overflow();
    if (artifactDir) await page.screenshot({ path: `${artifactDir}/thinking-room-${locale}.png`, fullPage: true });
    assert.equal(writes.length, 3);
    assert.deepEqual(writes.map(w => w.payload.thinking_level), ['high', 'max', null]);
    assert.deepEqual(errors, []);
    console.log(`${locale}: global low → high/reopen; room inheritance → max → null/reopen; current common medium; 360px no overflow; 3 intercepted writes; zero page errors`);
    await page.close();
  }
} finally { await browser?.close(); await server.close(); }
console.log('Thinking settings payload and production-control browser contracts passed');
