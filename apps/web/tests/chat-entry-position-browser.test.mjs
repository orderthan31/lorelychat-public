import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';
import { chromium, expect } from '@playwright/test';

// All application API traffic is fulfilled here, even against the live dev UI.
// No participant/runtime write, generation or TTS reaches an actual API.
const out = process.env.UX_ARTIFACT_DIR;
if (out) mkdirSync(out, { recursive: true });
const externalBase = process.env.UX_BASE_URL;
const fixture = { name: 'ux-full-app-fixture', configureServer(server) {
  server.middlewares.use(async (req, res, next) => {
    if (!req.url.startsWith('/conversations') && !req.url.startsWith('/__chat-position')) return next();
    res.setHeader('Content-Type', 'text/html');
    const entry=req.url.startsWith('/__chat-position')?'chat-position.fixture.tsx':'hardening-browser.fixture.tsx';
    res.end(await server.transformIndexHtml(req.url, `<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/${entry}"></script></body></html>`));
  });
}};
const cacheDir = mkdtempSync(join(tmpdir(), 'chat-position-vite-'));
const server = externalBase ? null : await createServer({ cacheDir, configFile: false, envFile: false, plugins: [fixture, react(), tailwind()], server: { host: '127.0.0.1', port: 0, watch: null, hmr: false }, logLevel: 'error' });
let browser;
const checks = [], errors = [], unexpectedWrites = [], blockedExternal = [];
const requests = [];
const ko = JSON.parse(readFileSync('src/i18n/messages.ko.json', 'utf8'));
const t = key => ko[key] || key;
try {
  if (server) await server.listen();
  const origin = externalBase || `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({ headless: true });
  async function makePage(width, height, theme = 'light') {
    const page = await browser.newPage({ viewport: { width, height } });
    // Keep a concurrent development edit from reloading the synthetic room.
    // Mock (do not connect) its HMR socket; API requests remain intercepted.
    if(externalBase) await page.routeWebSocket('**/*', () => {});
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(theme => {
      window.__uxClientErrors = [];
      window.addEventListener('error', event => { if (event.message) window.__uxClientErrors.push(event.message); }, true);
      localStorage.setItem('lorechat-language', 'ko');
      localStorage.setItem('lorely-chat-theme', theme);
    }, theme);
    const state = { imageHold: null, imageRelease: null, imageRequests: 0, imageDelay: 900, olderRequests: 0, olderHold: null, olderRelease: null, invites: 0, patches: 0, hold: null, release: null, failRoom: false, initialDelay: 0, participants: [{ type: 'user', id: 'user' }, { type: 'character', id: 'c1' }], setting: {} };
    await page.route('**/*', async route => {
      const req = route.request(), u = new URL(req.url()), path = u.pathname;
      if (u.origin !== origin) { blockedExternal.push(u.origin); return route.abort(); }
      if (path==='/fixture-delayed.svg') {
        state.imageRequests++;
        if(state.imageHold) await state.imageHold;
        return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="900"><rect width="300" height="900" fill="#ccddee"/></svg>'});
      }
      if (!path.startsWith('/api/')) return ['GET', 'HEAD'].includes(req.method()) ? route.continue() : route.abort();
      requests.push({ path, method: req.method() });
      const json = (data, status = 200) => route.fulfill({ status, json: data });
      if (path === '/api/images/prepare') return json({ image_url: '/images/cache/synthetic/preview.webp' });
      if (path.startsWith('/api/images/cache/')) { await new Promise(r => setTimeout(r, state.imageDelay)); return route.fulfill({ contentType: 'image/webp', body: readFileSync('public/brand/lorely-app-icon-light.webp') }); }
      if (req.method() === 'POST' && path === '/api/conversations/A/participants') {
        state.invites++;
        state.participants.push(JSON.parse(req.postData()));
        return json(state.participants);
      }
      if (req.method() === 'PATCH' && path === '/api/runtime-settings/conversations/A') {
        state.patches++;
        state.setting = { ...state.setting, ...JSON.parse(req.postData()) };
        return json(state.setting);
      }
      if (req.method() === 'PATCH' && path === '/api/conversations/A') {
        return json({ id: 'A', title: 'Synthetic UX room', genre_mode: 'romance', mode: 'multi_character', tts_enabled: false, ...JSON.parse(req.postData()) });
      }
      if (req.method() === 'POST' && path.endsWith('/read')) return json({});
      if (!['GET', 'HEAD'].includes(req.method())) { unexpectedWrites.push({ path, method: req.method() }); return route.abort(); }
      if (path === '/api/conversations/A') {
        if (state.initialDelay) { const ms = state.initialDelay; state.initialDelay = 0; await new Promise(r => setTimeout(r, ms)); }
        if (state.hold) await state.hold;
        if (state.failRoom) return json({ detail: 'Synthetic refresh unavailable' }, 503);
        return json({ id: 'A', title: 'Synthetic UX room', genre_mode: 'romance', mode: 'multi_character', tts_enabled: false });
      }
      if (path === '/api/conversations/A/messages') {
        await new Promise(r => setTimeout(r, 300));
        if (u.searchParams.has('before_id')) {
          state.olderRequests++;
          if(state.olderHold) await state.olderHold;
          return json(Array.from({length:32},(_,i)=>({id:`older-${i}`,speaker_type:'user',content:`Older synthetic ${i}. `+'Older page. '.repeat(20),created_at:new Date(1799999900000+i*1000).toISOString()})));
        }
        return json(Array.from({ length: 180 }, (_, i) => ({ id: `m-${i}`, conversation_id: 'A', speaker_type: i % 4 === 0 ? 'user' : 'character', speaker_id: 'c1', content: `Synthetic turn ${Math.floor(i / 4)} bubble ${i}. ` + 'Multi bubble history with deliberately variable height. '.repeat(i % 7 === 0 ? 45 : 9), assets: i === 179 ? [{id:'late-image', image_url:'/fixture-image.png', label:'Delayed synthetic image'}] : [], created_at: new Date(1800000000000 + i * 1000).toISOString() })));
      }
      if (path.endsWith('/participants')) return json(state.participants);
      if (path.includes('/generation-jobs/active')) return json({ jobs: [] });
      if (path.endsWith('/context')) return json({ scene: { summary: 'Synthetic story context' }, memories: [] });
      if (path === '/api/conversations') return json([{ id: 'A', title: 'Synthetic UX room', participants: ['c1','c2','c3'].map(id=>({type:'character',id})) }]);
      if (path === '/api/characters') return json(['c1', 'c2', 'c3'].map((id, i) => ({ id, name: `Synthetic cast ${i + 1}`, persona: 'Fixture persona' })));
      if (path.startsWith('/api/runtime-settings')) return json(state.setting);
      if (path === '/api/health' || path === '/api/ready') return json({ status: 'ok' });
      if (path === '/api/world-settings' || path === '/api/presets' || path === '/api/chat-commands' || path.includes('/tts/') || path === '/api/model-options' || path === '/api/model-provider-accounts' || path === '/api/characters/usage-ranking') return json([]);
      return json({});
    });
    return { page, state };
  }
  const input = page => page.getByRole('textbox', { name: '메시지 입력 · Enter 줄바꿈 · Ctrl+Enter 전송', exact: true });
  const position = page => page.locator('.chat-thread').evaluate(node => ({ top:node.scrollTop, height:node.scrollHeight, viewport:node.clientHeight, gap:node.scrollHeight-node.clientHeight-node.scrollTop, last: !!node.querySelector('[data-chat-row="m-179"]'), rows:node.querySelectorAll('[data-chat-row]').length }));
  const anchor = page => page.locator('.chat-thread').evaluate(node => {
    const top=node.getBoundingClientRect().top;
    const row=[...node.querySelectorAll('[data-chat-row]')].find(row=>row.getBoundingClientRect().bottom>top+1);
    return {id:row?.dataset.chatRow, offset:row?.getBoundingClientRect().top-top};
  });
  for (const [width, height] of [[390,844], [844,390], [820,1180], [1024,768]]) {
    const {page, state} = await makePage(width,height);
    state.initialDelay=500;
    await page.goto(origin+'/conversations/A');
    await expect(input(page)).toBeVisible({timeout:20000}).catch(async error => { console.log({errors, body:(await page.locator('body').innerText()).slice(0,3000), requests}); throw error; });
    await page.waitForTimeout(2000);
    const initial = await position(page);
    console.log(JSON.stringify({case:'initial',width,...initial}));
    if (!process.env.SCROLL_BASELINE) {
      assert.ok(initial.gap <= 3, `entry must settle at final bubble: ${JSON.stringify(initial)}`);
      assert.equal(initial.last,true);
    }
    checks.push({case:'initial',width,viewportHeight:height,...initial});
    if(process.env.SCROLL_BASELINE) { await page.close(); continue; }
    assert.ok(initial.rows < 80,'history is actually virtualized');
    await expect(page.locator('.bubble-asset-image')).toHaveAttribute('data-image-state','ready');
    if(width===1024) {
      await page.setViewportSize({width:1920,height});
      await page.waitForTimeout(400);
      const wide=await position(page);
      assert.ok(wide.gap<3,'latest intent survives 1024 to 1920 expanded dock');
      checks.push({case:'1024 to 1920 latest position',...wide});
      await page.setViewportSize({width,height});
      await page.waitForTimeout(400);
      assert.ok((await position(page)).gap<3,'latest intent survives reverse resize');
    }
    await input(page).fill('Unsent draft survives retained refresh');
    const composer=await input(page).elementHandle();
    await page.locator('.chat-thread').evaluate(node=>{ node.scrollTop=1000; });
    await page.waitForTimeout(300);
    const retained=await anchor(page);
    const retainedTop=(await position(page)).top;
    state.hold=new Promise(resolve=>{state.release=resolve;});
    if(!await page.locator('.context-drawer').count()) await page.locator('.chat-settings-button').click();
    // Settings opens the single panel; invitation lives in its Manage tab.
    const invite=page.getByRole('combobox',{name:'초대할 캐릭터 선택',exact:true});
    if(!await invite.isVisible()) await page.getByRole('button',{name:'관리',exact:true}).click();
    await invite.selectOption('c2');
    await page.getByRole('button',{name:'초대',exact:true}).click();
    await expect.poll(()=>state.invites).toBe(1);
    await page.waitForTimeout(150);
    assert.ok(Math.abs((await position(page)).top-retainedTop)<3,'pending refresh retains reading position');
    state.release(); state.hold=null;
    await expect(page.locator('.role-card-list')).toContainText('Synthetic cast 2');
    await page.waitForTimeout(300);
    assert.deepEqual(await anchor(page),retained,'same-room snapshot preserves reading anchor');
    assert.equal(await composer.evaluate(node=>node.isConnected),true);
    await expect(input(page)).toHaveValue('Unsent draft survives retained refresh');
    if(width!==1024 && await page.locator('.drawer-close').count()) await page.locator('.drawer-close').click();
    checks.push({case:'same-ready-room pending/settled refresh retains anchor and mounted draft',width,anchor:retained});
    if(width===1024) {
      const beforeWide=await anchor(page);
      await page.setViewportSize({width:1920,height});
      await page.waitForTimeout(400);
      const afterWide=await anchor(page);
      assert.equal(afterWide.id,beforeWide.id,'wide resize retains reading message');
      assert.ok(Math.abs(afterWide.offset-beforeWide.offset)<3,'wide resize retains intra-row position');
      await expect(input(page)).toHaveValue('Unsent draft survives retained refresh');
      const widths=await page.evaluate(()=>({shell:document.querySelector('.chat-shell').getBoundingClientRect().width,thread:document.querySelector('.chat-thread').getBoundingClientRect().width,panel:document.querySelector('.context-drawer')?.getBoundingClientRect().width??0}));
      assert.ok(widths.shell<=960&&widths.thread<=960,'wide chat is capped at 960px');
      assert.ok(widths.panel>=959,'1920px dock absorbs the remaining width');
      checks.push({case:'1024 to 1920 retained reading position and draft',before:beforeWide,after:afterWide,widths});
    }

    await page.locator('.chat-thread').evaluate(node=>{node.scrollTop=320;});
    await page.waitForTimeout(300);
    const beforePrepend=await anchor(page);
    state.olderHold=new Promise(resolve=>{state.olderRelease=resolve;});
    await page.getByRole('button',{name:'이전 대화 더 보기',exact:true}).evaluate(node=>node.click());
    await expect.poll(()=>state.olderRequests).toBe(1);
    state.olderRelease(); state.olderHold=null;
    await expect(page.locator('[data-chat-window]')).toHaveAttribute('data-total-messages','212');
    await page.waitForTimeout(400);
    const afterPrepend=await anchor(page);
    assert.equal(afterPrepend.id,beforePrepend.id,'prepend keeps message ID');
    assert.ok(Math.abs(afterPrepend.offset-beforePrepend.offset)<3,`prepend keeps intra-row position: ${JSON.stringify({beforePrepend,afterPrepend})}`);
    checks.push({case:'upward pagination ID/intra-row anchoring',width,before:beforePrepend,after:afterPrepend});
    await page.close();
  }
  if(!process.env.SCROLL_BASELINE) {
    // Scoped hook fixture exercises intrinsic media growth, rather than relying
    // on a production image frame that already reserves its aspect ratio.
    if(!externalBase) for(const [width,height] of [[390,844],[1024,768]]) for(const manual of [false,true]) {
      const {page,state}=await makePage(width,height);
      state.imageHold=new Promise(resolve=>{state.imageRelease=resolve;});
      await page.goto(origin+'/__chat-position');
      await expect.poll(()=>state.imageRequests).toBe(1);
      const thread=page.locator('.fixture-thread');
      const geometry=()=>thread.evaluate(node=>({top:node.scrollTop,gap:node.scrollHeight-node.scrollTop-node.clientHeight,rows:node.querySelectorAll('[data-chat-row]').length}));
      await expect.poll(async()=>(await geometry()).gap).toBeLessThan(3);
      if(manual) {
        const box=await thread.boundingBox();
        await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
        await page.mouse.wheel(0,-280);
        await expect.poll(async()=>(await geometry()).gap).toBeGreaterThan(100);
        await page.waitForTimeout(150);
      }
      const before=await geometry();
      state.imageRelease();state.imageHold=null;
      await expect.poll(()=>page.locator('img').evaluate(image=>image.complete&&image.naturalHeight===900)).toBe(true);
      await page.waitForTimeout(350);
      const after=await geometry();
      if(manual) assert.ok(Math.abs(after.top-before.top)<3,`manual scroll wins over late image: ${JSON.stringify({before,after})}`);
      else assert.ok(after.gap<3,`late mount + image stays at latest: ${JSON.stringify(after)}`);
      const visibleAnchor=()=>thread.evaluate(node=>{
        const top=node.getBoundingClientRect().top;
        const row=[...node.querySelectorAll('[data-chat-row]')].find(row=>row.getBoundingClientRect().bottom>top+1);
        return {id:row.dataset.chatRow,offset:row.getBoundingClientRect().top-top};
      });
      const rotationAnchor=await visibleAnchor();
      await page.setViewportSize({width:height,height:width});
      await page.waitForTimeout(350);
      const rotated=await geometry();
      if(manual) {
        const actual=await visibleAnchor();
        assert.equal(actual.id,rotationAnchor.id,'rotation keeps reading message');
        assert.ok(Math.abs(actual.offset-rotationAnchor.offset)<3,'rotation keeps intra-row offset');
      } else assert.ok(rotated.gap<3,'latest stays latest after rotation');
      await page.getByRole('button',{name:'Refresh snapshot'}).click();
      await page.waitForTimeout(150);
      assert.ok(Math.abs((await geometry()).top-rotated.top)<3,'explicit refresh does not reapply entry');
      checks.push({case:manual?'manual wheel wins over delayed image and rotation':'delayed scroller mount/intrinsic image growth and rotation',width,before,after,rotated});
      await page.close();
    }
    for(const [width,height] of [[320,568],[390,844],[820,1180],[1024,768],[1440,1000]]) {
      const {page}=await makePage(width,height);
      await page.goto(origin+'/conversations');
      await expect(page.locator('.conversation-participant-avatar')).toHaveCount(2);
      const geometry=await page.locator('[data-resource-card]').first().evaluate(card=>{
        const rect=el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
        return {media:rect(card.querySelector('[data-resource-media]')),avatars:[...card.querySelectorAll('.conversation-participant-avatar')].map(rect),controls:rect(card.querySelector('[data-resource-actions]')),overflow:document.documentElement.scrollWidth-innerWidth};
      });
      const overlap=(a,b)=>a.x<b.right&&a.right>b.x&&a.y<b.bottom&&a.bottom>b.y;
      assert.equal(geometry.overflow,0);
      assert.equal(overlap(...geometry.avatars),false,'profiles never cover one another');
      for(const avatar of geometry.avatars) {
        assert.equal(overlap(avatar,geometry.media),false,'profiles are separate from room photo');
        assert.equal(overlap(avatar,geometry.controls),false,'profiles are separate from actions');
        assert.ok(avatar.width>=35&&avatar.height>=35);
      }
      if(out&&width===390) await page.screenshot({path:`${out}/synthetic-participants-390.png`});
      checks.push({case:'participant metadata geometry',width,height,...geometry});
      await page.close();
    }
  }
  assert.deepEqual(errors,[]);
  assert.deepEqual(unexpectedWrites,[]);
  const result={status:process.env.SCROLL_BASELINE?'BASELINE':'PASS',checks,pageErrors:errors,liveApiRequestsForwarded:0};
  if(out) writeFileSync(`${out}/result.json`,JSON.stringify(result,null,2)+'\n');
  console.log(JSON.stringify(result));
} finally { await browser?.close(); await server?.close(); rmSync(cacheDir,{recursive:true,force:true}); }
