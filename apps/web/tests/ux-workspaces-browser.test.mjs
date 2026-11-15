import assert from 'node:assert/strict';
import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {createServer} from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';
import {chromium,expect} from '@playwright/test';
const out=process.env.UX_ARTIFACT_DIR;if(out)mkdirSync(out,{recursive:true});
const plugin={name:'ux-workspaces-fixture',configureServer(server){server.middlewares.use('/workspace-fixture',async(_req,res)=>{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/workspace-fixture','<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/ux-workspaces.fixture.tsx"></script></body></html>'));});}};
const server=await createServer({configFile:false,envFile:false,plugins:[plugin,react(),tailwind()],server:{host:'127.0.0.1',port:0,watch:null,hmr:false},logLevel:'error'});let browser;const checks=[],errors=[],writes=[];
try{
 await server.listen();browser=await chromium.launch({headless:true});const origin=`http://127.0.0.1:${server.httpServer.address().port}`;
 const account={id:'account-a',provider_type:'openai',alias:'Synthetic provider',enabled:true,configured:true,api_key_status:'set',active_model_count:5};
 const models=Array.from({length:5},(_,i)=>({id:`model-${i}`,key:`model-${i}`,model:`synthetic-model-${i}`,display_label:`Synthetic model ${i}`,label:`Synthetic model ${i}`,provider_account_id:account.id,provider_type:'openai',provider_account_alias:account.alias,enabled:true,supports_chat:true,supports_compression:true,supports_tts:false,supports_decision:false,input_modalities:['text'],image_mime_types:[],capability_source:'manual',model_family:'custom',source:'fetched'}));
 async function pageFor(width,height,view,locale='ko',theme='light'){
  const page=await browser.newPage({viewport:{width,height}});page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(({locale,theme})=>{localStorage.setItem('lorechat-language',locale);document.addEventListener('DOMContentLoaded',()=>document.documentElement.classList.toggle('theme-dark',theme==='dark'),{once:true})},{locale,theme});
  await page.route('**/*',route=>{const req=route.request(),u=new URL(req.url());if(u.origin!==origin)return route.abort();if(u.pathname==='/api/images/prepare'){assert.equal(req.method(),'POST');return route.fulfill({json:{image_url:'/images/cache/synthetic/preview.webp'}})}if(u.pathname.startsWith('/api/images/cache/'))return route.fulfill({contentType:'image/webp',body:readFileSync('public/brand/lorely-app-icon-light.webp')});if(req.method()!=='GET'){writes.push(u.pathname);return route.abort()}if(u.pathname==='/synthetic.png')return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lz8AAAAASUVORK5CYII=','base64')});if(!u.pathname.startsWith('/api/'))return route.continue();if(u.pathname==='/api/model-provider-accounts')return route.fulfill({json:[account]});if(u.pathname==='/api/model-options')return route.fulfill({json:models});if(u.pathname==='/api/conversations')return route.fulfill({json:[{id:'room-a',title:'Synthetic room',world_setting_id:'world-a',participants:[]}]});if(u.pathname==='/api/characters')return route.fulfill({json:{items:[{id:'character-a',name:'Synthetic character',description:'Synthetic preview only',persona:'Preserved authored text'}],total:1,page:1,page_size:12,pages:1}});if(u.pathname.includes('/usage/'))return route.fulfill({json:{total_calls:null,prompt_tokens:0,completion_tokens:120,total_tokens:120,buckets:[]}});return route.fulfill({json:[]});});
  await page.goto(`${origin}/workspace-fixture?view=${view}${view==='models'?'#models':''}`);return page;
 }
 const overflow=async page=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth),0,'no document horizontal overflow');
 for(const [width,height] of [[320,568],[390,844],[820,1180],[1024,768],[1440,1000]])for(const theme of ['light','dark']){
  const page=await pageFor(width,height,'models','ko',theme);await expect(page.locator('[data-model-row]')).toHaveCount(5);
  if(width<960){const toggle=page.locator('[data-model-filter-toggle]');await expect(toggle).toHaveAttribute('aria-expanded','false');await expect(page.locator('[data-model-filter-fields]')).toBeHidden();await toggle.click();await expect(page.locator('[data-model-filter-fields]')).toBeVisible();await toggle.click();}
  else await expect(page.locator('[data-model-filter-fields]')).toBeVisible();
  await overflow(page);if(out)await page.screenshot({path:`${out}/models-${width}-${theme}.png`});checks.push(`model filters ${width}x${height} ${theme}`);await page.close();
 }
 for(const locale of ['en','ja']){const page=await pageFor(390,844,'models',locale);await expect(page.locator('[data-model-row]')).toHaveCount(5);const t=JSON.parse(readFileSync(`src/i18n/messages.${locale}.json`,'utf8'));await expect(page.locator('[data-model-filter-toggle]')).toHaveText(t['필터']);await overflow(page);checks.push(`model filter locale ${locale}`);await page.close();}
 for(const kind of ['characters','conversations']){const list=await pageFor(1440,1000,kind);await expect(list.locator('[data-resource-card]')).toHaveCount(1);await list.locator('[data-resource-preview]').click();await expect(list.locator('.resource-inspector')).toBeVisible();await expect(list.locator('.resource-inspector')).toContainText(kind==='characters'?'Synthetic preview only':'Synthetic world');if(out)await list.screenshot({path:`${out}/${kind}-preview-1440.png`});await list.locator('.resource-inspector').getByRole('button',{name:'닫기',exact:true}).click();await expect(list.locator('.resource-inspector')).toHaveCount(0);await expect(list.locator('[data-resource-preview]')).toBeFocused();await list.locator('[data-resource-card] > button').first().click();await expect(list.locator('[data-navigation]')).toHaveText(kind==='characters'?'/characters/character-a':'/conversations/room-a');checks.push(`${kind} desktop preview keeps primary navigation and restores focus`);await overflow(list);await list.close();}
 for(const width of [320,390,820,1440]) {
  const page=await pageFor(width,width===320?568:1000,'assets');
  await page.locator('.asset-grid button').first().click();
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
 const stats=await pageFor(1440,1000,'statistics');await expect(stats.locator('[data-usage-summary]')).toHaveCount(1);await expect(stats.locator('[data-usage-summary] dd').first()).toHaveText('—');await expect(stats.locator('[data-usage-summary] dd').nth(1)).toHaveText('0');await overflow(stats);checks.push('statistics unknown and measured zero remain distinct');await stats.close();
 assert.deepEqual(writes,[],'no mutation requests sent');assert.deepEqual(errors,[],'no page errors');console.log(JSON.stringify({status:'PASS',checks:checks.length,cases:checks,pageErrors:errors,mutationRequests:writes}));if(out)writeFileSync(`${out}/result.json`,JSON.stringify({status:'PASS',checks,cases:checks.length,pageErrors:errors,mutationRequests:writes},null,2)+'\n');
}finally{await browser?.close();await server.close()}
