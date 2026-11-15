import assert from 'node:assert/strict';
import { readFileSync, existsSync, mkdirSync } from 'node:fs';
import { createServer } from 'node:http';
import { resolve, extname } from 'node:path';
import { chromium, expect } from '@playwright/test';

const dist = resolve(process.env.AGENT_DIST || 'dist');
assert.ok(existsSync(resolve(dist, 'index.html')), 'Build production assets first');
const artifacts = process.env.AGENT_ARTIFACT_DIR;
if (artifacts) mkdirSync(artifacts, { recursive: true });
const mime = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.webp': 'image/webp', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' };
const server = createServer((req, res) => {
  const path = new URL(req.url, 'http://fixture').pathname;
  if (path.startsWith('/api/')) { res.writeHead(500); res.end('UNINTERCEPTED API'); return; }
  const requested = resolve(dist, '.' + path);
  const file = requested.startsWith(dist + '/') && existsSync(requested) && extname(requested) ? requested : resolve(dist, 'index.html');
  res.setHeader('Content-Type', mime[extname(file)] || 'application/octet-stream'); res.end(readFileSync(file));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });
const dict = JSON.parse(readFileSync('src/i18n/messages.en.json', 'utf8'));
const t = key => dict[key] || key;
const image = readFileSync('public/brand/lorely-app-icon-light.webp');
const draftFor = (id, kind, target_id, after) => ({ id, session_id: null, kind, target_id, after, before: target_id ? { name: 'Original' } : null, changes: after, version: 1, preview_hash: `${id}-1`, base_revision: target_id ? 1 : null, status: 'draft', warnings: [], result: null });
async function setup(width = 1360) {
  const page = await browser.newPage({ viewport: { width, height: 1000 } });
  await page.addInitScript(() => localStorage.setItem('lorechat-language', 'en'));
  const s = {
    writes: [], errors: [], reads: [], operations: {}, abortCommit: false, holdDraft: null,
    characters: [{ id: 'c1', name: 'Original', persona: 'Unchanged persona', description: 'Keep description', appearance: 'Original appearance', emotional_rules: ['Keep calm'], forbidden_rules: ['Keep rule'], trait_scores: { empathy: 65 }, avatar_url: '/portrait.webp', tts_provider: 'supertonic', tts_model: 'supertonic-3', tts_voice_style: 'F2', tts_sample_text: 'Keep voice sample', unrelated: 'Retained column' }, { id: 'c2', name: 'Other character', persona: 'Other persona' }],
    worlds: [{ id: 'w1', title: 'Existing world', world_seed: 'Existing rules', thumbnail_url: '/world.webp', enabled: false, tags: ['retained'] }],
    drafts: {
      world: draftFor('world', 'world', null, { title: 'Proposed world', world_seed: 'Proposed rules', description: 'New description', enabled: true, tags: ['proposal'] }),
      character: draftFor('character', 'character', 'c1', { name: 'Proposed character', appearance: 'Proposed appearance' }),
    },
  };
  page.on('pageerror', error => s.errors.push(error.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    if (url.origin !== origin) return route.abort();
    if (!path.startsWith('/api/')) {
      if (['/portrait.webp', '/world.webp'].includes(path)) return route.fulfill({ body: image, contentType: 'image/webp' });
      return method === 'GET' ? route.continue() : route.abort();
    }
    const json = (value, status = 200) => route.fulfill({ json: value, status });
    const body = method === 'GET' ? null : req.postDataJSON();
    if (method === 'GET') s.reads.push(path); else if (path !== '/api/images/prepare') s.writes.push({ path, method, body });
    if (path === '/api/lore-agent/settings') return json({ enabled: true, llm_model_option_key: 'synthetic', llm_options: [], image_accounts: [], image_models: [] });
    if (path === '/api/lore-agent/sessions') return json([{ id: 'session', title: 'Synthetic', messages: [], drafts: Object.values(s.drafts), busy: false }]);
    if (path.startsWith('/api/lore-agent/operations/')) return json(s.operations[path.split('/').at(-1)] || { request_id: path.split('/').at(-1), status: 'unknown', result: null });
    if (path.startsWith('/api/lore-agent/drafts/')) {
      const [, , , , id, action] = path.split('/');
      const draft = s.drafts[id]; assert.ok(draft, path);
      if (method === 'GET') { if (s.holdDraft) await s.holdDraft; return json(draft); }
      assert.equal(body.expected_version, draft.version, 'exact expected version');
      if (action === 'revise') {
        assert.ok(!('thumbnail_url' in body.fields)); assert.ok(!('avatar_url' in body.fields)); assert.ok(!('tts_provider' in body.fields));
        draft.after = { ...draft.after, ...body.fields }; draft.version++; draft.preview_hash = `${id}-${draft.version}`; draft.status = 'draft'; return json(draft);
      }
      assert.equal(body.preview_hash, draft.preview_hash, 'exact preview hash');
      if (action === 'approve') { draft.status = 'approved'; return json(draft); }
      assert.equal(action, 'commit'); assert.equal(draft.status, 'approved');
      const journal = await page.evaluate(() => JSON.parse(sessionStorage.getItem('lore-agent-request-recovery-v1')));
      assert.equal(journal[0].request_id, body.request_id); assert.equal(journal[0].draft_id, id);
      if (s.holdCommit) await s.holdCommit;
      if (s.abortCommit) { s.operations[body.request_id] = { request_id: body.request_id, status: 'unknown', result: null }; return route.abort(); }
      const target = draft.target_id || `${draft.kind}-created`;
      const list = draft.kind === 'character' ? s.characters : s.worlds;
      const existing = list.find(item => item.id === target);
      if (existing) Object.assign(existing, draft.after); else list.push({ id: target, ...draft.after });
      draft.status = 'committed'; draft.result = { id: target, kind: draft.kind };
      const op = s.operations[body.request_id] = { request_id: body.request_id, status: 'completed', result: draft.result, kind: draft.kind };
      return json(op);
    }
    if (path === '/api/images/prepare') return json({ image_url: '/api/images/cache/fixture.webp' });
    if (path.startsWith('/api/images/cache/')) return route.fulfill({ body: image, contentType: 'image/webp' });
    if (path === '/api/characters' || path === '/api/world-settings') {
      const list = path === '/api/characters' ? s.characters : s.worlds;
      if (method === 'POST') { const saved = { id: 'ordinary-created', ...body }; list.push(saved); return json(saved); }
      assert.equal(method, 'GET');
      return json(url.searchParams.has('paginated') ? { items: list, total: list.length, page: 1, page_size: 20, pages: 1 } : list);
    }
    if (path === '/api/characters/usage-ranking') return json([]);
    if (/^\/api\/characters\/[^/]+$/.test(path)) {
      const item = s.characters.find(item => item.id === path.split('/').at(-1));
      if (method === 'PATCH') Object.assign(item, body); else assert.equal(method, 'GET');
      return json(item);
    }
    assert.equal(method, 'GET', `No unexpected mutation: ${path}`);
    if (path.startsWith('/api/runtime-settings')) return json({});
    if (path === '/api/health') return json({ status: 'ok' });
    return json([]);
  });
  const navigate = path => page.evaluate(path => { history.pushState({}, '', path); dispatchEvent(new PopStateEvent('popstate')); }, path);
  const open = async id => {
    await page.locator('.agent-launcher').click();
    await page.locator(`[data-agent-draft="${id}"]`).getByRole('button', { name: t('agent.reviewProposal'), exact: true }).click();
    await expect(page.locator('.agent-dock:visible, .agent-modal:visible')).toHaveCount(0);
  };
  return { page, s, navigate, open };
}
try {
  // New world: real App route and native editor, never ordinary create.
  {
    const { page, s, open } = await setup(820);
    await page.goto(origin + '/characters/c1');
    await expect(page.getByLabel(t('이름'), { exact: true })).toHaveValue('Original');
    await open('world');
    await expect(page).toHaveURL(/\/world-settings\/new$/);
    await expect(page.getByLabel(t('세계관 이름'), { exact: true })).toHaveValue('Proposed world');
    await expect(page.getByLabel(t('세계관/규칙'), { exact: true })).toHaveValue('Proposed rules');
    assert.equal(s.writes.length, 0, 'opening and prefilling perform no mutation');
    await expect(page.getByLabel(t('세계관 썸네일 URL'), { exact: true })).toBeDisabled();
    await page.getByLabel(t('세계관 이름'), { exact: true }).fill('Owner world');
    await page.getByRole('button', { name: t('저장'), exact: true }).click();
    await expect(page).toHaveURL(/\/world-settings\/world-created$/);
    assert.deepEqual(s.writes.map(w => w.path.split('/').at(-1)), ['revise', 'approve', 'commit']);
    assert.deepEqual(s.writes[0].body.fields, { title: 'Owner world' });
    assert.equal(s.writes[1].body.expected_version, 2); assert.equal(s.writes[2].body.preview_hash, 'world-2');
    assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem('lore-agent-request-recovery-v1')).length), 0);
    assert.deepEqual(s.errors, []); await page.close();
  }
  // Partial edit: full resource baseline, highlights/tabs, media/voice retained.
  {
    const { page, s, open } = await setup();
    await page.goto(origin + '/characters/c1');
    const name = page.getByLabel(t('이름'), { exact: true });
    await expect(name).toHaveValue('Original'); await name.fill('Unsaved ordinary draft');
    await open('character'); await expect(name).toHaveValue('Proposed character');
    assert.equal(s.writes.length, 0);
    await expect(page.locator('[data-proposal-field="name"]')).toBeVisible();
    await expect(page.locator('[data-proposal-tab="prompt"]')).toBeVisible();
    await expect(page.getByLabel(t('설명'), { exact: true })).toHaveValue('Keep description');
    await expect(page.getByLabel(t('아바타 URL'), { exact: true })).toHaveValue('/portrait.webp');
    await expect(page.getByLabel(t('아바타 URL'), { exact: true })).toBeDisabled();
    await expect(page.getByRole('button', { name: t('TTS'), exact: true })).toBeDisabled();
    await page.getByRole('button', { name: t('취소'), exact: true }).click();
    await expect(name).toHaveValue('Unsaved ordinary draft');
    await open('character'); await expect(name).toHaveValue('Proposed character');
    await page.getByRole('button', { name: t('프롬프트'), exact: true }).click();
    await expect(page.getByLabel(t('페르소나'), { exact: true })).toHaveValue('Unchanged persona');
    await expect(page.locator('[data-proposal-field="appearance"]')).toBeVisible();
    if (artifacts) await page.screenshot({ path: resolve(artifacts, 'native-character-highlight.png'), fullPage: true });
    await page.getByRole('button', { name: t('저장'), exact: true }).click();
    await expect(page.locator('[data-proposal-field]')).toHaveCount(0);
    assert.deepEqual(s.writes.map(w => w.path.split('/').at(-1)), ['approve', 'commit']);
    assert.equal(s.characters[0].tts_voice_style, 'F2'); assert.equal(s.characters[0].avatar_url, '/portrait.webp'); assert.equal(s.characters[0].unrelated, 'Retained column');
    assert.deepEqual(s.errors, []); await page.close();
  }
  // Stale draft is rejected before any approval/commit, edits stay in place.
  {
    const { page, s, open } = await setup();
    await page.goto(origin + '/characters/c1'); await open('world');
    await expect(page.getByLabel(t('세계관 이름'), { exact: true })).toHaveValue('Proposed world');
    s.drafts.world.version++; s.drafts.world.preview_hash = 'concurrent-change';
    await page.getByRole('button', { name: t('저장'), exact: true }).click();
    await expect(page.getByText(/The proposal changed/)).toBeVisible();
    assert.equal(s.writes.length, 0); await expect(page.getByLabel(t('세계관 이름'), { exact: true })).toHaveValue('Proposed world');
    assert.deepEqual(s.errors, []); await page.close();
  }
  // Unknown commit: durable request, disabled Save, GET-only Agent recovery.
  {
    const { page, s, open } = await setup();
    await page.goto(origin + '/characters/c1'); await open('world');
    await expect(page.getByLabel(t('세계관 이름'), { exact: true })).toHaveValue('Proposed world');
    s.abortCommit = true; await page.getByRole('button', { name: t('저장'), exact: true }).click();
    await expect(page.getByRole('button', { name: t('저장'), exact: true })).toBeDisabled();
    await expect.poll(() => s.writes.filter(w => w.path.endsWith('/commit')).length).toBe(1);
    await page.locator('.agent-launcher').click();
    await expect.poll(() => s.reads.filter(path => path.includes('/operations/')).length).toBeGreaterThan(0);
    assert.equal(s.writes.filter(w => w.path.endsWith('/commit')).length, 1);
    assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem('lore-agent-request-recovery-v1')).length), 1);
    assert.deepEqual(s.errors, []); await page.close();
  }
  // Slow proposal GET must never prefill the next resource route.
  {
    const { page, s, open, navigate } = await setup();
    await page.goto(origin + '/characters/c1');
    await expect(page.getByLabel(t('이름'), { exact: true })).toHaveValue('Original');
    let release; s.holdDraft = new Promise(resolve => { release = resolve; });
    await open('character');
    await navigate('/characters/c2'); release(); s.holdDraft = null;
    await expect(page.getByLabel(t('이름'), { exact: true })).toHaveValue('Other character');
    await page.waitForTimeout(100);
    await expect(page.getByLabel(t('이름'), { exact: true })).toHaveValue('Other character');
    assert.equal(s.writes.length, 0); assert.deepEqual(s.errors, []); await page.close();
  }
  // Native new-character and existing-world routes use the same transaction.
  {
    const { page, s, open, navigate } = await setup();
    s.drafts.character = draftFor('character', 'character', null, { name: 'Created character', persona: 'Created persona' });
    s.drafts.world = draftFor('world', 'world', 'w1', { title: 'Edited world', tags: ['revised'] });
    await page.goto(origin + '/characters/c1'); await open('character');
    await expect(page).toHaveURL(/\/characters\/new$/);
    await expect(page.getByLabel(t('이름'), { exact: true })).toHaveValue('Created character');
    await page.getByRole('button', { name: t('저장'), exact: true }).click();
    await expect(page).toHaveURL(/\/characters\/character-created$/);
    await navigate('/world-settings/w1'); await open('world');
    await expect(page.getByLabel(t('세계관 이름'), { exact: true })).toHaveValue('Edited world');
    await expect(page.getByLabel(t('세계관/규칙'), { exact: true })).toHaveValue('Existing rules');
    await expect(page.getByLabel(t('세계관 썸네일 URL'), { exact: true })).toHaveValue('/world.webp');
    await expect(page.locator('[data-proposal-field="title"]')).toBeVisible();
    await expect(page.locator('[data-proposal-field="tags"]')).toBeVisible();
    await expect(page.locator('[data-proposal-field="enabled"]')).toHaveCount(0);
    await page.getByRole('button', { name: t('저장'), exact: true }).click();
    await expect(page.locator('[data-proposal-field]')).toHaveCount(0);
    assert.equal(s.worlds[0].thumbnail_url, '/world.webp'); assert.equal(s.worlds[0].enabled, false);
    assert.deepEqual(s.writes.map(w => w.path.split('/').at(-1)), ['approve', 'commit', 'approve', 'commit']);
    assert.deepEqual(s.errors, []); await page.close();
  }
  // Same-route new-world review cancellation retains the ordinary unsaved draft.
  {
    const { page, s, open } = await setup();
    await page.goto(origin + '/world-settings/new');
    await page.getByLabel(t('세계관 이름'), { exact: true }).fill('Unsaved ordinary world');
    await open('world');
    await expect(page.getByLabel(t('세계관 이름'), { exact: true })).toHaveValue('Proposed world');
    await page.getByRole('button', { name: t('취소'), exact: true }).click();
    await expect(page.getByLabel(t('세계관 이름'), { exact: true })).toHaveValue('Unsaved ordinary world');
    assert.equal(s.writes.length, 0); assert.deepEqual(s.errors, []); await page.close();
  }
  // A late successful commit reconciles its receipt without changing another route.
  {
    const { page, s, open, navigate } = await setup();
    await page.goto(origin + '/characters/c1'); await open('world');
    await expect(page.getByLabel(t('세계관 이름'), { exact: true })).toHaveValue('Proposed world');
    let release; s.holdCommit = new Promise(resolve => { release = resolve; });
    await page.getByRole('button', { name: t('저장'), exact: true }).click();
    await expect.poll(() => s.writes.filter(w => w.path.endsWith('/commit')).length).toBe(1);
    await navigate('/characters/c2');
    await expect(page.getByLabel(t('이름'), { exact: true })).toHaveValue('Other character');
    release(); s.holdCommit = null;
    await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('lore-agent-request-recovery-v1')).length)).toBe(0);
    await expect(page).toHaveURL(/\/characters\/c2$/);
    await expect(page.getByLabel(t('이름'), { exact: true })).toHaveValue('Other character');
    assert.equal(s.writes.filter(w => w.path.endsWith('/commit')).length, 1);
    assert.deepEqual(s.errors, []); await page.close();
  }
  // Ordinary editors retain their original CRUD paths and editable media.
  {
    const { page, s, navigate } = await setup();
    await page.goto(origin + '/characters/c1');
    await expect(page.getByLabel(t('이름'), { exact: true })).toHaveValue('Original');
    await expect(page.getByLabel(t('아바타 URL'), { exact: true })).toBeEnabled();
    await page.getByLabel(t('이름'), { exact: true }).fill('Ordinary character');
    await page.getByRole('button', { name: t('저장'), exact: true }).click();
    await expect.poll(() => s.writes.some(w => w.method === 'PATCH' && w.path === '/api/characters/c1')).toBe(true);
    await navigate('/world-settings/new');
    await page.getByLabel(t('세계관 이름'), { exact: true }).fill('Ordinary world');
    await page.getByLabel(t('세계관/규칙'), { exact: true }).fill('Ordinary rules');
    await expect(page.getByLabel(t('세계관 썸네일 URL'), { exact: true })).toBeEnabled();
    await page.getByRole('button', { name: t('저장'), exact: true }).click();
    await expect(page).toHaveURL(/\/world-settings\/ordinary-created$/);
    assert.equal(s.writes.filter(w => w.path.startsWith('/api/lore-agent/')).length, 0);
    assert.deepEqual(s.errors, []); await page.close();
  }
  console.log('PASS production native proposal forms: world create/prefill/exact Save, partial character/full baseline/highlights/unsaved cancel, stale rejection, unknown journal GET-only recovery, slow-read route isolation, unchanged ordinary CRUD.');
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
