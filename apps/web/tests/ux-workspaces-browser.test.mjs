import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {createServer} from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';
import {chromium,expect} from '@playwright/test';
const listsOnly=process.env.UX_LISTS_ONLY==='1';
const out=process.env.UX_ARTIFACT_DIR;if(out)mkdirSync(out,{recursive:true});
const plugin={name:'ux-workspaces-fixture',configureServer(server){server.middlewares.use('/workspace-fixture',async(_req,res)=>{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/workspace-fixture','<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/ux-workspaces.fixture.tsx"></script></body></html>'));});}};
const cacheDir = mkdtempSync(join(tmpdir(), 'integration-repair-vite-'));
const server=await createServer({ cacheDir,configFile:false,envFile:false,plugins:[plugin,react(),tailwind()],server:{host:'127.0.0.1',port:0,watch:null,hmr:false},logLevel:'error'});let browser;const checks=[],errors=[],writes=[];
try{
 await server.listen();browser=await chromium.launch({headless:true});const origin=`http://127.0.0.1:${server.httpServer.address().port}`;
 const account={id:'account-a',provider_type:'openai',alias:'Synthetic provider',enabled:true,configured:true,api_key_status:'set',active_model_count:5};
 const models=Array.from({length:5},(_,i)=>({id:`model-${i}`,key:`model-${i}`,model:`synthetic-model-${i}`,display_label:`Synthetic model ${i}`,label:`Synthetic model ${i}`,provider_account_id:account.id,provider_type:'openai',provider_account_alias:account.alias,enabled:true,supports_chat:true,supports_compression:true,supports_tts:false,supports_decision:false,input_modalities:['text'],image_mime_types:[],capability_source:'manual',model_family:'custom',source:'fetched'}));
 async function pageFor(width,height,view,locale='ko',theme='light'){
  const page=await browser.newPage({viewport:{width,height}});page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(({locale,theme})=>{localStorage.setItem('lorechat-language',locale);document.addEventListener('DOMContentLoaded',()=>document.documentElement.classList.toggle('theme-dark',theme==='dark'),{once:true})},{locale,theme});
  await page.route('**/*',route=>{const req=route.request(),u=new URL(req.url());if(u.origin!==origin)return route.abort();if(u.pathname==='/api/images/prepare'){assert.equal(req.method(),'POST');return route.fulfill({json:{image_url:'/images/cache/synthetic/preview.webp'}})}if(u.pathname.startsWith('/api/images/cache/'))return route.fulfill({contentType:'image/webp',body:readFileSync('public/brand/lorely-app-icon-light.webp')});if(req.method()!=='GET'){writes.push(u.pathname);return route.abort()}if(u.pathname==='/synthetic.png')return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lz8AAAAASUVORK5CYII=','base64')});if(!u.pathname.startsWith('/api/'))return route.continue();if(u.pathname==='/api/model-provider-accounts')return route.fulfill({json:[account]});if(u.pathname==='/api/model-options')return route.fulfill({json:models});if(/^\/api\/conversations\/[^/]+\/participants$/.test(u.pathname)){const id=u.pathname.split('/')[3];const count=id==='room-a'?0:({'room-1':1,'room-2':2,'room-3':5}[id]??0);return route.fulfill({json:Array.from({length:count},(_,j)=>({type:'character',id:`cast-${j}`}))})}if(u.pathname==='/api/conversations')return route.fulfill({json:[0,1,2,5].map((count,i)=>({id:i===0?'room-a':`room-${i}`,title:`Synthetic room ${i}`,world_setting_id:'world-a',has_unread:i===1,unread_count:3,participants:Array.from({length:count},(_,j)=>({type:'character',id:`cast-${j}`}))}))});if(u.pathname==='/api/world-settings')return route.fulfill({json:{items:Array.from({length:4},(_,i)=>({id:i===0?'world-a':`world-${i}`,title:`Synthetic world ${i}`,description:'Synthetic world description'})),total:4,page:1,page_size:12,pages:1}});if(u.pathname==='/api/characters')return route.fulfill({json:{items:Array.from({length:4},(_,i)=>({id:i===0?'character-a':`character-${i}`,name:`Synthetic character ${i}`,description:'Synthetic description only',persona:'Preserved authored text'})),total:4,page:1,page_size:12,pages:1}});if(u.pathname.includes('/usage/'))return route.fulfill({json:{total_calls:null,prompt_tokens:0,completion_tokens:120,total_tokens:120,buckets:[]}});return route.fulfill({json:[]});});
  await page.goto(`${origin}/workspace-fixture?view=${view}${view==='models'?'#models':''}`);return page;
 }
 const overflow=async page=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth),0,'no document horizontal overflow');
 if(!listsOnly) for(const [width,height] of [[320,568],[390,844],[820,1180],[1024,768],[1440,1000]])for(const theme of ['light','dark']){
  const page=await pageFor(width,height,'models','ko',theme);await expect(page.locator('[data-model-row]')).toHaveCount(5);
  if(width<960){const toggle=page.locator('[data-model-filter-toggle]');await expect(toggle).toHaveAttribute('aria-expanded','false');await expect(page.locator('[data-model-filter-fields]')).toBeHidden();await toggle.click();await expect(page.locator('[data-model-filter-fields]')).toBeVisible();await toggle.click();}
  else await expect(page.locator('[data-model-filter-fields]')).toBeVisible();
  await overflow(page);if(out)await page.screenshot({path:`${out}/models-${width}-${theme}.png`});checks.push(`model filters ${width}x${height} ${theme}`);await page.close();
 }
 if(!listsOnly) for(const locale of ['en','ja']){const page=await pageFor(390,844,'models',locale);await expect(page.locator('[data-model-row]')).toHaveCount(5);const t=JSON.parse(readFileSync(`src/i18n/messages.${locale}.json`,'utf8'));await expect(page.locator('[data-model-filter-toggle]')).toHaveText(t['필터']);await overflow(page);checks.push(`model filter locale ${locale}`);await page.close();}
 for(const kind of ['characters','worlds','conversations']) for(const pane of [320,400,700,1100]) {
  const list=await pageFor(pane===320?320:1440,1000,kind);
  await expect(list.locator('[data-resource-card]')).toHaveCount(4);
  await list.locator('.shell').evaluate((node,width)=>{node.style.width=`${width}px`;node.style.maxWidth='100%';node.style.margin='0';},pane);
  await expect(list.locator('[data-resource-preview], .resource-inspector')).toHaveCount(0);
  const geometry=await list.evaluate(()=>{
   const rect=node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}};
   return {pane:rect(document.querySelector('[data-resource-workspace]')),columns:getComputedStyle(document.querySelector('.resource-list-grid')).gridTemplateColumns.split(' ').length,cards:[...document.querySelectorAll('[data-resource-card]')].map(node=>({rect:rect(node),media:rect(node.querySelector('[data-resource-media]')),avatars:[...node.querySelectorAll('.conversation-participant-avatar')].map(rect),summary:node.querySelector('.conversation-card-summary')?rect(node.querySelector('.conversation-card-summary')):null,metadata:node.querySelector('.conversation-card-metadata')?rect(node.querySelector('.conversation-card-metadata')):null,actions:[...node.querySelectorAll('[data-resource-actions] button')].map(rect)}))};
  });
  const expected=geometry.pane.width>=1000?3:geometry.pane.width>=(kind==='conversations'?760:640)?2:1;
  assert.equal(geometry.columns,expected,'columns follow actual pane width');
  assert.equal(new Set(geometry.cards.map(card=>Math.round(card.rect.height))).size,1,'equal card heights');
  if(kind==='conversations'){
   for(const [i,card] of geometry.cards.entries()){
    assert.equal(card.avatars.length,[0,1,2,2][i]);assert.ok(card.metadata.height>0,'participant metadata has visible bounds');
    for(const [j,avatar] of card.avatars.entries()){
     assert.ok(avatar.width>0&&avatar.height>0,'participant portraits have visible bounds');
     assert.ok(avatar.x>=card.media.right,'portraits never overlay room art');
     assert.ok(avatar.right<=card.metadata.right,'portraits fit metadata');
     assert.ok(Math.abs(avatar.x-(j?card.avatars[j-1].right+3:card.summary.x))<1,'portraits left aligned, nonoverlapping');
    }
    for(const action of card.actions){assert.ok(action.width+0.0001>=44&&action.height+0.0001>=44,`minimum 44px target: ${JSON.stringify({pane,action})}`);assert.ok(action.x>=card.metadata.right,'actions outside participant row');}
   }
   await expect(list.locator('.conversation-participant-extra')).toHaveText('+3');
   await expect(list.locator('[data-resource-card] b')).toHaveCount(1);
   await list.locator('[data-resource-actions] button').first().click();await expect(list.locator('[data-navigation]')).toHaveText('/conversations/room-a/edit');
   await list.locator('[data-resource-actions] button').nth(1).click();await expect(list.locator('[data-navigation]')).toHaveText('delete:room-a');
  }
  await list.locator('[data-resource-card] > button').first().click();
  await expect(list.locator('[data-navigation]')).toHaveText(kind==='characters'?'/characters/character-a':kind==='worlds'?'/world-settings/world-a':'/conversations/room-a');
  await overflow(list);
  if(out){await list.screenshot({path:`${out}/${kind}-pane-${pane}.png`});writeFileSync(`${out}/${kind}-pane-${pane}.json`,JSON.stringify(geometry,null,2));}
  if(kind==='conversations'){await list.getByRole('textbox').fill('Synthetic cast 4');await expect(list.locator('[data-resource-card]')).toHaveCount(1);}
  checks.push(`${kind} pane ${pane}: no preview, primary navigation, pane columns, compact cast/actions`);await list.close();
 }
 if(!listsOnly) for(const width of [320,390,820,1440]) {
  const page=await pageFor(width,width===320?568:1000,'assets');
  const tile=page.locator('.asset-tile').first();
  const image=tile.locator('img');
  await expect(image).toHaveAttribute('data-image-state','ready');
  const mediaBox=await image.boundingBox(), captionBox=await tile.locator('.asset-tile-caption').boundingBox(), tileBox=await tile.boundingBox();
  assert.ok(Math.abs(mediaBox.width-mediaBox.height)<1 && mediaBox.width>=tileBox.width-3 && captionBox.y>=mediaBox.y+mediaBox.height-1, 'thumbnail fills a square with caption below');
  await tile.click();
  if(width>=1100){await expect(page.locator('.asset-inspector')).toBeVisible();await expect(page.locator('[role="dialog"]')).toHaveCount(0);}
  else {await expect(page.locator('[role="dialog"]')).toBeVisible();assert.equal(await page.evaluate(()=>document.documentElement.style.overflow),'hidden');}
  const save=page.locator('.asset-save-action');await expect(save).toBeInViewport();
  const details=page.locator('.asset-inspector-details');await details.evaluate(node=>{node.scrollTop=node.scrollHeight});
  const last=details.locator('input[type=number]');await last.fill('70');
  const fieldBox=await last.boundingBox(), bodyBox=await details.boundingBox(), saveBox=await page.locator('.asset-save-bar').boundingBox();
  assert.ok(fieldBox.y+fieldBox.height<=bodyBox.y+bodyBox.height+1,'last field is fully inside scroll area');
  assert.ok(bodyBox.y+bodyBox.height<=saveBox.y+1,'save occupies separate space, never overlays fields');
  await save.click();await expect(page.locator('[data-asset-saves]')).toHaveText('1');
  await overflow(page);if(out)await page.screenshot({path:`${out}/assets-${width}.png`});
  if(width<1100){await page.keyboard.press('Escape');await expect(page.locator('[role="dialog"]')).toHaveCount(0);await expect(page.locator('.asset-grid button').first()).toBeFocused();assert.equal(await page.evaluate(()=>document.documentElement.style.overflow),'');}
  checks.push(`asset preview ${width} modal/docked, reachable nonoverlapping save and last field`);await page.close();
 }
 if(!listsOnly){const stats=await pageFor(1440,1000,'statistics');await expect(stats.locator('[data-usage-summary]')).toHaveCount(1);await expect(stats.locator('[data-usage-summary] dd').first()).toHaveText('—');await expect(stats.locator('[data-usage-summary] dd').nth(1)).toHaveText('0');await overflow(stats);checks.push('statistics unknown and measured zero remain distinct');await stats.close();}
 assert.deepEqual(writes,[],'no mutation requests sent');assert.deepEqual(errors,[],'no page errors');console.log(JSON.stringify({status:'PASS',checks:checks.length,cases:checks,pageErrors:errors,mutationRequests:writes}));if(out)writeFileSync(`${out}/result.json`,JSON.stringify({status:'PASS',checks,cases:checks.length,pageErrors:errors,mutationRequests:writes},null,2)+'\n');
}finally{await browser?.close();await server.close(); rmSync(cacheDir, { recursive: true, force: true }); }
