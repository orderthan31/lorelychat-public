import assert from 'node:assert/strict';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';
import { chromium, expect } from '@playwright/test';
const plugin={name:'ui-consistency-fixture',configureServer(server){server.middlewares.use('/ui-test',async(_req,res)=>{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/ui-test','<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/ui-consistency.fixture.tsx"></script></body></html>'));});}};
const server=await createServer({configFile:false,envFile:false,plugins:[plugin,react(),tailwind()],server:{host:'127.0.0.1',port:0,watch:null,hmr:false},logLevel:'error'});
let browser,passed=0;const failures=[],measurements=[];
try{
 await server.listen();browser=await chromium.launch({headless:true});
 const origin=`http://127.0.0.1:${server.httpServer.address().port}`;
 const context=await browser.newContext({viewport:{width:390,height:1000},permissions:['clipboard-read','clipboard-write']});
 const page=await context.newPage(),errors=[];page.setDefaultTimeout(5000);page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',route=>{const r=route.request(),url=new URL(r.url());return url.origin===origin&&!url.pathname.startsWith('/api/')&&r.method()==='GET'?route.continue():route.abort();});
 const check=async(name,fn)=>{try{await fn();console.log(`ok ${++passed} - ${name}`);}catch(e){failures.push(name);console.error(`FAIL ${name}: ${e.stack}`);}};
 const load=async()=>{await page.goto(origin+'/ui-test');await page.locator('[data-chat-row="single"]').waitFor();await page.evaluate(()=>window.uiFixture.setLocale('ko'));await expect(page.getByRole('button',{name:'버블 액션 메뉴',exact:true})).toHaveCount(1);};
 const copy=async()=>{await page.getByRole('button',{name:'버블 액션 메뉴',exact:true}).click();await page.locator('.bubble-action-menu').getByRole('button',{name:'복사',exact:true}).click();};
 const status=()=>page.locator('.chat-thread [role="status"]');
 const expected='First action\nThen speech\n(Fictional thought)\nPanel\nA | B\n\nLIVE 댓글\nViewer: Comment';
 await check('single menu uses real native clipboard, exact shared formatting, visible success, no mutation',async()=>{
  await load();await copy();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),expected);await expect(status()).toHaveText('복사 완료',{timeout:2000});assert.equal(await page.locator('[data-chat-row]').count(),1);assert.equal(await page.locator('.bubble-action-menu').count(),0);
 });
 await check('missing clipboard API invokes actual execCommand fallback and removes textarea',async()=>{
  await load();await page.evaluate(async()=>{await navigator.clipboard.writeText('fallback sentinel');window.clipboardReader=navigator.clipboard;Object.defineProperty(navigator,'clipboard',{value:undefined,configurable:true});});await copy();assert.equal(await page.evaluate(()=>window.clipboardReader.readText()),expected);await expect(status()).toHaveText('복사 완료',{timeout:2000});assert.equal(await page.locator('[data-clipboard-fallback]').count(),0);
 });
 await check('real insecure HTTP origin copies through native fallback with clipboard read-back',async()=>{
  await load();await page.evaluate(()=>navigator.clipboard.writeText('HTTP sentinel'));
  const insecure=await context.newPage();insecure.on('pageerror',e=>errors.push(e.message));
  await insecure.route('**/*',async route=>{const req=route.request(),url=new URL(req.url());if(url.origin!=='http://clipboard.fixture'||url.pathname.startsWith('/api/')||req.method()!=='GET')return route.abort();const response=await route.fetch({url:origin+url.pathname+url.search});return route.fulfill({response});});
  try{await insecure.goto('http://clipboard.fixture/ui-test');await insecure.locator('[data-chat-row]').waitFor();assert.deepEqual(await insecure.evaluate(()=>({secure:isSecureContext,api:typeof navigator.clipboard})),{secure:false,api:'undefined'});await insecure.getByRole('button',{name:'버블 액션 메뉴',exact:true}).click();await insecure.locator('.bubble-action-menu').getByRole('button',{name:'복사',exact:true}).click();await expect(insecure.locator('.chat-thread [role="status"]')).toHaveText('복사 완료');await page.bringToFront();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),expected);}finally{await insecure.close();}
 });
 await check('permission rejection reports localized failure, never exception details',async()=>{
  await load();await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:async()=>{throw Error('PRIVATE permission detail');}},configurable:true}));await copy();await expect(status()).toHaveText('복사 실패',{timeout:2000});assert.equal(await page.getByText('PRIVATE permission detail').count(),0);
 });
 await check('false fallback result reports failure with no leaked temporary node',async()=>{
  await load();await page.evaluate(()=>{Object.defineProperty(navigator,'clipboard',{value:undefined,configurable:true});document.execCommand=()=>false;});await copy();await expect(status()).toHaveText('복사 실패',{timeout:2000});assert.equal(await page.locator('[data-clipboard-fallback]').count(),0);
 });
 await check('room remount and late completion cannot carry feedback into another room',async()=>{
  await load();await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:()=>new Promise(resolve=>window.finishCopy=resolve)},configurable:true}));await copy();await page.evaluate(()=>window.uiFixture.setRoom('room-b'));await page.waitForTimeout(100);await page.evaluate(()=>window.finishCopy());await page.waitForTimeout(100);assert.equal((await status().allTextContents()).join(''),'');
 });
 await check('completed feedback clears on room change; superseded pending copy cannot overwrite a new menu',async()=>{
  await load();await copy();await expect(status()).toBeVisible();await page.evaluate(()=>window.uiFixture.setRoom('room-b'));await expect(status()).toHaveText('');await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:()=>new Promise(resolve=>window.finishCopy=resolve)},configurable:true}));await copy();await page.getByRole('button',{name:'버블 액션 메뉴',exact:true}).click();await page.evaluate(()=>window.finishCopy());await page.waitForTimeout(100);await expect(status()).toHaveText('');
 });
 await check('copy feedback is localized and reset when another action menu opens',async()=>{
  await load();await copy();await expect(status()).toHaveText('복사 완료',{timeout:2000});await page.evaluate(()=>window.uiFixture.setLocale('en'));await expect(status()).toHaveText('Copied');await page.getByRole('button',{name:'Bubble action menu',exact:true}).click();await expect(status()).toHaveText('');
 });
 await load();
 // Sample real resolved CSS (including color-mix) through a canvas, not source strings.
 const styles=async selector=>page.locator(selector).evaluateAll(elements=>elements.map(e=>{
  const s=getComputedStyle(e),r=e.getBoundingClientRect(),canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');canvas.width=canvas.height=1;
  const rgb=color=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=color;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data];};
  const lum=rgb=>rgb.slice(0,3).map(c=>c/255).map(c=>c<=.04045?c/12.92:((c+.055)/1.055)**2.4).reduce((sum,c,i)=>sum+c*[.2126,.7152,.0722][i],0);
  const fg=lum(rgb(s.color)),bg=lum(rgb(s.backgroundColor));return {label:e.textContent,fg:s.color,bg:s.backgroundColor,alpha:rgb(s.backgroundColor)[3],border:s.borderTopWidth,borderColor:s.borderTopColor,opacity:s.opacity,width:r.width,height:r.height,contrast:(Math.max(fg,bg)+.05)/(Math.min(fg,bg)+.05)};
 }));
 for(const width of [320,390,768,1280])for(const theme of ['light','dark'])await check(`${width}px ${theme}: atom and toolbar enabled/hover contrasts, neutral borders, geometry`,async()=>{
  await page.setViewportSize({width,height:1000});await page.evaluate(theme=>document.documentElement.classList.toggle('theme-dark',theme==='dark'),theme);await page.mouse.move(0,0);await page.waitForTimeout(180);
  const rows=await styles('[data-variant], .message-selection-toolbar button');measurements.push({width,theme,state:'rest',rows});
  for(const row of rows){assert.equal(row.alpha,255,JSON.stringify(row));assert.equal(row.border,'1px');assert.ok(row.contrast>=4.5,JSON.stringify(row));assert.ok(row.height>=44);assert.equal(row.borderColor,theme==='light'?'rgb(225, 226, 230)':'rgba(148, 163, 184, 0.18)');}
  for(const variant of ['default','secondary','ghost','destructive']){
   const button=page.locator(`[data-variant="${variant}"]`);await button.evaluate(e=>{e.disabled=true;});await expect(button).toHaveCSS('opacity','0.55');assert.equal(await button.evaluate(e=>getComputedStyle(e).opacity),'0.55');assert.equal(await button.evaluate(e=>getComputedStyle(e).pointerEvents),'none');await button.evaluate(e=>{e.disabled=false;});await page.mouse.move(0,0);await page.keyboard.press('Tab');await button.focus();assert.equal(await button.evaluate(e=>e.matches(':focus-visible')),true);const focus=await button.evaluate(e=>{const s=getComputedStyle(e);return {shadow:s.boxShadow,ring:s.getPropertyValue('--tw-ring-color'),foreground:getComputedStyle(document.documentElement).getPropertyValue('--foreground')};});measurements.push({width,theme,state:'focus',variant,...focus});assert.notEqual(focus.shadow,'none');assert.equal(focus.ring.trim(),focus.foreground.trim());await button.blur();
  }
  await page.getByRole('button',{name:'버블 액션 메뉴',exact:true}).click();const menuRows=await styles('.bubble-action-menu button:not(:disabled)');measurements.push({width,theme,state:'menu',rows:menuRows});for(const row of menuRows)assert.ok(row.contrast>=4.5,JSON.stringify(row));await page.getByRole('button',{name:'버블 액션 메뉴',exact:true}).click();
  for(const variant of ['default','secondary','ghost','destructive']){await page.locator(`[data-variant="${variant}"]`).hover();await page.waitForTimeout(180);const [row]=await styles(`[data-variant="${variant}"]`);measurements.push({width,theme,state:'hover',variant,...row});assert.ok(row.contrast>=4.5,JSON.stringify(row));}
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth),0);
 });
 await check('native semantics, disabled atom precedence, utility override, icon geometry, focus, Slot link',async()=>{
  await load();await page.locator('[data-submit]').click();await expect(page.locator('[data-count]')).toHaveText('1');await page.locator('[data-native]').click();await expect(page.locator('[data-count]')).toHaveText('2');await expect(page.locator('[data-disabled]')).toBeDisabled();await expect(page.locator('[data-native-disabled]')).toBeDisabled();
  assert.equal(await page.locator('[data-disabled]').evaluate(e=>getComputedStyle(e).opacity),'0.55');assert.equal(await page.locator('[data-native-disabled]').evaluate(e=>getComputedStyle(e).opacity),'0.72');assert.equal(await page.locator('[data-native]').evaluate(e=>getComputedStyle(e).backgroundColor),'rgba(0, 0, 0, 0)');
  const [over]=await styles('[data-override]');assert.equal(over.bg,'rgb(255, 255, 255)');assert.equal(over.fg,'rgb(32, 33, 38)');assert.equal(over.borderColor,'rgb(225, 226, 230)');assert.ok(over.width>300);
  const [icon]=await styles('[data-icon]');assert.equal(icon.width,44);assert.equal(icon.height,44);
  await page.locator('[data-variant="default"]').focus();await page.keyboard.press('Tab');assert.equal(await page.locator('[data-variant="secondary"]').evaluate(e=>e.matches(':focus-visible')),true);const shadow=await page.locator('[data-variant="secondary"]').evaluate(e=>getComputedStyle(e).boxShadow);assert.notEqual(shadow,'none');
  assert.equal(await page.locator('[data-link]').evaluate(e=>e.tagName),'A');await page.locator('[data-link]').click();assert.ok(page.url().endsWith('#fixture-link'));
 });
 console.log('CSS_MEASUREMENTS '+JSON.stringify(measurements));
 assert.deepEqual(errors,[],'No unhandled clipboard or React errors');assert.deepEqual(failures,[]);console.log(`ui consistency: ${passed} checks passed; isolated React/CSS; network writes blocked`);
}finally{await browser?.close();await server.close();}
