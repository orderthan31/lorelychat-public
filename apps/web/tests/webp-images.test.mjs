import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium, expect } from '@playwright/test';
import ts from 'typescript';
import { createElement } from 'react';
import { renderToString } from 'react-dom/server';

// This shard runs against an isolated source copy, never the configured app/API.
assert.ok(!process.env.VITE_API_BASE_URL, 'use default synthetic API only');
const raster = readFileSync('public/brand/lorely-app-icon-light.webp');
assert.equal(raster.toString('ascii', 0, 4), 'RIFF');
assert.equal(raster.toString('ascii', 8, 12), 'WEBP');
const plugin = { name: 'webp-fixture', configureServer(server) {
  server.middlewares.use('/image-test', async (_req, res) => {
    res.setHeader('Content-Type', 'text/html');
    res.end(await server.transformIndexHtml('/image-test', '<html><body><div id="root"></div><script type="module" src="/tests/webp-images.fixture.tsx"></script></body></html>'));
  });
} };
const server = await createServer({ configFile: false, envFile: false, plugins: [plugin, react()], server: { host: '127.0.0.1', port: 0, hmr: false, watch: null }, logLevel: 'error' });
let browser;
let passed = 0;
const check = async (name, fn) => { await fn(); console.log(`PASS ${++passed}: ${name}`); };
try {
  await server.listen(); browser = await chromium.launch({ headless: true });
  await check('production helpers honor relative/absolute/trailing API bases without changing generic exports', async () => {
    for (const base of ['/api', '/api/', 'https://api.invalid/v1', 'https://api.invalid/v1/']) {
      const source = readFileSync('src/utils/assets.ts', 'utf8').replace("import { API_BASE } from '../constants/domain';", `const API_BASE = ${JSON.stringify(base)};`);
      const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
      const assets = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
      const normalized = base.replace(/\/$/, '');
      const display = `${normalized}/images/render.webp?source=%2Fuploads%2Ftest.png&variant=full`;
      assert.equal(assets.imageUrlFor('/uploads/test.png', 'full'), display);
      assert.equal(assets.imageUrlFor(display), display);
      assert.equal(assets.imageUrlFor('/images/cache/key/full.webp'), `${normalized}/images/cache/key/full.webp`);
      assert.equal(assets.assetUrlFor('/uploads/audio.mp3'), `${base}/uploads/audio.mp3`);
      assert.equal(assets.assetUrlFor('https://external.invalid/export.png'), 'https://external.invalid/export.png');
      assert.equal(assets.imageUrlFor('/uploads/vector.svg'), `${normalized}/images/render.webp?source=%2Fuploads%2Fvector.svg&variant=thumbnail`);
      assert.equal(assets.imageUrlFor('https://external.invalid/uploads/private.png'), 'https://external.invalid/uploads/private.png');
      assert.equal(assets.imageUrlFor('http://127.0.0.1:8123/uploads/test.png', 'full'), display);
      if (base.startsWith('https:')) assert.equal(assets.imageUrlFor('https://api.invalid/uploads/test.png', 'full'), display);
    }
  });
  await check('SSR renders synchronous local WebP and never embeds unprepared remote/data rasters', async () => {
    const { WebpImage } = await server.ssrLoadModule('/src/components/atoms/WebpImage.tsx');
    const local = renderToString(createElement(WebpImage, { src: '/uploads/ssr.png', alt: 'SSR', variant: 'full' }));
    assert.match(local, /src="\/api\/images\/render.webp\?source=%2Fuploads%2Fssr.png&amp;variant=full"/);
    for (const src of ['https://external.invalid/ssr.png', 'data:image/png;base64,c3ludGhldGlj']) {
      const markup = renderToString(createElement(WebpImage, { src, alt: 'Pending' }));
      assert.ok(!markup.includes(src)); assert.match(markup, /data-image-state="loading"/);
    }
  });
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  const page = await browser.newPage(); page.setDefaultTimeout(6000);
  const errors = [], blocked = [], preparations = [], imageRequests = [], pending = new Map();
  page.on('pageerror', error => errors.push(error.message));
  const prepared = (route, source, variant) => route.fulfill({ json: { image_url: `/images/cache/${Buffer.from(source).toString('hex').slice(-80) || 'source'}/${variant}.webp` } });
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.origin !== origin) { blocked.push(req.url()); return route.abort(); }
    if (url.pathname === '/api/images/prepare') {
      assert.equal(req.method(), 'POST');
      const { source, variant } = req.postDataJSON(); preparations.push({ source, variant });
      if (source.includes('hold-')) { pending.set(source, () => prepared(route, source, variant)); return; }
      if (source.includes('fail-')) return route.fulfill({ status: 502, json: { detail: 'synthetic failure' } });
      if (source.includes('bad-result')) return route.fulfill({ json: { image_url: 'https://external.invalid/original.png' } });
      return prepared(route, source, variant);
    }
    if (url.pathname.startsWith('/api/images/')) {
      assert.equal(req.method(), 'GET'); imageRequests.push(url.pathname + url.search);
      if (url.search.includes('broken')) return route.fulfill({ status: 404, body: '' });
      if (url.search.includes('hold-load')) { pending.set('load', () => route.fulfill({ contentType: 'image/webp', body: raster })); return; }
      return route.fulfill({ contentType: 'image/webp', headers: { ETag: '"synthetic-webp"' }, body: raster });
    }
    if (url.pathname === '/vector.svg') return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="red"/></svg>' });
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/uploads/') || !['GET', 'HEAD'].includes(req.method())) { blocked.push(req.url()); return route.abort(); }
    return route.continue();
  });
  await page.goto(origin + '/image-test'); await page.waitForFunction(() => window.imageFixture);
  const set = items => page.evaluate(items => window.imageFixture.setItems(items), items);
  const ready = async id => { await expect(page.locator(`#${id}`)).toHaveAttribute('data-image-state', 'ready'); assert.ok(await page.locator(`#${id}`).evaluate(e => e.complete && e.naturalWidth > 0)); };
  const finish = async source => { await expect.poll(() => pending.has(source)).toBe(true); await pending.get(source)(); pending.delete(source); };
  await check('helper mapping, legacy normalization, idempotence, variants, audio preservation', async () => {
    const result = await page.evaluate(() => {
      const { imageUrlFor, assetUrlFor, avatarUrlFor, conversationThumbnailUrlFor } = window.imageFixture.assets;
      return [imageUrlFor('/uploads/a b.png'), imageUrlFor('http://127.0.0.1:8123/uploads/a.jpg?old=1#x', 'full'),
        imageUrlFor('/api/images/render.webp?source=x&variant=full'), imageUrlFor('/images/cache/key/thumbnail.webp'),
        assetUrlFor('/uploads/a.mp3'), avatarUrlFor({ avatar_url: '/uploads/a.png' }), conversationThumbnailUrlFor({ thumbnail_url: '/uploads/w.png' }), imageUrlFor('https://external.invalid/image.png')];
    });
    assert.deepEqual(result, ['/api/images/render.webp?source=%2Fuploads%2Fa+b.png&variant=thumbnail', '/api/images/render.webp?source=%2Fuploads%2Fa.jpg&variant=full', '/api/images/render.webp?source=x&variant=full', '/api/images/cache/key/thumbnail.webp', '/api/uploads/a.mp3', '/api/images/render.webp?source=%2Fuploads%2Fa.png&variant=thumbnail', '/api/images/render.webp?source=%2Fuploads%2Fw.png&variant=thumbnail', 'https://external.invalid/image.png']);
  });
  await check('actual production img decodes real WebP; refs/classes/alt/events and srcSet cannot bypass routing', async () => {
    await set([{ id: 'local', src: '/uploads/local.png', alt: 'Local picture', className: 'preserved', width: 32, srcSet: 'https://external.invalid/bypass.jpg 2x' }]); await ready('local');
    await expect(page.getByRole('img', { name: 'Local picture' })).toHaveClass('preserved');
    assert.equal(await page.evaluate(() => window.imageRefs.local === document.getElementById('local')), true);
    assert.equal(await page.locator('#local').getAttribute('srcset'), null);
    await page.locator('#local').click(); assert.ok((await page.evaluate(() => window.imageEvents)).some(e => e[0] === 'click'));
    assert.ok(imageRequests.some(url => url.includes('render.webp?source=%2Fuploads%2Flocal.png&variant=thumbnail')));
  });
  await check('remote/data preparation, in-flight and result dedup, full variants', async () => {
    const source = 'https://external.invalid/hold-dedup.png';
    await set([{ id: 'a', src: source }, { id: 'b', src: source }]);
    await expect.poll(() => preparations.filter(x => x.source === source).length).toBe(1);
    assert.equal(await page.locator('#a').getAttribute('src'), null); await finish(source); await ready('a'); await ready('b');
    await set([{ id: 'c', src: source }]); await ready('c'); assert.equal(preparations.filter(x => x.source === source).length, 1);
    const data = 'data:image/png;base64,c3ludGhldGljLW9ubHk=';
    await set([{ id: 'data', src: data }, { id: 'full', src: '/uploads/large.png', variant: 'full' }]); await ready('data'); await ready('full');
    assert.ok(preparations.some(x => x.source === data && x.variant === 'thumbnail'));
    await expect(page.locator('#full')).toHaveAttribute('src', /variant=full$/);
    await set([{ id: 'fullremote', src: 'https://external.invalid/full.jpg', variant: 'full' }]); await ready('fullremote');
    assert.ok(preparations.some(x => x.source.endsWith('/full.jpg') && x.variant === 'full'));
  });
  await check('source replacement, A→B→A and unmount cannot adopt stale work; shared subscriber survives', async () => {
    const a = 'https://external.invalid/hold-old.png', b = 'https://external.invalid/hold-new.png';
    await set([{ id: 'swap', src: a }, { id: 'shared', src: a }]); await expect.poll(() => pending.has(a)).toBe(true);
    await set([{ id: 'swap', src: b }, { id: 'shared', src: a }]); await expect.poll(() => pending.has(b)).toBe(true);
    await finish(a); await ready('shared'); assert.equal(await page.locator('#swap').getAttribute('src'), null);
    await set([{ id: 'swap', src: a }]); await ready('swap'); const src = await page.locator('#swap').getAttribute('src');
    await finish(b); await expect(page.locator('#swap')).toHaveAttribute('src', src);
    const gone = 'https://external.invalid/hold-unmount.png'; await set([{ id: 'gone', src: gone }]); await expect.poll(() => pending.has(gone)).toBe(true);
    await set([]); await finish(gone); await expect(page.locator('#gone')).toHaveCount(0);
  });
  await check('preparation failure, malformed result and broken derived image never fall back to original', async () => {
    await set([{ id: 'failure', src: 'https://external.invalid/fail-image.png' }, { id: 'invalid', src: 'https://external.invalid/bad-result.png' }, { id: 'broken', src: '/uploads/broken.png' }]);
    for (const id of ['failure', 'invalid', 'broken']) { await expect(page.locator(`#${id}`)).toHaveAttribute('data-image-state', 'error'); assert.equal(await page.locator(`#${id}`).getAttribute('src'), null); await expect(page.locator(`#${id}`)).toHaveAttribute('title', 'Image unavailable'); }
    assert.ok((await page.evaluate(() => window.imageEvents)).some(e => e[0] === 'error' && e[1] === 'broken'));
  });
  await check('shipped branding and known data SVG remain direct; external SVG suffix cannot bypass WebP', async () => {
    const brands = ['symbol', 'horizontal', 'app-icon'].flatMap(kind => [`/brand/lorely-${kind}.webp`, ...['light', 'dark'].map(theme => `/brand/lorely-${kind}-${theme}.webp`)]);
    assert.match(readFileSync('index.html', 'utf8'), /type="image\/webp" href="\/brand\/lorely-app-icon\.webp"/);
    const before = preparations.length; await set([...brands.map((src, i) => ({ id: `brand${i}`, src })), { id: 'vector', src: 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="red"/></svg>') }]);
    for (let i = 0; i < brands.length; i++) await ready(`brand${i}`); await ready('vector'); assert.equal(preparations.length, before);
    await set([{ id: 'disguised', src: 'https://external.invalid/raster.svg' }]); await ready('disguised');
    assert.ok(preparations.some(value => value.source === 'https://external.invalid/raster.svg'));
    await expect(page.locator('#disguised')).toHaveAttribute('src', /images\/cache\/.*\.webp$/);
  });
  await check('real ChatImageFrame and AvatarPreviewModal choose thumbnail/full without QueryClientProvider', async () => {
    await page.evaluate(() => window.imageFixture.setSurface('/uploads/surface.jpg'));
    await expect(page.locator('.bubble-asset-image')).toHaveAttribute('data-image-state', 'ready');
    await expect(page.locator('.bubble-asset-image')).toHaveAttribute('src', /variant=thumbnail$/);
    await expect(page.locator('.avatar-preview-image')).toHaveAttribute('data-image-state', 'ready');
    await expect(page.locator('.avatar-preview-image')).toHaveAttribute('src', /variant=full$/);
    await page.evaluate(() => window.imageFixture.setSurface(''));
  });
  await check('finite preparation/load wait, failure TTL and successful memo TTL', async () => {
    await page.clock.install();
    const timeout = 'https://external.invalid/hold-timeout.png'; await set([{ id: 'timeout', src: timeout }]); await expect.poll(() => pending.has(timeout)).toBe(true);
    await page.clock.fastForward(15_001); await expect(page.locator('#timeout')).toHaveAttribute('data-image-state', 'error');
    await set([{ id: 'loadtimeout', src: '/uploads/hold-load.png' }]); await expect.poll(() => pending.has('load')).toBe(true);
    await page.clock.fastForward(20_001); await expect(page.locator('#loadtimeout')).toHaveAttribute('data-image-state', 'error');
    await set([{ id: 'retry', src: 'https://external.invalid/fail-image.png' }]); await expect(page.locator('#retry')).toHaveAttribute('data-image-state', 'error');
    const before = preparations.filter(x => x.source.includes('fail-image')).length;
    await set([{ id: 'retry2', src: 'https://external.invalid/fail-image.png' }]); await expect(page.locator('#retry2')).toHaveAttribute('data-image-state', 'error'); assert.equal(preparations.filter(x => x.source.includes('fail-image')).length, before);
    await page.clock.fastForward(10_001); await set([{ id: 'retry3', src: 'https://external.invalid/fail-image.png' }]); await expect.poll(() => preparations.filter(x => x.source.includes('fail-image')).length).toBe(before + 1);
    const cached = 'https://external.invalid/ttl.png'; await set([{ id: 'ttl', src: cached }]); await ready('ttl'); await set([]); await page.clock.fastForward(300_001);
    await set([{ id: 'ttl2', src: cached }]); await ready('ttl2'); assert.equal(preparations.filter(x => x.source === cached).length, 2);
  });
  await check('result memo evicts old entries after its 128-entry bound', async () => {
    await page.evaluate(async () => {
      const { prepareImageUrl } = window.imageFixture.images;
      for (let i = 0; i < 130; i++) await prepareImageUrl(`https://external.invalid/evict-${i}.png`, 'thumbnail');
      await prepareImageUrl('https://external.invalid/evict-0.png', 'thumbnail');
    });
    assert.equal(preparations.filter(x => x.source.endsWith('/evict-0.png')).length, 2);
  });
  await check('bounded shared preparation rejects overload and never sends originals to img', async () => {
    const count = await page.evaluate(async () => {
      const images = window.imageFixture.images;
      // All remain pending until their finite timeout; the 33rd must fail immediately.
      const calls = Array.from({ length: 33 }, (_, i) => images.prepareImageUrl(`https://external.invalid/hold-limit-${i}.png`, 'thumbnail').catch(() => 'rejected'));
      return await Promise.race([calls[32], Promise.resolve('pending').then(() => new Promise(resolve => setTimeout(() => resolve('not bounded'), 1000)))]);
    });
    assert.equal(count, 'rejected');
  });
  await check('source inventory has only the common raw img; Markdown images/HTML remain inert', async () => {
    const walk = dir => readdirSync(dir, { withFileTypes: true }).flatMap(e => e.isDirectory() ? walk(`${dir}/${e.name}`) : [`${dir}/${e.name}`]);
    const consumers = [];
    for (const file of walk('src').filter(f => f.endsWith('.tsx'))) {
      const source = readFileSync(file, 'utf8'); const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      assert.equal(tree.parseDiagnostics.length, 0, file);
      const visit = node => { if ((ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) && node.tagName.getText(tree) === 'img') consumers.push(file); ts.forEachChild(node, visit); }; visit(tree);
    }
    assert.deepEqual(consumers, ['src/components/atoms/WebpImage.tsx']);
    const md = readFileSync('src/components/molecules/BubbleRichText.tsx', 'utf8'); assert.match(md, /\['p', 'strong', 'em', 'code', 'br'\]/); assert.match(md, /skipHtml/);
  });
  assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
  console.log(JSON.stringify({ passed, pageErrors: errors, prohibitedRequests: blocked, prepared: preparations.length, derivedWebpRequests: imageRequests.length, result: 'passed' }));
} finally { await browser?.close(); await server.close(); }
