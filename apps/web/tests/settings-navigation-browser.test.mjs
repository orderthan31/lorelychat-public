import { testViteCacheDir } from './isolated-vite-cache.mjs';
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync } from 'node:fs';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { chromium, expect } from '@playwright/test';
const dir = process.env.SETTINGS_ARTIFACT_DIR;
if (dir) mkdirSync(dir, { recursive: true });
const messages = JSON.parse(readFileSync('src/i18n/messages.ko.json', 'utf8'));
const t = key => messages[key] || key;
const server = await createServer({ cacheDir: testViteCacheDir, configFile: false, envFile: false, plugins: [{ name: 'settings-navigation-fixture', configureServer(s) { s.middlewares.use(async (req, res, next) => {
  if (!['/settings','/settings/models','/settings/providers','/settings/model-catalog','/settings/lore-agent','/model-settings'].includes(req.url?.split('?')[0])) return next();
  res.setHeader('Content-Type','text/html'); res.end(await s.transformIndexHtml(req.url, '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/settings-navigation.fixture.tsx"></script></body></html>'));
}); } },react(),tailwindcss()], server: { host: '127.0.0.1', port: 0, hmr: false, watch: null }, logLevel: 'error' });
let browser;
try {
 await server.listen(); browser = await chromium.launch({ headless: true });
 const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
 for (const [width,height] of [[320,740],[390,844],[1024,768],[1440,900]]) {
  const page = await browser.newPage({ viewport: { width,height } }); const errors=[],writes=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>localStorage.setItem('lorechat-language','ko'));
  await page.route('**/*', route=> { const req=route.request(),url=new URL(req.url());
   if(url.origin!==origin) return route.abort();
   if(url.pathname.startsWith('/api/')) { if(req.method()!=='GET') writes.push(url.pathname); return route.fulfill({json: []}); }
   return route.continue();
  });
  await page.goto(`${origin}/settings`); await page.evaluate(()=>window.__sameDocument=true);
  for (const [key,path,section] of [['settings.defaultModels','models','runtime'],['settings.providers','providers','providers'],['settings.modelCatalog','model-catalog','models']]) {
   await page.locator('[data-page=settings-hub]').getByRole('button',{name:t(key),exact:true}).click();
   await expect(page).toHaveURL(`${origin}/settings/${path}`);
   await expect(page.locator(`[data-settings-section=${section}]`)).toBeVisible();
   await expect(page.locator('[role=tab]')).toHaveCount(0);
   await expect(page.locator('[data-route-title]')).toHaveText(t(key));
   assert.equal(await page.evaluate(()=>window.__sameDocument),true);
   if(dir) await page.screenshot({path:`${dir}/${path}-${width}x${height}.png`});
   await page.reload(); await expect(page.locator(`[data-settings-section=${section}]`)).toBeVisible();
   await page.goBack(); await expect(page.locator('[data-page=settings-hub]')).toBeVisible();
   await page.evaluate(()=>window.__sameDocument=true);
  }
  await page.goto(`${origin}/model-settings`); await expect(page.locator('[data-settings-section=runtime]')).toBeVisible();
  await page.goto(`${origin}/settings/lore-agent`);
  const switches=page.getByRole('switch'); await expect(switches).toHaveCount(2);
  for (const control of await switches.all()) {
   const hitHeight = await control.evaluate(element => element.closest('label').getBoundingClientRect().height);
   assert.ok(hitHeight >= 44, 'both settings switches retain a usable touch target');
  }
  await expect(page.getByLabel(t('agent.llm'),{exact:true})).toHaveValue('chat');
  await expect(page.getByLabel(t('agent.imageModel'),{exact:true})).toHaveValue('image-model');
  await switches.first().focus(); await page.keyboard.press('Space'); await expect(switches.first()).not.toBeChecked();
  await page.keyboard.press('Space'); await expect(switches.first()).toBeChecked();
  const scope=page.getByRole('checkbox',{name:'lore:read',exact:true}); await expect(scope).toBeDisabled(); await expect(scope).toBeChecked();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(writes,[],'navigation and draft toggles must not mutate settings or refresh providers');
  assert.deepEqual(errors,[]);
  if(dir) await page.screenshot({path:`${dir}/agent-${width}x${height}.png`,fullPage:true});
  console.log(`PASS settings destinations/deeplinks/reload/back/no writes/keyboard switch semantics ${width}x${height}`);
  await page.close();
 }
} finally { await browser?.close(); await server.close(); }
