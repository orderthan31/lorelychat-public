import assert from 'node:assert/strict';
import { readFileSync, existsSync, mkdirSync } from 'node:fs';
import { createServer } from 'node:http';
import { resolve, extname } from 'node:path';
import { chromium, expect } from '@playwright/test';

// Production app, loopback static assets only. Every API request is fulfilled;
// every external request is blocked. No provider or runtime is contacted.
const artifacts = process.env.AGENT_ARTIFACT_DIR;
if (artifacts) mkdirSync(artifacts, { recursive: true });
const dist = resolve(process.env.AGENT_DIST || 'dist');
assert.ok(existsSync(resolve(dist, 'index.html')), 'Build production app before running this gate');
const mime = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.webp': 'image/webp', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' };
const server = createServer((req, res) => {
  const url = new URL(req.url, 'http://fixture');
  if (url.pathname.startsWith('/api/')) { res.writeHead(500); res.end('UNINTERCEPTED API'); return; }
  const requested = resolve(dist, '.' + url.pathname);
  const file = requested.startsWith(dist + '/') && existsSync(requested) && extname(requested) ? requested : resolve(dist, 'index.html');
  res.setHeader('Content-Type', mime[extname(file)] || 'application/octet-stream'); res.end(readFileSync(file));
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });
const dict = JSON.parse(readFileSync('src/i18n/messages.en.json', 'utf8'));
const t = key => dict[key] || key;
const image = readFileSync('public/brand/lorely-app-icon-light.webp');
let assertions = 0;
async function setup(width, height, locale = 'en') {
  const page = await browser.newPage({ viewport: { width, height } });
  const errors = [], writes = [], reads = [];
  // Admitted catalog from image-consistency-sdk-result; no retired or invented editable routes.
  // The real-backend probe compares selectors against the actual SDK-backed settings catalog.
  const settings = { enabled: false, llm_model_option_key: null, image_provider_account_id: null, image_model_id: null, mcp_enabled: false, llm_accounts: [{ id: 'a', alias: 'Registered OpenAI', provider_type: 'openai' }, { id: 'v', alias: 'Registered Vertex', provider_type: 'vertex' }, { id: 'a2', alias: 'Second OpenAI', provider_type: 'openai' }], llm_options: [{ key: 'agent-llm', label: 'Synthetic independent LLM', provider_account_id: 'a', provider_type: 'openai' }, { key: 'vertex-model', label: 'Synthetic Vertex', provider_account_id: 'v', provider_type: 'vertex' }, { key: 'second-openai', label: 'Synthetic second account', provider_account_id: 'a2', provider_type: 'openai' }], image_accounts: [{ id: 'a', alias: 'Synthetic images', provider_type: 'openai' }, { id: 'x', alias: 'Synthetic xAI images', provider_type: 'xai' }], image_models: [...['gpt-image-2.5-sunburst', 'gpt-image-1.5', 'gpt-image-1-mini'].map(id => ({ id, label: id === 'gpt-image-2.5-sunburst' ? id : `${id} (deprecated; shuts down 2026-12-01)`, provider_type: 'openai', supports_reference: true, deprecated: id !== 'gpt-image-2.5-sunburst', shutdown_date: id === 'gpt-image-2.5-sunburst' ? null : '2026-12-01' })), { id: 'grok-imagine-image', label: 'grok-imagine-image', provider_type: 'xai', supports_reference: false }] };
  const state = { settings, clients: [], sessions: [], drafts: {}, operations: {}, jobs: {}, abortMessage: false, unavailable: false, writes, reads, errors };
  const sessionView = (id = 's1') => ({ image_contexts: [], ...state.sessions.find(s => s.id === id), drafts: Object.values(state.drafts).filter(d => d.session_id === id) });
  function makeDraft(id, kind, after, session_id = 's1') { if (kind === 'image_generation') after = { generation_mode: 'new', owner_choice_confirmed: false, reference: null, image_context: { has_existing_images: false, requires_choice: false, candidates: [], inventory_digest: 'empty' }, ...after }; return state.drafts[id] = { id, session_id, kind, target_id: 'c1', version: 1, base_revision: 1, before: kind === 'character' ? { name: 'Before' } : null, after, changes: after, preview_hash: `hash-${id}-1`, status: 'draft', warnings: [], result: null }; }
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(locale => { localStorage.setItem('lorechat-language', locale); window.agentBlobLifecycle = { made: [], revoked: [] }; const make = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL); URL.createObjectURL = blob => { const url = make(blob); window.agentBlobLifecycle.made.push(url); return url; }; URL.revokeObjectURL = url => { window.agentBlobLifecycle.revoked.push(url); revoke(url); }; }, locale);
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    if (url.origin !== origin) return route.abort();
    if (!path.startsWith('/api/')) return ['GET','HEAD'].includes(method) ? route.continue() : route.abort();
    const json = (value, status = 200) => route.fulfill({ json: value, status });
    const body = method === 'GET' || method === 'DELETE' ? null : req.postDataJSON();
    if (path.startsWith('/api/lore-agent')) {
      if (method === 'GET') reads.push(path); else writes.push({ path, method, body });
      const suffix = path.slice('/api/lore-agent'.length);
      assert.equal(req.headers()['x-lore-owner-key'], undefined, 'private REST never sends an owner key');
      assert.equal(req.headers().authorization, undefined, 'private REST does not invent replacement auth');
      assert.equal(url.search, '', 'private REST never puts credentials in URLs');
      assert.equal(req.headers()['cache-control']?.includes('no-store'), true, 'private REST bypasses cache');
      if (suffix === '/settings' && method === 'GET') return state.unavailable ? json({},404) : json(settings);
      if (suffix === '/settings') { Object.assign(settings, body); return json(settings); }
      if (suffix === '/mcp/clients') {
        if (method === 'POST') { const client = { id: 'client1', enabled: true, ...body }; state.clients.push(client); return json({ client, token: 'SYNTHETIC-MCP-ONCE' }); }
        return json(state.clients);
      }
      if (suffix === '/mcp/clients/client1' && method === 'DELETE') { state.clients[0].enabled = false; return route.fulfill({ status: 204 }); }
      if (body?.request_id) {
        const persisted = await page.evaluate(() => sessionStorage.getItem('lore-agent-request-recovery-v1'));
        assert.ok(persisted.includes(body.request_id), 'request ID is stored before POST');
        assert.ok(!persisted.includes('Synthetic request'), 'journal contains identifiers only');
      }
      if (suffix === '/sessions') {
        if (method === 'POST') { state.sessions = [{ id: 's1', title: 'Synthetic session', messages: [], busy: false, last_error: null }]; state.operations[body.request_id] = { request_id: body.request_id, status: 'completed', kind: 'session', result: { id: 's1' }, error: null }; return json(sessionView()); }
        return json(state.sessions.map(s => sessionView(s.id)));
      }
      if (/^\/sessions\/[^/]+$/.test(suffix)) return json(sessionView(suffix.split('/').at(-1)));
      if (suffix === '/sessions/s1/messages') {
        if (state.inFlightContext) {
          state.sessions[0].messages.push({id:'accepted',role:'user',content:body.content});
          state.sessions[0].busy = true;
          state.sessions[0].image_contexts = [state.inFlightContext];
          state.operations[body.request_id] = {request_id:body.request_id,status:'running',kind:'agent_turn',progress:{stage:'image_context',sequence:1},result:null,error:null};
          await new Promise(resolve => { state.releaseMessage = resolve; });
          state.sessions[0].busy = false;
          state.operations[body.request_id].status = 'completed';
          return json(sessionView());
        }
        if (state.delayMessage) await new Promise(resolve => setTimeout(resolve, 400));
        assert.deepEqual(body.context, state.expectedContext || [], 'only explicitly selected fields are attached');
        state.operations[body.request_id] = { request_id: body.request_id, status: state.abortMessage ? 'unknown' : 'completed', kind: 'agent_turn', result: { session_id: 's1' }, error: null };
        if (state.abortMessage) return route.abort();
        state.sessions[0].messages.push({ id: 'u1', role: 'user', content: body.content }, { id: 'a1', role: 'assistant', content: 'Synthetic preview, not applied.' });
        makeDraft('d1', 'character', { name: 'After' });
        // A restored committed native-form proposal supplies image registration targets.
        Object.assign(makeDraft('world-create', 'world', { title: 'Newly committed world' }), { status: 'committed', result: { id: 'w-new', kind: 'world' } });
        makeDraft('world-plan', 'image_generation', { target_kind: 'world', target_id: 'w-new', provider_account_id: 'a', model_id: 'gpt-image-1.5', prompt: 'Synthetic world', count: 2 });
        makeDraft('image-plan', 'image_generation', { provider_account_id: 'a', model_id: 'gpt-image-1.5', prompt: 'Synthetic image prompt', count: 1, options: {}, reference_output_id: null, target_id: 'c1' });
        const image_context = { has_existing_images: true, requires_choice: true, inventory_digest: 'fixture-inventory', candidates: [{ id: 'avatar', label: 'Current portrait', kind: 'avatar', is_default: true, available: true }, { id: 'asset', label: 'Blue outfit', kind: 'asset', is_default: false, available: true }, { id: 'remote', label: 'Remote portrait', kind: 'avatar', is_default: false, available: false }] };
        makeDraft('reference-plan', 'image_generation', { target_kind: 'character', target_id: 'c1', provider_account_id: 'a', model_id: 'gpt-image-1.5', prompt: 'Keep identity, new pose', count: 1, generation_mode: 'undecided', image_context });
        makeDraft('unsupported-plan', 'image_generation', { target_kind: 'character', target_id: 'c1', provider_account_id: 'x', model_id: 'grok-imagine-image', prompt: 'Fresh design', count: 1, generation_mode: 'undecided', image_context });
        return json(sessionView());
      }
      if (suffix.includes('/references/')) {
        if (suffix.endsWith('/broken')) return json({},404);
        if (state.holdThumbnails) await new Promise(resolve => { (state.thumbnailReleases ||= []).push(resolve); });
        return route.fulfill({ body: image, contentType: 'image/webp' });
      }
      if (suffix.startsWith('/operations/')) {
        if (state.holdRecovery && suffix.endsWith('/paid-unknown')) await new Promise(resolve => { state.releaseRecovery = resolve; });
        return json(state.operations[suffix.split('/').at(-1)] || { status: 'unknown', request_id: suffix.split('/').at(-1), kind: 'unknown', result: null, error: null });
      }
      if (suffix.startsWith('/drafts/')) {
        const [, , id, action] = suffix.split('/'); const draft = state.drafts[id];
        if (state.holdDraft === id) await new Promise(resolve => { state.releaseDraft = resolve; });
        if (state.failDraft === id) return json({}, 503);
        if (!draft || state.missingDraft === id) return json({},404);
        if (action === 'reference') {
          if (state.holdThumbnails) await new Promise(resolve => { (state.thumbnailReleases ||= []).push(resolve); });
          return route.fulfill({ body: image, contentType: 'image/webp' });
        }
        if (action === 'image-choice') { assert.equal(body.expected_version, draft.version); draft.version++; draft.status = 'draft'; draft.after.generation_mode = body.generation_mode; draft.after.owner_choice_confirmed = true; draft.after.reference = body.generation_mode === 'reference' ? { source_id: body.reference_id, snapshot_id: 'snapshot-' + draft.version, sha256: 'fixture-digest', mime_type: 'image/webp' } : null; draft.preview_hash = `hash-${id}-${draft.version}`; }
        if (action === 'revise') { assert.equal(body.expected_version, draft.version); draft.version++; draft.after = { ...draft.after, ...body.fields }; if (draft.kind === 'image_generation' && draft.after.image_context.has_existing_images) { draft.after.generation_mode = 'undecided'; draft.after.owner_choice_confirmed = false; draft.after.reference = null; } draft.preview_hash = `hash-${id}-${draft.version}`; draft.status = 'draft'; }
        if (action === 'approve' || action === 'commit') { assert.equal(body.expected_version, draft.version); assert.equal(body.preview_hash, draft.preview_hash); }
        if (action === 'approve') draft.status = 'approved';
        if (action === 'commit') {
          assert.equal(draft.status, 'approved', 'no commit without owner approval'); draft.status = 'committed';
          draft.result = draft.kind === 'image_generation' ? { job_id: id === 'world-plan' ? 'jw' : 'j1' } : { id: draft.kind === 'world' ? 'w-new' : 'c1', kind: draft.kind };
          if (id === 'world-plan') state.jobs.jw = { id: 'jw', plan: draft.after, status: 'completed', outputs: [{ id: 'ow1' }, { id: 'ow2' }], error: null };
          else if (draft.kind === 'image_generation') state.jobs.j1 = { id: 'j1', plan: draft.after, status: 'completed', outputs: [{ id: 'o1', preview_url: '/api/lore-agent/image-outputs/o1' }], error: null };
          const op = state.operations[body.request_id] = { request_id: body.request_id, status: 'completed', kind: draft.kind, result: draft.result, error: null }; return json(op);
        }
        return json(draft);
      }
      if (method === 'GET' && /^\/image-jobs\/[^/]+$/.test(suffix) && state.jobs[suffix.split('/').at(-1)]) return json(state.jobs[suffix.split('/').at(-1)]);
      if (suffix === '/image-jobs/jw') return json(state.jobs.jw);
      if (suffix === '/image-jobs/jw/registration') {
        assert.equal(body.target_kind, 'world'); assert.equal(body.target_id, 'w-new'); assert.deepEqual(body.output_ids, ['ow2']); assert.deepEqual(body.metadata, {});
        const draft = makeDraft('world-registration', 'image_registration', { ...body }, null);
        return json(draft);
      }
      if (suffix === '/image-jobs/j1') return json(state.jobs.j1);
      if (suffix.startsWith('/image-outputs/')) return route.fulfill({ body: image, contentType: 'image/webp' });
      if (suffix === '/image-jobs/j1/registration') {
        assert.deepEqual(body.output_ids, ['o1']); assert.equal(body.target_id, 'c1'); assert.equal(body.target_kind, 'character');
        const draft = makeDraft('registration', 'image_registration', { output_ids: ['o1'], target_id: 'c1', metadata: body.metadata }, null);
        state.operations[body.request_id] = { request_id: body.request_id, status: 'completed', kind: 'stage_image_registration', result: { draft_id: draft.id }, error: null }; return json(draft);
      }
      throw new Error(`Unexpected Agent fixture request: ${method} ${suffix}`);
    }
    if (path === '/api/images/prepare') return json({ image_url: '/api/images/cache/synthetic.webp' });
    if (path.startsWith('/api/images/cache/')) return route.fulfill({ body: image, contentType: 'image/webp' });
    if (path.endsWith('/read') && method === 'POST') return json({});
    assert.equal(method, 'GET', `No unrelated write allowed: ${path}`);
    if (path === '/api/conversations/A') return json({ id: 'A', title: 'Synthetic room', genre_mode: 'romance', mode: 'multi_character', tts_enabled: false });
    if (path.endsWith('/messages')) return json(Array.from({ length: 40 }, (_, i) => ({ id: `m${i}`, conversation_id: 'A', speaker_type: 'user', content: `Synthetic history ${i}. `.repeat(15), created_at: '2026-11-14T00:00:00Z' })));
    if (path.endsWith('/participants')) return json([{ type: 'user', id: 'user' }, { type: 'character', id: 'c1' }]);
    if (path.includes('/generation-jobs/active')) return json({ jobs: [] });
    if (path.endsWith('/context')) return json({ scene: { summary: 'Synthetic story' }, memories: [] });
    if (path === '/api/conversations') return json([{ id: 'A', title: 'Synthetic room' }]);
    if (path === '/api/world-settings') return json([{ id: 'w1', name: 'Synthetic world' }]);
    if (path === '/api/characters') return json([{ id: 'c1', name: 'Synthetic character', persona: 'Synthetic' }]);
    if (path.startsWith('/api/runtime-settings')) return json({});
    if (path === '/api/health' || path === '/api/ready') return json({ status: 'ok' });
    return json([]);
  });
  const navigate = async path => { await page.evaluate(path => { history.pushState({}, '', path); dispatchEvent(new PopStateEvent('popstate')); }, path); };
  return { page, state, navigate };
}
async function verifyConversation(page, state, panel, local, width) {
  const markdown = '# A readable answer\n\n**Bold choice** and *emphasis* with `inline code`.\n\n- First item\n- Second item\n\n1. Ordered item\n\n> A quoted note\n\n```js\n' + 'const longToken = "' + 'x'.repeat(220) + '";\n```\n\n[Safe link](https://example.com) [Unsafe](javascript:alert(1))\n\n![Remote image](https://example.com/tracker.png)\n\n<script>window.agentUnsafe = true</script>\n<img src="https://example.com/raw.png" onerror="alert(1)">';
  state.sessions[0].messages = [...Array.from({ length: 24 }, (_, i) => ({ id: `long-${i}`, role: i % 2 ? 'assistant' : 'user', content: i % 2 ? 'A useful answer with room to read. '.repeat(7) : 'A short question' })), { id: 'markdown', role: 'assistant', content: markdown }];
  await panel.getByRole('button', { name: local['닫기'] || '닫기', exact: true }).click();
  await page.locator('.agent-launcher').click();
  const rich = panel.locator('.agent-markdown').last();
  await expect(rich.locator('h1')).toHaveText('A readable answer');
  await expect(rich.locator('strong')).toHaveText('Bold choice');
  await expect(rich.locator('ul li')).toHaveCount(2);
  await expect(rich.locator('ol li')).toHaveCount(1);
  await expect(rich.locator('blockquote')).toHaveText('A quoted note');
  await expect(rich.locator('pre code')).toHaveCount(1);
  assert.equal(await rich.locator('pre').evaluate(node => {
    node.scrollLeft = 80;
    return getComputedStyle(node).overflowX === 'auto' && node.scrollWidth > node.clientWidth && node.scrollLeft > 0;
  }), true, 'long code is independently horizontally scrollable, not lost or page-overflowing');
  await rich.locator('pre').evaluate(node => { node.scrollLeft = 0; });
  await expect(rich.locator('a')).toHaveCount(1);
  await expect(rich.locator('a')).toHaveAttribute('rel', 'noopener noreferrer');
  await expect(rich.locator('img,script,iframe')).toHaveCount(0);
  assert.equal(await page.evaluate(() => window.agentUnsafe), undefined);
  const composer = panel.locator('.agent-composer');
  const transcript = panel.locator('.agent-transcript');
  await transcript.evaluate(node => { node.scrollTop = node.scrollHeight; });
  const box = await composer.boundingBox();
  assert.ok(box.y >= 0 && box.y + box.height <= (await page.viewportSize()).height, 'bottom composer stays visible through long transcript');
  assert.ok(await transcript.evaluate(node => node.scrollHeight > node.clientHeight));
  assert.ok(await transcript.evaluate(node => node.scrollWidth <= node.clientWidth + 1), 'long fenced code stays within transcript');
  const avatar = await panel.locator('.agent-message-avatar').last().boundingBox();
  assert.ok(avatar.width >= 24 && avatar.width <= 30 && avatar.height >= 24, 'recognizable small Agent mark');
  await rich.locator('h1').scrollIntoViewIfNeeded();
  if (artifacts) await page.screenshot({ path: `${artifacts}/conversation-${width}.png` });
  await panel.getByLabel(local['agent.message']).fill('Retained conversation draft');
  await transcript.evaluate(node => { node.scrollTop = 100; node.dispatchEvent(new Event('scroll')); });
  state.sessions[0].messages.push({ id: 'new-while-reading', role: 'assistant', content: 'New answer while reading above.' });
  await panel.getByRole('button', { name: local['닫기'] || '닫기', exact: true }).click();
  await page.locator('.agent-launcher').click();
  await expect(panel.locator('.agent-messages')).toContainText('New answer while reading above.');
  assert.ok(Math.abs(await transcript.evaluate(node => node.scrollTop) - 100) < 4, 'refresh preserves manual upward reading');
  await expect(panel.getByLabel(local['agent.message'])).toHaveValue('Retained conversation draft');
  if (width === 390) {
    const viewport = page.viewportSize();
    await page.setViewportSize({ width: 1440, height: viewport.height });
    const dock = page.locator('.agent-dock:visible');
    await expect(dock.getByLabel(local['agent.message'])).toHaveValue('Retained conversation draft');
    assert.ok(Math.abs(await dock.locator('.agent-transcript').evaluate(node => node.scrollTop) - 100) < 4, 'dock transition retains upward reading');
    await page.setViewportSize(viewport);
    await expect(panel.getByLabel(local['agent.message'])).toHaveValue('Retained conversation draft');
    assert.ok(Math.abs(await transcript.evaluate(node => node.scrollTop) - 100) < 4, 'modal transition retains upward reading');
  }
  const advanced = panel.locator('.agent-advanced');
  if (!await advanced.evaluate(node => node.open)) await panel.getByText(local['agent.chatAdvanced'], { exact: true }).click();
  await transcript.evaluate(node => { node.scrollTop = node.scrollHeight; node.dispatchEvent(new Event('scroll')); });
  state.sessions[0].messages.push({ id: 'new-at-bottom', role: 'assistant', content: 'Follow this new answer at the bottom.' });
  await panel.locator('.agent-advanced').getByRole('button', { name: local['agent.refresh'], exact: true }).click();
  await expect(panel.locator('.agent-messages')).toContainText('Follow this new answer at the bottom.');
  await expect.poll(() => transcript.evaluate(node => node.scrollHeight - node.clientHeight - node.scrollTop)).toBeLessThan(4);
  await panel.getByText(local['agent.chatAdvanced'], { exact: true }).click();
}
async function verifyImageFeedback(width, height, locale) {
  const { page, state } = await setup(width, height, locale);
  const local = JSON.parse(readFileSync(`src/i18n/messages.${locale}.json`, 'utf8'));
  Object.assign(state.settings, { enabled: true, llm_model_option_key: 'agent-llm' });
  const item = { target_kind: 'character', target_id: 'c1', target_label: 'Synthetic portrait subject', context: { has_existing_images: true, requires_choice: true, inventory_digest: 'fixture', candidates: [
    { id: 'avatar', label: 'Portrait', kind: 'avatar', is_default: true, available: true },
    { id: 'unavailable', label: 'Unavailable asset', kind: 'asset', is_default: false, available: false },
    { id: 'broken', label: 'Missing file', kind: 'asset', is_default: false, available: true },
  ] } };
  state.sessions = [{id:'s1',title:'Image conversation',messages:[],image_contexts:[item],busy:false,last_error:null},{id:'s2',title:'Other conversation',messages:[],image_contexts:[],busy:false,last_error:null}];
  state.holdThumbnails = true;
  await page.goto(origin + '/settings'); await page.locator('.agent-launcher').click();
  const panel=page.locator('.agent-surface'), transcript=panel.locator('.agent-transcript');
  await panel.getByText(local['agent.chatAdvanced'],{exact:true}).click();
  await panel.getByText(local['agent.history'],{exact:true}).click();
  await panel.getByLabel(local['agent.sessions']).selectOption('s1');
  await panel.getByText(local['agent.chatAdvanced'],{exact:true}).click();
  const card=panel.locator('[data-agent-image-context=c1]');
  await expect(card).toHaveCount(1);
  await expect(card.getByText(local['agent.imageLoading'],{exact:true})).toBeVisible();
  await expect(card.getByText(local['agent.referenceUnavailable'],{exact:true})).toBeVisible();
  await expect(card.getByText(local['agent.imageFailed'],{exact:true})).toBeVisible();
  await expect(card.getByText(local['agent.imageNotGenerated'],{exact:true})).toBeVisible();
  assert.equal(state.writes.length,0);
  assert.ok(!state.reads.some(path=>path.endsWith('/unavailable')));
  state.holdThumbnails=false; for (const release of state.thumbnailReleases || []) release();
  await expect(card.locator('img')).toHaveCount(1);
  await expect.poll(()=>card.locator('img').evaluate(img=>img.complete && img.naturalWidth>0)).toBe(true);
  assert.ok((await card.locator('img').getAttribute('src')).startsWith('blob:'));
  await expect(card.getByText(local['agent.imageLoading'],{exact:true})).toHaveCount(0);
  const composer=await panel.locator('.agent-composer').boundingBox();
  assert.ok(composer.y>=0 && composer.y+composer.height<=height);
  assert.equal(await transcript.evaluate(n=>n.scrollWidth>n.clientWidth+1),false);
  if (artifacts) await page.screenshot({path:`${artifacts}/image-context-${width}.png`});
  // Session switching must never show another conversation's context.
  await panel.getByText(local['agent.chatAdvanced'],{exact:true}).click();
  await panel.getByLabel(local['agent.sessions']).selectOption('s2'); await expect(card).toHaveCount(0);
  await panel.getByLabel(local['agent.sessions']).selectOption('s1'); await expect(card).toHaveCount(1);
  await panel.getByText(local['agent.chatAdvanced'],{exact:true}).click();
  // Reset context via GET, then expose accepted message/context and plan during one held POST.
  state.sessions[0].image_contexts=[{target_kind:'world',target_id:'empty',target_label:'Empty world',context:{has_existing_images:false,requires_choice:false,inventory_digest:'empty',candidates:[]}}];
  await panel.getByRole('button',{name:local['닫기'] || '닫기',exact:true}).click(); await page.locator('.agent-launcher').click();
  await expect(panel.locator('[data-agent-image-context=empty]')).toContainText(local['agent.noExistingImages']);
  state.sessions[0].image_contexts=[];
  state.sessions[0].messages=Array.from({length:20},(_,i)=>({id:`history-${i}`,role:'assistant',content:'Synthetic history to verify reading position. '.repeat(8)}));
  await panel.getByRole('button',{name:local['닫기'] || '닫기',exact:true}).click(); await page.locator('.agent-launcher').click();
  await expect(card).toHaveCount(0);
  await transcript.evaluate(n=>{n.scrollTop=100;n.dispatchEvent(new Event('scroll'));});
  state.inFlightContext=item;
  await panel.getByLabel(local['agent.message']).fill('Synthetic in-flight image request');
  await panel.getByRole('button',{name:local['전송'] || '전송',exact:true}).click();
  await expect(panel.locator('.agent-messages')).toContainText('Synthetic in-flight image request',{timeout:8000});
  await expect(card).toHaveCount(1);
  await expect(card.getByText(local['agent.imagePlanPreparing'],{exact:true})).toBeVisible();
  assert.equal(state.writes.length,1,'only explicitly submitted message; no choice, approval or generation');
  assert.ok(Math.abs(await transcript.evaluate(n=>n.scrollTop)-100)<4,'context arrival preserves upward reading');
  await transcript.evaluate(n=>{n.scrollTop=n.scrollHeight;n.dispatchEvent(new Event('scroll'));});
  state.holdThumbnails=true;state.thumbnailReleases=[];
  state.drafts.plan={id:'plan',session_id:'s1',kind:'image_generation',target_id:'c1',version:1,base_revision:null,before:null,after:{target_kind:'character',target_id:'c1',provider_account_id:'a',model_id:'gpt-image-1.5',prompt:'Synthetic prompt',count:1,generation_mode:'undecided',image_context:item.context},changes:{},preview_hash:'fixture',status:'draft',warnings:[],result:null};
  await expect(panel.locator('[data-agent-draft=plan]')).toHaveCount(1,{timeout:8000});
  await expect(card).toHaveCount(0);
  await expect.poll(()=>state.thumbnailReleases.length).toBeGreaterThan(0);
  state.holdThumbnails=false;for(const release of state.thumbnailReleases) release();
  await expect.poll(()=>panel.locator('[data-agent-draft=plan] img').first().evaluate(img=>img.complete && img.naturalWidth>0)).toBe(true);
  await expect.poll(()=>transcript.evaluate(n=>n.scrollHeight-n.clientHeight-n.scrollTop)).toBeLessThan(4);
  await expect(panel.locator('[data-agent-draft=plan] input[type=radio]:checked')).toHaveCount(0);
  assert.equal(state.writes.length,1);
  state.releaseMessage();
  await expect(panel.getByLabel(local['agent.message'])).toHaveValue('');
  if (artifacts) await page.screenshot({path:`${artifacts}/image-plan-final-${width}.png`});
  await expect(panel.getByRole('button',{name:local['agent.newSession'],exact:true})).toBeEnabled();
  assert.equal(await transcript.evaluate(n=>n.scrollWidth>n.clientWidth+1),false);
  assert.deepEqual(state.errors,[]);
  console.log(`PASS image feedback ${locale} ${width}x${height}: context without draft, loading/unavailable/404, authenticated blob, session isolation, pending POST context/plan, no implicit writes, visible composer`);
  assertions++; await page.close();
}
async function verifyPendingRecovery() {
  const {page,state}=await setup(390,844);
  Object.assign(state.settings,{enabled:true,llm_model_option_key:'agent-llm'});
  state.sessions=[{id:'s1',title:'First',messages:[],busy:false},{id:'s2',title:'Second',messages:[],busy:false}];
  const paid={request_id:'paid-unknown',kind:'commit',session_id:'s1'};
  await page.addInitScript(p=>sessionStorage.setItem('lore-agent-request-recovery-v1',JSON.stringify([p])),paid);
  await page.goto(origin+'/settings');await page.locator('.agent-launcher').click();
  const panel=page.locator('.agent-surface'), composer=panel.locator('.agent-composer');
  const input=panel.getByLabel(t('agent.message')), send=composer.locator('button[type=submit]');
  const recover=composer.getByRole('button',{name:t('agent.refresh'),exact:true});
  await expect(recover).toBeEnabled();
  await panel.getByText(t('agent.chatAdvanced'),{exact:true}).click();
  await panel.getByText(t('agent.history'),{exact:true}).click();
  const history=panel.getByLabel(t('agent.sessions'));await expect(history).toBeEnabled();await history.selectOption('s1');
  await panel.getByText(t('agent.chatAdvanced'),{exact:true}).click();
  state.inFlightContext={target_kind:'world',target_id:'empty',target_label:'Empty',context:{candidates:[],has_existing_images:false}};
  await input.fill('Explicit new text despite unknown image');await expect(send).toBeEnabled();await send.click();
  await expect.poll(()=>typeof state.releaseMessage).toBe('function');
  state.holdRecovery=true;await expect(recover).toBeEnabled();await recover.click();
  await expect.poll(()=>typeof state.releaseRecovery).toBe('function');await expect(recover).toBeDisabled();
  await expect(send).toBeDisabled();
  await panel.getByText(t('agent.chatAdvanced'),{exact:true}).click();await expect(history).toBeEnabled();await history.selectOption('s2');
  await panel.getByText(t('agent.chatAdvanced'),{exact:true}).click();
  await input.fill('Other conversation draft');
  state.holdRecovery=false;state.releaseRecovery();await expect(recover).toBeEnabled();await expect(send).toBeDisabled();
  assert.equal(state.writes.length,1,'GET recovery never replays POST');
  state.releaseMessage();await expect(send).toBeEnabled();await expect(input).toHaveValue('Other conversation draft');
  await panel.getByText(t('agent.chatAdvanced'),{exact:true}).click();await expect(history).toHaveValue('s2');
  await panel.getByText(t('agent.chatAdvanced'),{exact:true}).click();
  assert.deepEqual(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('lore-agent-request-recovery-v1'))),[paid]);
  state.inFlightContext=null;await panel.getByRole('button',{name:t('agent.newSession'),exact:true}).click();
  await input.fill('Explicit fresh conversation');await send.click();
  await expect(input).toHaveValue('');
  assert.equal(state.writes.length,3,'only explicit message plus fresh session/message');
  assert.deepEqual(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('lore-agent-request-recovery-v1'))),[paid]);
  assert.ok(state.writes.every(w=>w.path.endsWith('/messages') || w.path.endsWith('/sessions')));
  await expect(panel.locator('[data-agent-draft=image-plan]').getByRole('button',{name:t('agent.generatePaid'),exact:true})).toHaveCount(0);
  assert.deepEqual(state.errors,[]);
  if(artifacts) await page.screenshot({path:`${artifacts}/pending-image-recovery-390.png`});
  console.log('PASS pending image recovery 390: usable history under POST, independent slow GET singleflight, retained unknown journal, new text/new chat, stale POST composer fence, no paid/replayed POST');
  assertions++;await page.close();
}
async function verifyRecoveryNavigation(width, height, locale) {
  const { page, state } = await setup(width, height, locale);
  const local = JSON.parse(readFileSync(`src/i18n/messages.${locale}.json`, 'utf8'));
  Object.assign(state.settings, { enabled: true, llm_model_option_key: 'agent-llm' });
  state.sessions = [{ id: 's2', title: 'Unrelated history', messages: [], busy: false }, { id: 's1', title: 'Named portrait conversation', messages: Array.from({ length: 30 }, (_, i) => ({ id: `r-${i}`, role: 'assistant', content: 'Synthetic long conversation. '.repeat(12) })), busy: false }];
  const plan = { id: 'recovery-plan', session_id: 's1', kind: 'image_generation', target_id: 'c1', version: 1, base_revision: null, before: null, after: { target_kind: 'character', target_id: 'c1', provider_account_id: 'a', model_id: 'gpt-image-1.5', prompt: 'Synthetic portrait', count: 1, generation_mode: 'new', owner_choice_confirmed: true, image_context: { has_existing_images: false, requires_choice: false, candidates: [] } }, changes: {}, preview_hash: 'synthetic', status: 'committed', warnings: [], result: { job_id: 'recovery-job' } };
  plan.after.generation_mode = 'reference';
  plan.after.reference = { source_id: 'avatar', snapshot_id: 'snapshot', sha256: 'synthetic', mime_type: 'image/webp' };
  plan.after.image_context = { has_existing_images: true, requires_choice: true, candidates: Array.from({ length: 4 }, (_, i) => ({ id: i ? `asset-${i}` : 'avatar', label: `Synthetic reference ${i}`, kind: 'asset', available: true })) };
  state.holdThumbnails = true;
  state.drafts[plan.id] = plan;
  state.jobs['recovery-job'] = { id: 'recovery-job', plan: plan.after, status: 'unknown', outputs: [], error: 'Raw provider uncertainty' };
  // A trailing completed result prevents a false pass from merely going to bottom.
  state.drafts.trailing = { ...plan, id: 'trailing', result: { job_id: 'trailing-job' } };
  state.jobs['trailing-job'] = { id: 'trailing-job', plan: plan.after, status: 'completed', outputs: Array.from({ length: 4 }, (_, i) => ({ id: `trailing-${i}` })), error: null };
  const journal = [{ request_id: 'recovery-request', kind: 'commit', draft_id: plan.id, ...(width === 390 ? {} : { session_id: 's1' }) }];
  state.operations['recovery-request'] = { request_id: 'recovery-request', kind: 'image_generation', status: 'unknown', result: { draft_id: plan.id, job_id: 'recovery-job' }, error: 'Raw provider uncertainty' };
  await page.addInitScript(journal => sessionStorage.setItem('lore-agent-request-recovery-v1', JSON.stringify(journal)), journal);
  await page.goto(origin + '/settings'); await page.locator('.agent-launcher').click();
  const panel = page.locator('.agent-surface'), notice = panel.locator('.agent-recovery-notice'), transcript = panel.locator('.agent-transcript');
  const input = panel.getByLabel(local['agent.message']);
  const refresh = panel.locator('.agent-composer').getByRole('button', { name: local['agent.refresh'], exact: true });
  const view = () => notice.getByRole('button');
  const job = panel.locator('[data-agent-image-job=recovery-job]');
  const advanced = panel.getByText(local['agent.chatAdvanced'], { exact: true });
  const history = panel.getByLabel(local['agent.sessions']);
  await expect(refresh).toBeEnabled();
  await expect(notice).toContainText('Named portrait conversation');
  await expect(view()).toHaveText(local['agent.viewImageRequest']);
  await expect(view()).toBeInViewport();
  await expect(panel.locator('.agent-advanced')).not.toHaveAttribute('open');
  await expect(job).toHaveCount(0);
  await input.fill('Keep my unsent composer');
  await expect(panel.locator('.agent-composer button[type=submit]')).toBeEnabled();
  if (artifacts) await page.screenshot({ path: `${artifacts}/recovery-notice-${width}.png` });
  await view().click();
  await expect(job).toBeFocused(); await expect(job.locator('h3')).toBeInViewport();
  await expect(panel.locator('[data-agent-draft=recovery-plan] h3')).toContainText(local['agent.imageRequestSubmitted']);
  await expect(panel.locator('[data-agent-draft=recovery-plan] h3')).not.toContainText(local['agent.status.committed']);
  await expect(job).toContainText(local['agent.imageRequestNoOutput']); await expect(job.locator('img')).toHaveCount(0);
  await expect(panel.getByText('Raw provider uncertainty', { exact: true })).toHaveCount(0);
  await expect(input).toHaveValue('Keep my unsent composer');
  await expect(history).toHaveValue('s1');
  await expect.poll(() => transcript.evaluate(n => n.scrollHeight - n.scrollTop - n.clientHeight)).toBeGreaterThan(100);
  // Real late reference/snapshot decoding above the target must retain its anchor.
  await expect.poll(() => state.thumbnailReleases?.length || 0).toBeGreaterThan(0);
  const anchorTop = await job.evaluate(n => n.getBoundingClientRect().top);
  const priorHeight = await transcript.evaluate(n => n.scrollHeight);
  state.holdThumbnails = false; for (const release of state.thumbnailReleases) release();
  await expect.poll(() => panel.locator('[data-agent-draft=recovery-plan] img').evaluateAll(nodes => nodes.length === 5 && nodes.every(n => n.complete && n.naturalWidth > 0))).toBe(true);
  await expect.poll(() => transcript.evaluate(n => n.scrollHeight)).toBeGreaterThan(priorHeight);
  await expect.poll(() => job.evaluate(n => n.getBoundingClientRect().top)).toBeCloseTo(anchorTop, 0);
  await expect(job).toBeFocused();
  await expect(job.locator('h3')).toBeInViewport();
  if (artifacts) await page.screenshot({ path: `${artifacts}/recovery-focus-${width}.png` });
  // Same-session explicit navigation restores focus and position after manual reading.
  await transcript.evaluate(n => { n.dispatchEvent(new WheelEvent('wheel', { bubbles: true, deltaY: -100 })); n.scrollTop = 100; n.dispatchEvent(new Event('scroll')); });
  await view().click(); await expect(job).toBeFocused(); await expect(job.locator('h3')).toBeInViewport();
  // Background readback retains manual reading, no repeat focus/navigation.
  await transcript.evaluate(n => { n.dispatchEvent(new WheelEvent('wheel', { bubbles: true, deltaY: -100 })); n.scrollTop = 100; n.dispatchEvent(new Event('scroll')); });
  await refresh.click(); await expect(refresh).toBeEnabled();
  assert.ok(Math.abs(await transcript.evaluate(n => n.scrollTop) - 100) < 4);
  await advanced.click(); await panel.getByText(local['agent.history'], { exact: true }).click();
  await history.selectOption('s2'); await advanced.click(); await expect(job).toHaveCount(0);
  await view().click(); await expect(history).toHaveValue('s1'); await expect(job).toBeFocused();
  // A held GET belongs to its explicit selection epoch, not a later history view.
  state.holdDraft = plan.id; await view().click();
  await expect.poll(() => typeof state.releaseDraft).toBe('function'); await expect(view()).toBeDisabled();
  await advanced.click(); await history.selectOption('s2'); await advanced.click();
  state.holdDraft = null; state.releaseDraft(); await expect(view()).toBeEnabled();
  await expect(history).toHaveValue('s2'); await expect(job).toHaveCount(0);
  // Missing and failed GETs show a live retry without changing conversation.
  for (const failure of ['missingDraft', 'failDraft']) {
    await advanced.click(); await history.selectOption('s2'); await advanced.click();
    state[failure] = plan.id; await view().click();
    await expect(notice.getByRole('alert')).toHaveText(local['agent.recoveryFailed']);
    await expect(view()).toHaveText(local['agent.recoveryRetry']); await expect(history).toHaveValue('s2');
    state[failure] = null; await view().click(); await expect(job).toBeFocused();
    await expect(notice.getByRole('alert')).toHaveCount(0);
    await panel.getByRole('button', { name: local['agent.newSession'], exact: true }).click();
    await expect(history).toHaveValue(''); await expect(job).toHaveCount(0);
    await expect(panel.locator('.agent-composer button[type=submit]')).toBeEnabled();
  }
  assert.deepEqual(await page.evaluate(() => JSON.parse(sessionStorage.getItem('lore-agent-request-recovery-v1'))), journal);
  assert.equal(state.writes.length, 0, 'all request navigation, retry, refresh and new-chat actions are GET/local only');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.deepEqual(state.errors, []);
  console.log(`PASS recovery navigation ${locale} ${width}: visible notice from blank/other history, exact named session, long-history target focus not bottom, same-session focus, old journal, 404/503 retry, stale selection fence, preserved composer/journal, zero writes`);
  assertions++; await page.close();
}
async function saveAgentSettings(page, label) {
  const save = page.getByRole('button', { name: label, exact: true });
  await expect(save).toHaveCount(1);
  assert.equal(await save.evaluate(button => !!button.form?.closest('[data-page="agent-settings"]')), true, 'save action belongs to the Agent settings form');
  await save.click();
}
try {
  for (const profile of [[390,844,'ko'], [820,1180,'en']]) await verifyRecoveryNavigation(...profile);
  await verifyPendingRecovery();
  for (const profile of (process.env.AGENT_IMAGE_ONLY ? [[390,844,'ko'],[820,1180,'ja']] : [[320,740,'ko'],[390,844,'en'],[820,1180,'ja'],[1440,1000,'en']])) await verifyImageFeedback(...profile);
  for (const [width,height] of (process.env.AGENT_IMAGE_ONLY ? [] : [[390,844],[1440,1000]])) {
    const { page, state, navigate } = await setup(width,height);
    await page.goto(origin + '/settings/lore-agent');
    const settings = page.locator('[data-page=agent-settings]');
    await expect(settings.locator('input[type=password], .agent-unlock')).toHaveCount(0);
    await expect(settings.getByRole('button', { name: /^(Unlock|Lock)$/ })).toHaveCount(0);
    await expect(settings.getByRole('switch', { name: t('agent.enable') })).not.toBeChecked();
    await expect(settings.getByRole('switch', { name: t('agent.mcpEnable') })).not.toBeChecked();
    await expect(settings.getByLabel(t('agent.llm'), { exact: true })).toHaveValue('');
    await expect(settings.getByLabel(t('agent.imageAccount'))).toHaveValue('');
    await expect(settings.getByLabel(t('agent.imageModel'))).toHaveValue('');
    assert.equal(state.writes.length, 0, 'opening settings never enables features or sends model calls');
    assert.ok(!state.reads.includes('/api/lore-agent/sessions'), 'OFF does not read sessions');
    await expect(settings.getByRole('switch', { name: t('agent.enable') })).toBeEnabled();
    await settings.getByRole('switch', { name: t('agent.enable') }).focus(); await page.keyboard.press('Space');
    await expect(settings.getByRole('button', { name: t('agent.open'), exact: true })).toHaveCount(0);
    await settings.getByLabel(t('agent.llmAccount'), { exact: true }).selectOption('a');
    await settings.getByLabel(t('agent.llm'), { exact: true }).selectOption('agent-llm');
    await settings.getByLabel(t('agent.imageAccount')).selectOption('a');
    await expect(settings.getByLabel(t('agent.imageModel'))).toHaveValue('');
    assert.deepEqual(await settings.getByLabel(t('agent.imageModel')).locator('option').evaluateAll(nodes => nodes.map(n => n.value)), ['', 'gpt-image-2.5-sunburst', 'gpt-image-1.5', 'gpt-image-1-mini']);
    await settings.getByLabel(t('agent.imageModel')).selectOption('gpt-image-1.5');
    await settings.getByLabel(t('agent.imageAccount')).selectOption('x');
    await expect(settings.getByLabel(t('agent.imageModel'))).toHaveValue('');
    assert.deepEqual(await settings.getByLabel(t('agent.imageModel')).locator('option').evaluateAll(nodes => nodes.map(n => n.value)), ['', 'grok-imagine-image']);
    await settings.getByLabel(t('agent.imageModel')).selectOption('grok-imagine-image');
    await settings.getByLabel(t('agent.imageAccount')).selectOption('a');
    await expect(settings.getByLabel(t('agent.imageModel'))).toHaveValue('');
    await settings.getByLabel(t('agent.imageModel')).selectOption('gpt-image-1.5');
    await settings.getByRole('switch', { name: t('agent.mcpEnable') }).focus(); await page.keyboard.press('Space');
    await saveAgentSettings(page, t('저장'));
    await expect(settings.getByText(t('agent.saved'))).toBeVisible();
    assert.equal(state.settings.llm_model_option_key, 'agent-llm');
    assert.equal(state.settings.image_model_id, 'gpt-image-1.5');
    await expect(settings.getByLabel('lore:read', { exact: true })).toBeChecked();
    await expect(settings.getByLabel('lore:read', { exact: true })).toBeDisabled();
    await settings.getByLabel(t('agent.clientName')).fill('Synthetic external client');
    await settings.getByRole('button', { name: t('agent.createClient') }).click();
    await expect(settings.getByLabel(t('agent.clientToken'))).toHaveValue('SYNTHETIC-MCP-ONCE');
    await settings.getByRole('button', { name: t('agent.dismissToken') }).click();
    await expect(settings.getByLabel(t('agent.clientToken'))).toHaveCount(0);
    await settings.getByRole('button', { name: t('agent.revoke'), exact: true }).click();
    await expect(settings.getByRole('button', { name: t('agent.revoke'), exact: true })).toBeDisabled();
    await navigate('/conversations/A');
    const composer = page.locator('.composer textarea');
    await expect(composer).toBeVisible();
    await composer.fill('ROLEPLAY DRAFT RETAINED'); const original = await composer.elementHandle();
    await page.locator('.chat-thread').evaluate(node => { node.scrollTop = 150; });
    const beforeScroll = await page.locator('.chat-thread').evaluate(node => node.scrollTop);
    await page.locator('.agent-launcher').click();
    const panel = page.locator('.agent-surface');
    await expect(panel).toBeVisible();
    const launcherBox = await page.locator('.agent-launcher').boundingBox();
    assert.ok(launcherBox.width >= 44 && launcherBox.height >= 44, 'launcher hit target');
    await expect(page.locator('.agent-overlay')).toHaveCount(0);
    await page.locator('.agent-launcher').focus();
    await expect(page.locator('.agent-launcher')).toBeFocused();
    await expect(page.locator('.context-drawer:visible')).toHaveCount(0);
    assert.equal(state.writes.filter(w => w.path.endsWith('/sessions')).length, 0, 'opening is GET-only, no manual creation needed');
    await expect(panel.getByLabel(t('agent.sessions'))).not.toBeVisible();
    await expect(panel.getByLabel(t('agent.contextFields'))).not.toBeVisible();
    state.delayMessage = true;
    await panel.getByLabel(t('agent.message')).fill('Synthetic request');
    await panel.getByRole('button', { name: t('전송'), exact: true }).click();
    await expect(panel.locator('#agent-send-status .agent-progress')).toHaveText(t('agent.working'));
    await expect(panel.locator('#agent-send-status [data-agent-mark]')).toHaveCount(1);
    assert.equal(await panel.locator('#agent-send-status').evaluate(node => [...node.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join('').trim()), '', 'working indicator has no canned visible guide text');
    await expect(panel.locator('.agent-empty')).toHaveCount(0);
    await expect.poll(() => state.writes.filter(w => w.path.includes('/sessions')).map(w => w.path)).toEqual(['/api/lore-agent/sessions', '/api/lore-agent/sessions/s1/messages']);
    const draft = panel.locator('[data-agent-draft=d1]'); await expect(draft).toContainText('After');
    assert.equal(state.writes.filter(w => w.path.endsWith('/commit')).length,0);
    await expect(draft.getByRole('heading')).toHaveText('After');
    await expect(draft.getByRole('button', { name: t('agent.reviewProposal') })).toBeEnabled();
    await expect(draft.locator('textarea,pre,del,ins,code,details')).toHaveCount(0);
    await expect(draft.getByRole('button', { name: t('agent.approve') })).toHaveCount(0);
    await expect(draft.getByRole('button', { name: t('agent.commit') })).toHaveCount(0);
    const referencePlan = panel.locator('[data-agent-draft=reference-plan]');
    await expect(referencePlan.locator('input[type=radio]:checked')).toHaveCount(0);
    await expect(referencePlan.getByRole('button', { name: t('agent.approve') })).toBeDisabled();
    await referencePlan.getByLabel(t('agent.modeReference'), { exact: true }).check();
    await expect(referencePlan.getByRole('button', { name: t('agent.confirmImageChoice') })).toBeDisabled();
    await referencePlan.getByRole('radio', { name: 'Current portrait', exact: true }).check();
    await panel.getByRole('button', { name: t('닫기'), exact: true }).click(); await page.locator('.agent-launcher').click();
    await expect(referencePlan.getByRole('radio', { name: 'Current portrait', exact: true })).toBeChecked();
    await referencePlan.getByRole('button', { name: t('agent.confirmImageChoice') }).click();
    await expect(referencePlan.locator('figure img')).toBeVisible();
    assert.ok((await referencePlan.locator('figure img').getAttribute('src')).startsWith('blob:'));
    await referencePlan.getByRole('button', { name: t('agent.approve') }).click();
    await referencePlan.getByRole('radio', { name: 'Blue outfit', exact: true }).check();
    await expect(referencePlan.getByRole('button', { name: t('agent.generatePaid') })).toBeDisabled();
    await referencePlan.getByRole('button', { name: t('agent.confirmImageChoice') }).click();
    await expect(referencePlan.getByRole('button', { name: t('agent.generatePaid') })).toHaveCount(0);
    await referencePlan.getByRole('button', { name: t('agent.approve') }).click();
    await referencePlan.getByLabel(t('agent.imagePrompt')).fill('Changed prompt requires renewed consent');
    await expect(referencePlan.getByRole('button', { name: t('agent.generatePaid') })).toBeDisabled();
    await referencePlan.getByRole('button', { name: t('agent.revise') }).click();
    await expect(referencePlan.locator('input[type=radio]:checked')).toHaveCount(0);
    await expect(referencePlan.getByRole('button', { name: t('agent.approve') })).toBeDisabled();
    const unsupportedPlan = panel.locator('[data-agent-draft=unsupported-plan]');
    await unsupportedPlan.getByLabel(t('agent.modeReference'), { exact: true }).check();
    await unsupportedPlan.getByRole('radio', { name: 'Current portrait', exact: true }).check();
    await expect(unsupportedPlan.getByText(t('agent.referenceUnsupported'))).toBeVisible();
    await expect(unsupportedPlan.getByRole('button', { name: t('agent.confirmImageChoice') })).toBeDisabled();
    await expect(unsupportedPlan.getByRole('button', { name: t('agent.approve') })).toBeDisabled();
    assert.equal(state.writes.filter(w => w.path.includes('unsupported-plan/commit')).length, 0);
    await unsupportedPlan.getByLabel(t('agent.modeNew'), { exact: true }).check();
    await unsupportedPlan.getByRole('button', { name: t('agent.confirmImageChoice') }).click();
    await expect(unsupportedPlan.getByRole('button', { name: t('agent.approve') })).toBeEnabled();
    if (width < 1100) assert.equal(await panel.evaluate(node => { const r = node.getBoundingClientRect(); return node.contains(document.elementFromPoint(r.x + r.width / 2, r.bottom - 20)); }), true, 'Agent modal must cover the retained roleplay composer');
    if (artifacts) { await referencePlan.locator('legend').scrollIntoViewIfNeeded(); await page.screenshot({ path: `${artifacts}/consistency-${width}.png` }); }
    const plan = panel.locator('[data-agent-draft=image-plan]');
    await plan.getByRole('button', { name: t('agent.approve') }).click();
    await plan.getByRole('button', { name: t('agent.generatePaid') }).click();
    await expect(panel.getByAltText(t('agent.generatedImage'))).toBeVisible();
    assert.ok((await panel.getByAltText(t('agent.generatedImage')).getAttribute('src')).startsWith('blob:'));
    await panel.getByLabel(t('agent.selectImage')).check();
    await panel.getByLabel(t('agent.imageTarget'), { exact: true }).selectOption('c1');
    await panel.getByLabel(t('agent.imageLabel')).fill('Synthetic asset');
    await panel.getByRole('button', { name: t('닫기'), exact: true }).click();
    await page.locator('.agent-launcher').click();
    await expect(panel.getByLabel(t('agent.selectImage'))).toBeChecked();
    await expect(panel.getByLabel(t('agent.imageLabel'))).toHaveValue('Synthetic asset');
    await panel.getByRole('button', { name: t('agent.stageRegistration') }).click();
    const registration = panel.locator('[data-agent-draft=registration]'); await expect(registration).toBeVisible();
    await expect(registration.getByRole('button', { name: t('agent.commit') })).toHaveCount(0);
    await registration.getByRole('button', { name: t('agent.approve') }).click();
    await registration.getByRole('button', { name: t('agent.commit') }).click();
    await expect(registration).toContainText(t('agent.status.committed'));
    const worldCreate = panel.locator('[data-agent-draft=world-create]');
    await expect(worldCreate).toContainText(t('agent.status.committed'));
    await expect(worldCreate.locator('textarea,pre,del,ins,code,details')).toHaveCount(0);
    const worldPlan = panel.locator('[data-agent-draft=world-plan]');
    await worldPlan.getByRole('button', { name: t('agent.approve') }).click();
    await worldPlan.getByRole('button', { name: t('agent.generatePaid') }).click();
    const worldImages = panel.locator('[data-agent-image-job=jw]');
    await expect(worldImages.getByLabel(t('agent.imageTargetKind'))).toHaveValue('world');
    await expect(worldImages.getByLabel(t('agent.imageTarget'), { exact: true })).toHaveValue('w-new');
    await expect(worldImages.getByLabel(t('agent.imageTarget'), { exact: true }).locator('option:checked')).toHaveText('Newly committed world');
    await worldImages.getByLabel(t('agent.selectImage')).nth(0).check();
    await worldImages.getByLabel(t('agent.selectImage')).nth(1).check();
    await expect(worldImages.getByLabel(t('agent.selectImage')).nth(0)).not.toBeChecked();
    await panel.getByRole('button', { name: t('닫기'), exact: true }).click();
    await page.locator('.agent-launcher').click();
    await expect(worldImages.getByLabel(t('agent.selectImage')).nth(1)).toBeChecked();
    await expect(panel.locator('[data-agent-image-job=j1]').getByLabel(t('agent.imageLabel'))).toHaveValue('Synthetic asset');
    await worldImages.getByRole('button', { name: t('agent.stageRegistration') }).click();
    const worldRegistration = panel.locator('[data-agent-draft=world-registration]');
    await expect(worldRegistration).toContainText(t('agent.worldRegistrationHint'));
    await expect(worldRegistration.getByRole('button', { name: t('agent.commit') })).toHaveCount(0);
    await worldRegistration.getByRole('button', { name: t('agent.approve') }).click();
    await worldRegistration.getByRole('button', { name: t('agent.commit') }).click();
    await expect(worldRegistration).toContainText(t('agent.status.committed'));
    await verifyConversation(page, state, panel, dict, width);
    await panel.getByLabel(t('agent.message')).fill('UNSENT AGENT DRAFT');
    if (width < 1100) { await page.keyboard.press('Escape'); await expect(page.locator('.agent-launcher')).toBeFocused(); }
    else await panel.getByRole('button', { name: t('닫기'), exact: true }).click();
    assert.equal(await original.evaluate(node => node === document.querySelector('.composer textarea')),true);
    await expect(composer).toHaveValue('ROLEPLAY DRAFT RETAINED');
    assert.ok(Math.abs(await page.locator('.chat-thread').evaluate(node => node.scrollTop) - beforeScroll) < 4, 'opening agent preserves reading position');
    await navigate('/settings'); await page.locator('.agent-launcher').click();
    await expect(panel.getByLabel(t('agent.message'))).toHaveValue('UNSENT AGENT DRAFT');
    await panel.getByText(t('agent.chatAdvanced'), { exact: true }).click();
    await panel.getByText(t('agent.context'), { exact: true }).click();
    await panel.getByText(t('agent.contextAdvanced'), { exact: true }).click();
    await panel.getByLabel(t('agent.contextId'), { exact: true }).selectOption('c1');
    await panel.getByLabel(t('agent.contextFields')).fill('persona');
    await panel.getByRole('button', { name: t('추가'), exact: true }).click();
    state.expectedContext = [{ kind: 'character', id: 'c1', fields: ['persona'] }];
    state.abortMessage = true;
    await panel.getByRole('button', { name: t('전송'), exact: true }).click();
    await expect(panel.getByText(t('agent.uncertain'))).toBeVisible();
    const postCount = state.writes.length;
    await panel.getByRole('button', { name: t('agent.refresh'), exact: true }).first().click();
    await expect(panel.getByRole('button', { name: t('agent.newSession') })).toBeEnabled();
    const pendingJournal = await page.evaluate(() => JSON.parse(sessionStorage.getItem('lore-agent-request-recovery-v1')));
    assert.ok(pendingJournal.some(item => item.request_id === state.writes.at(-1).body.request_id), 'unknown receipt retains its exact request journal');
    assert.equal(state.writes.length, postCount, 'unknown recovery sends GET only');
    await expect(panel.locator('.agent-messages')).toContainText('A readable answer');
    const messageColors = await panel.locator('.agent-messages article').first().evaluate(node => ({ fg: getComputedStyle(node).color, bg: getComputedStyle(node).backgroundColor }));
    assert.notEqual(messageColors.fg, messageColors.bg, 'messages remain readable');
    const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
    assert.ok(!storage.includes('SYNTHETIC-MCP-ONCE') && !storage.includes('UNSENT AGENT DRAFT') && !storage.includes('Changed prompt requires renewed consent') && !storage.includes('base64') && !storage.includes('image_context'));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    if (artifacts) await page.screenshot({ path: `${artifacts}/agent-${width}.png` });
    await panel.getByRole('button', { name: t('닫기'), exact: true }).click();
    await navigate('/settings/lore-agent');
    await settings.getByRole('switch', { name: t('agent.enable') }).focus(); await page.keyboard.press('Space');
    await saveAgentSettings(page, t('저장'));
    await expect(page.locator('.agent-launcher')).toHaveCount(0);
    assert.equal(state.settings.llm_model_option_key, 'agent-llm', 'master toggle preserves independent selection');
    await expect.poll(() => page.evaluate(() => window.agentBlobLifecycle.made.filter(url => !window.agentBlobLifecycle.revoked.includes(url)).length)).toBe(0);
    assert.deepEqual(state.errors, []); assertions++;
    console.log(`PASS production Agent ${width}x${height}: direct settings without owner credentials, MCP issue/revoke, version approval, paid-plan gate, image blob, separate registration, composer/scroll/route retention, unknown GET-only recovery`);
    await page.close();
  }
  for (const [width,height,locale] of (process.env.AGENT_IMAGE_ONLY ? [] : [[320,740,'ko'],[820,1180,'ja']])) {
    const { page, state } = await setup(width,height,locale); state.settings.owner_configured = false;
    await page.goto(origin + '/settings/lore-agent');
    const local = JSON.parse(readFileSync(`src/i18n/messages.${locale}.json`, 'utf8'));
    const settings = page.locator('[data-page=agent-settings]');
    await expect(settings.getByRole('switch', { name: local['agent.enable'] })).toBeEnabled();
    await saveAgentSettings(page, local['저장'] || '저장');
    await expect(settings.getByText(local['agent.saved'])).toBeVisible();
    assert.equal(state.settings.enabled, false);
    assert.equal(state.settings.llm_model_option_key, null);
    assert.equal(state.settings.mcp_enabled, false);
    // Older servers without account metadata derive distinct account choices.
    if (locale === 'ja') delete state.settings.llm_accounts;
    // Existing saved selections and sessions work after reload, with legacy owner_configured=false.
    Object.assign(state.settings, { enabled: true, llm_model_option_key: 'agent-llm', image_provider_account_id: 'a', image_model_id: 'gpt-image-1.5' });
    state.sessions = [{ id: 's1', title: 'Existing session', messages: [], busy: false, last_error: null }];
    await page.reload();
    await expect(settings.getByLabel(local['agent.llm'], { exact: true })).toHaveValue('agent-llm');
    await expect(settings.getByLabel(local['agent.llmAccount'], { exact: true })).toHaveValue('a');
    await expect(settings.getByLabel(local['agent.imageAccount'])).toHaveValue('a');
    await expect(settings.getByLabel(local['agent.imageModel'])).toHaveValue('gpt-image-1.5');
    await settings.getByLabel(local['agent.llmAccount'], { exact: true }).selectOption('a2');
    await expect(settings.getByLabel(local['agent.llm'], { exact: true })).toHaveValue('');
    assert.deepEqual(await settings.getByLabel(local['agent.llm'], { exact: true }).locator('option').evaluateAll(nodes => nodes.map(n => n.value)), ['', 'second-openai']);
    await expect(settings.getByRole('switch', { name: local['agent.enable'] })).toBeChecked();
    await expect(settings.getByLabel(local['agent.imageModel'])).toHaveValue('gpt-image-1.5');
    await settings.getByLabel(local['agent.llmAccount'], { exact: true }).selectOption('v');
    await expect(settings.getByLabel(local['agent.llm'], { exact: true })).toHaveValue('');
    assert.deepEqual(await settings.getByLabel(local['agent.llm'], { exact: true }).locator('option').evaluateAll(nodes => nodes.map(n => n.value)), ['', 'vertex-model']);
    await settings.getByLabel(local['agent.llm'], { exact: true }).selectOption('vertex-model');
    await saveAgentSettings(page, local['저장'] || '저장');
    await expect(settings.getByText(local['agent.saved'])).toBeVisible();
    await page.reload();
    await expect(settings.getByLabel(local['agent.llmAccount'], { exact: true })).toHaveValue('v');
    await expect(settings.getByLabel(local['agent.llm'], { exact: true })).toHaveValue('vertex-model');
    await saveAgentSettings(page, local['저장'] || '저장');
    await expect(settings.getByText(local['agent.saved'])).toBeVisible();
    assert.equal(state.settings.llm_model_option_key, 'vertex-model');
    assert.equal(state.settings.image_model_id, 'gpt-image-1.5');
    assert.deepEqual(Object.keys(state.writes.at(-1).body).sort(), ['enabled','llm_model_option_key','image_provider_account_id','image_model_id','mcp_enabled'].sort());
    const writesBeforeOpen = state.writes.length;
    await expect(page.locator('.agent-launcher')).toHaveCount(1);
    await expect(page.locator('.agent-launcher')).toHaveAttribute('aria-label', local['agent.title']);
    await expect(page.locator('.agent-launcher')).toHaveAttribute('title', local['agent.title']);
    assert.equal(await page.locator('.agent-launcher').innerText(), '', 'icon-only launcher');
    assert.equal(await page.locator('.agent-launcher').evaluate(node => { const r = node.getBoundingClientRect(); return r.width >= 44 && r.height >= 44 && node.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)); }), true, 'labeled launcher remains hittable');
    await settings.getByRole('button', { name: local['agent.open'], exact: true }).click();
    const panel = page.locator('.agent-surface');
    await expect(panel.getByLabel(local['agent.sessions'])).not.toBeVisible();
    await panel.getByText(local['agent.chatAdvanced'], { exact: true }).click();
    await panel.getByText(local['agent.history'], { exact: true }).click();
    await expect(panel.getByLabel(local['agent.sessions'])).toHaveValue('');
    await panel.getByLabel(local['agent.sessions']).selectOption('s1');
    await expect(panel.getByLabel(local['agent.sessions'])).toHaveValue('s1');
    await expect(panel.getByLabel(local['agent.message'])).toBeEnabled();
    assert.equal(state.writes.length, writesBeforeOpen, 'opening existing Agent hydrates through GET only');
    await panel.getByRole('button', { name: local['닫기'] || '닫기', exact: true }).click();
    await expect(settings.getByRole('button', { name: local['agent.open'], exact: true })).toBeFocused();
    await settings.getByRole('button', { name: local['agent.open'], exact: true }).click();
    await verifyConversation(page, state, panel, local, width);
    await panel.getByRole('button', { name: local['닫기'] || '닫기', exact: true }).click();
    await expect(page.locator('.agent-launcher')).toBeFocused();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),false);
    await expect(page.locator('[data-page=agent-settings] input[type=password]')).toHaveCount(0);
    state.unavailable = true; await page.reload();
    await expect(page.locator('[data-page=agent-settings] [role=alert]')).toContainText('404');
    await expect(page.locator('.app-bar')).toBeVisible(); assert.deepEqual(state.errors, []); assertions++;
    console.log(`PASS production ${locale} ${width}x${height}: legacy owner flag ignored, direct save, saved selections/session hydration, old-backend 404 isolated to settings`);
    await page.close();
  }
  console.log(`PASS ${assertions} bounded production browser scenarios; all API synthetic, external traffic blocked`);
} finally { await browser.close(); await new Promise(r => server.close(r)); }
