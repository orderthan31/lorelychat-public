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
  await page.addInitScript(() => { localStorage.setItem('lorechat-language', 'en'); window.Audio = class { play() { return Promise.resolve(); } }; });
  const s = {
    writes: [], errors: [], reads: [], documents: [], assets: [], operations: {}, abortCommit: false, holdDraft: null,
    characters: [{ id: 'c1', name: 'Original', persona: 'Unchanged persona', description: 'Keep description', appearance: 'Original appearance', behavior_style: '', speech_style: '', emotional_rules: ['Keep calm'], forbidden_rules: ['Keep rule'], trait_scores: { empathy: 65 }, avatar_url: '/portrait.webp', tts_provider: 'supertonic', tts_model: 'supertonic-3', tts_voice_style: 'F2', tts_sample_text: 'Keep voice sample', unrelated: 'Retained column' }, { id: 'c2', name: 'Other character', persona: 'Other persona' }],
    worlds: [{ id: 'w1', title: 'Existing world', world_seed: 'Existing rules', description: '', genre_mode: 'battle', location: '', mood: '', opening_scene: '', opening_line: '', tone_preset: '', relationship_archetype: '', compression_focus: '', thumbnail_url: '/world.webp', enabled: false, tags: ['retained'] }],
    drafts: {
      world: draftFor('world', 'world', null, { title: 'Proposed world', world_seed: 'Proposed rules', description: 'New description', enabled: true, tags: ['proposal'] }),
      character: draftFor('character', 'character', 'c1', { name: 'Proposed character', appearance: 'Proposed appearance' }),
    },
  };
  page.on('pageerror', error => s.errors.push(error.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    if (req.resourceType() === 'document') s.documents.push(path);
    if (url.origin !== origin) return route.abort();
    if (!path.startsWith('/api/')) {
      if (['/portrait.webp', '/world.webp'].includes(path)) return route.fulfill({ body: image, contentType: 'image/webp' });
      return method === 'GET' ? route.continue() : route.abort();
    }
    const json = (value, status = 200) => route.fulfill({ json: value, status });
    const body = method === 'GET' || !req.headers()['content-type']?.includes('application/json') ? null : req.postDataJSON();
    if (method === 'GET') s.reads.push(path); else if (path !== '/api/images/prepare') s.writes.push({ path, method, body });
    if (path === '/api/lore-agent/settings') return json({ enabled: true, llm_model_option_key: 'synthetic', llm_options: [], image_accounts: [], image_models: [] });
    if (path === '/api/lore-agent/sessions' && method === 'POST') return json({id:'session',title:'Synthetic',messages:[],drafts:[],busy:false});
    if (path === '/api/lore-agent/sessions/session/messages') {
      s.messageId = body.request_id;
      await s.messageHold;
      return json({id:'session',title:'Synthetic',messages:[{id:'answer',role:'assistant',content:'Finished synthetic request'}],drafts:[],busy:false});
    }
    if (path === '/api/lore-agent/sessions' || path === '/api/lore-agent/sessions/session') {
      const session = { id: 'session', title: 'Synthetic', messages: s.history || [], drafts: Object.values(s.drafts), busy: false };
      return json(path.endsWith('/session') ? session : [session]);
    }
    if (path.startsWith('/api/lore-agent/operations/')) {
      if (s.progressTest) {
        s.pollCount = (s.pollCount || 0) + 1;
        s.pollActive = (s.pollActive || 0) + 1;
        s.pollMax = Math.max(s.pollMax || 0, s.pollActive);
        const sequence = s.pollCount;
        if (sequence === 1) await new Promise(resolve => setTimeout(resolve, 2400));
        s.pollActive--;
        return json({ request_id: path.split('/').at(-1), status: 'running', kind: 'agent_turn', progress: { stage: sequence === 1 ? 'model' : 'search', sequence }, result: null, error: null });
      }
      return json(s.operations[path.split('/').at(-1)] || { request_id: path.split('/').at(-1), status: 'unknown', result: null });
    }
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
      assert.equal(action, 'commit-form'); assert.equal(draft.status, 'approved');
      const journal = await page.evaluate(() => JSON.parse(sessionStorage.getItem('lore-agent-request-recovery-v1')));
      assert.equal(journal[0].request_id, body.request_id); assert.equal(journal[0].draft_id, id);
      if (s.holdCommit) await s.holdCommit;
      if (s.abortCommit) { s.operations[body.request_id] = { request_id: body.request_id, status: 'unknown', result: null }; return route.abort(); }
      assert.deepEqual(Object.keys(body.baseline_fields).sort(), draft.target_id ? (draft.kind === 'character' ? ['name','persona','description','appearance','behavior_style','speech_style','emotional_rules','forbidden_rules','trait_scores'] : ['title','description','genre_mode','location','mood','world_seed','opening_scene','opening_line','tone_preset','relationship_archetype','compression_focus','tags','enabled']).sort() : []);
      if (s.rebase) { draft.base_revision++; draft.version++; draft.preview_hash = `${id}-rebased`; }
      const target = draft.target_id || `${draft.kind}-created`;
      const list = draft.kind === 'character' ? s.characters : s.worlds;
      const existing = list.find(item => item.id === target);
      if (existing) Object.assign(existing, draft.after, body.manual_fields); else list.push({ id: target, ...draft.after, ...body.manual_fields });
      draft.status = 'committed'; draft.result = { id: target, kind: draft.kind };
      const op = s.operations[body.request_id] = { request_id: body.request_id, status: 'completed', result: { ...draft.result, draft_id: draft.id, version: draft.version, preview_hash: draft.preview_hash }, kind: draft.kind };
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
    if (path === '/api/characters/c1/tts-sample') return json({audio_url:'/fixture-only.wav'});
    if (path === '/api/uploads/avatars') return json({ avatar_url: '/uploaded.webp' });
    if (path === '/api/tts/models') return json([{key:'supertonic-3',provider:'supertonic',label:'Local'}]);
    if (path === '/api/tts/voices') return json([{key:'F1',provider:'supertonic',label:'F1'},{key:'F2',provider:'supertonic',label:'F2'}]);
    if (path === '/api/characters/c1/assets') {
      if (method === 'POST') { const asset = { id: 'asset-1', ...body }; s.assets.push(asset); return json(asset); }
      return json(s.assets);
    }
    if (path === '/api/characters/c1/assets/upload') { const asset = { id: 'asset-upload', label: 'Uploaded asset', image_url: '/uploaded.webp', enabled: true }; s.assets.push(asset); return json(asset); }
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
    if (!await page.locator('.agent-surface').isVisible()) await page.locator('.agent-launcher').click();
    await page.locator(`[data-agent-draft="${id}"]`).getByRole('button', { name: t('agent.reviewProposal'), exact: true }).click();
    await expect(page.locator('.agent-surface:visible')).toHaveCount(page.viewportSize().width >= 768 ? 1 : 0);
  };
  return { page, s, navigate, open };
}
try {
  // Bounded presentation gate can run without replaying unrelated paid-action
  // fixture scenarios. The default invocation still runs all existing coverage.
  {
    const { page, s, open } = await setup(390);
    s.history = Array.from({ length: 30 }, (_, i) => ({ id: `history-${i}`, role: 'assistant', content: 'Synthetic reading history. '.repeat(12) }));
    await page.goto(origin + '/characters/c1');
    await page.locator('.agent-launcher').click();
    const panel = page.locator('.agent-surface');
    await panel.getByText(t('agent.chatAdvanced'), { exact: true }).click();
    await panel.getByText(t('agent.history'), { exact: true }).click();
    await panel.getByLabel(t('agent.sessions')).selectOption('session');
    await panel.getByText(t('agent.chatAdvanced'), { exact: true }).click();
    const input = panel.locator('.agent-composer textarea');
    await input.fill('Retained draft 한글 composition');
    await input.evaluate(node => { window.retainedComposer = node; node.focus(); node.setSelectionRange(3, 8); node.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true, data: '한' })); });
    const transcript = panel.locator('.agent-transcript');
    await transcript.evaluate(node => { window.retainedTranscript = node; node.scrollTop = 100; node.dispatchEvent(new Event('scroll')); });
    for (const [width, height] of [[390,844], [820,1180], [1024,768], [1440,1000], [667,375], [390,844]]) {
      await page.setViewportSize({ width, height });
      await expect(panel).toHaveAttribute('data-surface', width >= 768 ? 'dock' : 'sheet');
      await expect(input).toBeFocused();
      await expect(input).toHaveValue('Retained draft 한글 composition');
      assert.equal(await input.evaluate(node => node === window.retainedComposer), true);
      assert.deepEqual(await input.evaluate(node => [node.selectionStart, node.selectionEnd]), [3, 8]);
      assert.equal(await transcript.evaluate(node => node === window.retainedTranscript), true);
      await expect.poll(() => transcript.evaluate(node => Math.abs(node.scrollTop - 100))).toBeLessThan(4);
      const box = await panel.boundingBox(), composer = await panel.locator('.agent-composer').boundingBox();
      assert.ok(composer.y >= 0 && composer.y + composer.height <= height + 1, 'composer remains reachable');
      if (width >= 768) {
        const main = await page.locator('main.shell').boundingBox();
        assert.ok(main.x + main.width <= box.x + 1 && main.width >= 450, 'dock is a non-overlapping layout sibling');
        assert.ok(box.width >= 300 && box.width <= 420);
      } else {
        assert.ok(Math.abs(box.y + box.height - height) < 2, 'sheet anchored at bottom');
        if (height > 500) assert.ok(box.height / height >= .6 && box.height / height <= .68);
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      if (artifacts) await page.screenshot({ path: resolve(artifacts, `responsive-agent-${width}.png`) });
    }
    await input.evaluate(node => node.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '한글' })));
    await panel.getByRole('button', { name: t('닫기'), exact: true }).click();
    await expect(panel).toBeHidden();
    await page.locator('.agent-launcher').click();
    assert.equal(await input.evaluate(node => node === window.retainedComposer), true);
    await expect.poll(() => transcript.evaluate(node => Math.abs(node.scrollTop - 100))).toBeLessThan(4);
    await page.setViewportSize({ width: 820, height: 1180 });
    const name = page.getByLabel(t('이름'), { exact: true });
    await name.click(); await name.fill('Page remains interactive');
    await expect(panel).toBeVisible(); await expect(name).toBeFocused();
    await open('character'); await expect(name).toHaveValue('Proposed character');
    await name.fill('Retained native proposal');
    await name.evaluate(node => { window.retainedNative = node; node.setSelectionRange(2, 7); });
    const reads = s.reads.filter(p => p === '/api/lore-agent/drafts/character').length;
    await open('character');
    await expect(name).toHaveValue('Retained native proposal');
    assert.equal(await name.evaluate(node => node === window.retainedNative), true);
    assert.deepEqual(await name.evaluate(node => [node.selectionStart,node.selectionEnd]), [2,7]);
    assert.equal(s.reads.filter(p => p === '/api/lore-agent/drafts/character').length, reads);
    await page.keyboard.press('Escape'); await expect(panel).toBeHidden();
    await expect(page.locator('.agent-launcher')).toBeFocused();
    await page.locator('.agent-launcher').click(); await expect(input).toHaveValue('Retained draft 한글 composition');
    assert.equal(await input.evaluate(node => node === window.retainedComposer), true);
    await page.setViewportSize({ width: 390, height: 844 });
    await open('character'); await expect(panel).toBeHidden();
    await expect(name).toHaveValue('Retained native proposal');
    assert.equal(s.writes.length, 0, 'resize/reopen/proposal review never sends or saves');
    assert.deepEqual(s.errors, []); await page.close();
    console.log('PASS responsive Agent: 390 sheet, 820/1024/1440 dock, short landscape, one composer/transcript DOM, draft/selection/focus/reading retention, interactive native editor, repeated proposal CAS identity, Escape/reopen, phone proposal dismissal; zero writes.');
  }
  if (!process.env.AGENT_RESPONSIVE_ONLY) {
  // A real pending POST is polled independently of panel renders. The delayed
  // first GET exceeds the poll interval and must still complete and display.
  {
    const { page, s } = await setup();
    let release; s.messageHold = new Promise(resolve => { release=resolve; }); s.progressTest=true;
    await page.goto(origin + '/characters/c1'); await page.locator('.agent-launcher').click();
    await page.getByRole('textbox',{name:t('agent.message'),exact:true}).fill('Synthetic delayed request');
    await page.getByRole('button',{name:t('전송'),exact:true}).click();
    await expect(page.locator('.agent-progress')).toHaveText(t('agent.working'));
    await expect(page.locator('[data-agent-progress="model"]')).toHaveText(t('agent.progress.model'),{timeout:10000});
    assert.equal(s.pollCount,1); assert.equal(s.pollMax,1);
    await expect(page.locator('[data-agent-progress="search"]')).toHaveText(t('agent.progress.search'),{timeout:6000});
    release();
    await expect(page.getByText('Finished synthetic request',{exact:true})).toBeVisible();
    await expect(page.locator('.agent-progress')).toHaveCount(0);
    const count=s.pollCount; await page.waitForTimeout(2200); assert.equal(s.pollCount,count);
    assert.equal(s.writes.filter(w=>w.path.endsWith('/messages')).length,1);
    assert.deepEqual(s.errors,[]); await page.close();
  }
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
    await expect(page.getByLabel(t('세계관 썸네일 URL'), { exact: true })).toBeEnabled();
    await page.getByLabel(t('세계관 이름'), { exact: true }).fill('Owner world');
    await page.getByRole('button', { name: t('저장'), exact: true }).click();
    await expect(page).toHaveURL(/\/world-settings\/world-created$/);
    assert.deepEqual(s.writes.map(w => w.path.split('/').at(-1)), ['revise', 'approve', 'commit-form']);
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
    await expect(page.getByLabel(t('아바타 URL'), { exact: true })).toBeEnabled();
    await expect(page.getByRole('button', { name: t('TTS'), exact: true })).toBeEnabled();
    await page.getByRole('button', { name: t('취소'), exact: true }).click();
    await expect(name).toHaveValue('Unsaved ordinary draft');
    await open('character'); await expect(name).toHaveValue('Proposed character');
    await page.getByRole('button', { name: t('프롬프트'), exact: true }).click();
    await expect(page.getByLabel(t('페르소나'), { exact: true })).toHaveValue('Unchanged persona');
    await expect(page.locator('[data-proposal-field="appearance"]')).toBeVisible();
    if (artifacts) await page.screenshot({ path: resolve(artifacts, 'native-character-highlight.png'), fullPage: true });
    await page.getByRole('button', { name: t('저장'), exact: true }).click();
    await expect(page.locator('[data-proposal-field]')).toHaveCount(0);
    assert.deepEqual(s.writes.map(w => w.path.split('/').at(-1)), ['approve', 'commit-form']);
    assert.equal(s.characters[0].tts_voice_style, 'F2'); assert.equal(s.characters[0].avatar_url, '/portrait.webp'); assert.equal(s.characters[0].unrelated, 'Retained column');
    assert.deepEqual(s.errors, []); await page.close();
  }
  // Reopening the exact review preserves the live editor node/selection and
  // manual media fields; real App navigation must not request a new document.
  {
    const { page, s, open } = await setup();
    await page.goto(origin + '/characters/c1'); await open('character');
    const name = page.getByLabel(t('이름'), { exact: true });
    await name.fill('My retained proposal');
    await name.evaluate(node => { window.retainedEditor = node; node.setSelectionRange(3, 8); });
    const beforeReads = s.reads.filter(p => p === '/api/lore-agent/drafts/character').length;
    await open('character');
    await expect(name).toHaveValue('My retained proposal');
    assert.equal(await name.evaluate(node => node === window.retainedEditor), true);
    assert.deepEqual(await name.evaluate(node => [node.selectionStart,node.selectionEnd]), [3,8]);
    assert.equal(s.reads.filter(p => p === '/api/lore-agent/drafts/character').length, beforeReads);
    assert.equal(s.documents.length, 1, 'CTA is client navigation, not document reload');
    await page.getByLabel(t('아바타 파일 업로드'), { exact:true }).setInputFiles({name:'avatar.webp',mimeType:'image/webp',buffer:image});
    await expect(page.getByLabel(t('아바타 URL'), {exact:true})).toHaveValue('/uploaded.webp');
    await page.getByRole('button',{name:t('이미지'),exact:true}).click();
    await page.getByRole('button',{name:t('+ 에셋 추가'),exact:true}).click();
    await page.getByLabel(t('라벨'),{exact:true}).fill('Manual asset');
    await page.getByLabel(t('이미지 URL'),{exact:true}).fill('/manual.webp');
    await page.getByLabel(t('기본 프사'),{exact:true}).focus(); await page.keyboard.press('Space');
    await expect(page.getByLabel(t('기본 프사'),{exact:true})).toBeChecked();
    await page.getByRole('button',{name:t('URL 에셋 추가'),exact:true}).click();
    await expect.poll(() => s.assets.length).toBe(1);
    assert.equal(s.writes.find(w=>w.path==='/api/characters/c1/assets').body.is_default,false,'review default intent must remain local until form Save, not stale the Agent draft');
    await page.getByLabel(t('파일 업로드'),{exact:true}).setInputFiles({name:'asset.webp',mimeType:'image/webp',buffer:image});
    await expect.poll(() => s.assets.length).toBe(2);
    await page.getByRole('button',{name:/Manual asset/}).click();
    await page.getByRole('button',{name:t('기본 이미지'),exact:true}).click();
    assert.equal(s.writes.some(w=>w.path.endsWith('set-default-avatar')),false, 'default selection stays in review form');
    await page.getByRole('button',{name:t('TTS'),exact:true}).click();
    await page.getByRole('combobox').nth(2).selectOption('F1');
    await page.getByLabel(t('보이스 샘플 대사'),{exact:true}).fill('Manual voice sample');
    assert.equal(s.writes.some(w=>w.path.includes('tts-sample')),false,'no automatic voice generation');
    await page.getByRole('button',{name:t('샘플 듣기'),exact:true}).click();
    await expect.poll(() => s.writes.filter(w=>w.path.includes('tts-sample')).length).toBe(1);
    assert.equal(s.writes.find(w=>w.path.includes('tts-sample')).body.voice_style,'F1');
    await page.getByRole('button',{name:t('기본'),exact:true}).click();
    await expect(name).toHaveValue('My retained proposal');
    await expect(page.getByLabel(t('아바타 URL'),{exact:true})).toHaveValue('/manual.webp');
    s.rebase = true;
    await page.getByRole('button',{name:t('저장'),exact:true}).click();
    await expect(page.locator('[data-proposal-field]')).toHaveCount(0);
    const saves=s.writes.filter(w=>w.path.endsWith('/commit-form'));
    assert.equal(saves.length,1);
    assert.deepEqual(saves[0].body.manual_fields,{avatar_url:'/manual.webp',tts_voice_style:'F1',tts_sample_text:'Manual voice sample'});
    assert.equal(saves[0].body.baseline_fields.name,'Original');
    assert.deepEqual(saves[0].body.baseline_fields.trait_scores,{empathy:65}, 'raw GET baseline, not UI-filled trait defaults');
    assert.equal(s.writes.filter(w=>w.path.includes('tts-sample')).length,1,'only the explicit owner sample click; fixture-only response');
    assert.equal(s.writes.some(w=>w.method==='PATCH' && w.path==='/api/characters/c1'),false);
    assert.equal(s.characters[0].name,'My retained proposal'); assert.equal(s.characters[0].tts_voice_style,'F1');
    assert.deepEqual(s.errors,[]); await page.close();
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
    await expect.poll(() => s.writes.filter(w => w.path.endsWith('/commit-form')).length).toBe(1);
    await page.locator('.agent-surface').getByText(t('agent.chatAdvanced'), { exact: true }).click();
    await page.locator('.agent-advanced').getByRole('button', { name: t('agent.refresh'), exact: true }).click();
    await expect.poll(() => s.reads.filter(path => path.includes('/operations/')).length).toBeGreaterThan(0);
    assert.equal(s.writes.filter(w => w.path.endsWith('/commit-form')).length, 1);
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
    await page.getByRole('button',{name:t('TTS'),exact:true}).click();
    await expect(page.getByRole('button',{name:t('저장 후 듣기'),exact:true})).toBeDisabled();
    await page.getByRole('button',{name:t('이미지'),exact:true}).click();
    await expect(page.getByText(t('캐릭터를 먼저 저장하세요.'),{exact:true})).toBeVisible();
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
    assert.deepEqual(s.writes.map(w => w.path.split('/').at(-1)), ['approve', 'commit-form', 'approve', 'commit-form']);
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
    await expect.poll(() => s.writes.filter(w => w.path.endsWith('/commit-form')).length).toBe(1);
    await navigate('/characters/c2');
    await expect(page.getByLabel(t('이름'), { exact: true })).toHaveValue('Other character');
    release(); s.holdCommit = null;
    await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('lore-agent-request-recovery-v1')).length)).toBe(0);
    await expect(page).toHaveURL(/\/characters\/c2$/);
    await expect(page.getByLabel(t('이름'), { exact: true })).toHaveValue('Other character');
    assert.equal(s.writes.filter(w => w.path.endsWith('/commit-form')).length, 1);
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
  console.log('PASS production native proposal forms: real delayed progress, repeat-open node/caret/no document navigation, manual avatar upload/assets/default/voice and bound rebased commit-form, save-first new resource controls; world create/prefill/exact Save, partial character/full baseline/highlights/unsaved cancel, stale rejection, unknown journal GET-only recovery, slow-read route isolation, unchanged ordinary CRUD.');
  }
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
