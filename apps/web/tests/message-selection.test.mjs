import assert from 'node:assert/strict';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';
import { chromium, expect } from '@playwright/test';
const fixturePlugin={name:'selection-fixture',configureServer(server){server.middlewares.use('/selection-test',async(_req,res)=>{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/selection-test','<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/message-selection.fixture.tsx"></script></body></html>'));});}};
const server=await createServer({configFile:false,envFile:false,plugins:[fixturePlugin,react(),tailwind()],server:{host:'127.0.0.1',port:0},logLevel:'error'});
let browser,passed=0;
try{
 await server.listen(); browser=await chromium.launch({headless:true});
 const origin=`http://127.0.0.1:${server.httpServer.address().port}`;
 const context=await browser.newContext({viewport:{width:390,height:844},permissions:['clipboard-read','clipboard-write']});
 const page=await context.newPage(), errors=[], writes=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',async route=>{const req=route.request(),url=new URL(req.url());
  if(url.origin!==origin)return route.abort();
  if(url.pathname.startsWith('/api/')){
   if(url.pathname==='/api/conversations/fixture/messages/bulk-delete'&&req.method()==='POST'){writes.push(req.postDataJSON());return route.fulfill({json:{deleted_count:req.postDataJSON().message_ids.length}});}
   return route.abort();
  }
  if(req.method()!=='GET')throw Error('Unexpected external write');
  return route.continue();
 });
 const check=async(name,fn)=>{await fn();console.log(`ok ${++passed} - ${name}`);};
 const load=async()=>{await page.goto(origin+'/selection-test');await page.locator('[data-chat-row="third"]').waitFor();};
 const row=id=>page.locator(`[data-chat-row="${id}"]`);
 const select=async id=>{await row(id).getByRole('button',{name:'버블 액션 메뉴',exact:true}).click();await page.getByRole('button',{name:'여러 개 선택',exact:true}).click();};
 const bar=()=>page.getByRole('toolbar',{name:'메시지 선택 동작'});
 await load();
 await check('selection replaces composer with bottom actions; cancel restores draft',async()=>{
  await select('second'); await bar().waitFor();
  assert.equal(await page.locator('form.composer').count(),0);
  for(const label of ['선택 복사','선택 삭제','선택 취소'])assert.equal(await bar().getByRole('button',{name:label,exact:true}).isVisible(),true);
  const r=await bar().boundingBox();assert.ok(r.y>700&&r.y+r.height<=845);
  await bar().getByRole('button',{name:'선택 취소',exact:true}).click();
  assert.equal(await page.locator('.composer textarea').inputValue(),'Preserved draft');
 });
 await check('copy preserves chronological ordered parts and speaker labels',async()=>{
  await select('second'); await row('first').getByRole('button',{name:'버블 선택',exact:true}).click();
  await bar().getByRole('button',{name:'선택 복사',exact:true}).click();
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'Fixture character\nFirst action\nFirst speech\n(First thought)\n\n나\nSecond speech');
  assert.equal(await bar().getByRole('status').textContent(),'복사 완료');
  assert.equal(await page.locator('.bubble.selected').count(),2);
  assert.equal(writes.length,0);
 });
 await check('empty selection disables copy/delete; busy disables destructive action and cancel',async()=>{
  for(const id of ['first','second'])await row(id).getByRole('button',{name:'버블 선택 해제',exact:true}).click();
  assert.equal(await bar().getByRole('button',{name:'선택 복사',exact:true}).isDisabled(),true);
  assert.equal(await bar().getByRole('button',{name:'선택 삭제',exact:true}).isDisabled(),true);
  await row('third').getByRole('button',{name:'버블 선택',exact:true}).click();
  await page.evaluate(()=>window.selectionFixture.setBusy(true));
  assert.equal(await bar().getByRole('button',{name:'선택 삭제',exact:true}).isDisabled(),true);
  assert.equal(await bar().getByRole('button',{name:'선택 취소',exact:true}).isDisabled(),true);
  await page.evaluate(()=>window.selectionFixture.setBusy(false));
 });
 await check('Escape exits selection without mutation',async()=>{await expect(bar().getByRole('button',{name:'선택 취소',exact:true})).toBeEnabled();await page.keyboard.press('Escape');await expect(bar()).toHaveCount(0);assert.equal(writes.length,0);});
 await check('mouse long press keeps the initially selected bubble after release',async()=>{
  const target=row('second').locator('[data-semantic-part]'); await target.scrollIntoViewIfNeeded(); const box=await target.boundingBox();
  await page.mouse.move(box.x+5,box.y+5);await page.mouse.down();await page.waitForTimeout(650);await page.mouse.up();
  assert.equal(await bar().getByRole('button',{name:'선택 삭제',exact:true}).isEnabled(),true);
  assert.equal(await row('second').locator('.bubble.selected').count(),1);
  await page.keyboard.press('Escape');
 });
 await check('scroll movement cancels long press; nested controls never start selection',async()=>{
  const target=row('second').locator('[data-semantic-part]');const box=await target.boundingBox();
  await page.mouse.move(box.x+5,box.y+5);await page.mouse.down();await page.mouse.move(box.x+25,box.y+5);await page.waitForTimeout(650);await page.mouse.up();
  assert.equal(await bar().count(),0);
  const button=row('second').getByRole('button',{name:'버블 액션 메뉴',exact:true});const b=await button.boundingBox();
  await page.mouse.move(b.x+10,b.y+10);await page.mouse.down();await page.waitForTimeout(650);await page.mouse.up();assert.equal(await bar().count(),0);
  await page.keyboard.press('Escape'); await load();
 });
 await check('selected unmounted messages remain included in chronological copy',async()=>{
  await select('first');
  await page.evaluate(()=>window.selectionFixture.setMessages(rows=>[...rows,...Array.from({length:200},(_,i)=>({id:`tail_${i}`,speaker_type:'user',content:`Tail speech ${i}`}))]));
  await page.locator('[data-total-messages="203"]').waitFor();
  await page.locator('.chat-thread').evaluate(e=>{e.scrollTop=e.scrollHeight;});
  await row('tail_199').waitFor();
  assert.equal(await row('first').count(),0);
  await row('tail_199').getByRole('button',{name:'버블 선택',exact:true}).click();
  await bar().getByRole('button',{name:'선택 복사',exact:true}).click();
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'Fixture character\nFirst action\nFirst speech\n(First thought)\n\n나\nTail speech 199');
  await load();
 });
 for(const width of [320,390,768,1280])for(const theme of ['light','dark']){
  await check(`${width}px ${theme}: bottom bar stays clickable, 44px targets, no overflow`,async()=>{
   await page.setViewportSize({width,height:844});await page.evaluate(theme=>document.documentElement.classList.toggle('theme-dark',theme==='dark'),theme);
   await select('second');
   await page.waitForTimeout(220);
   const geometry=await bar().evaluate(e=>{
    const context=document.createElement('canvas').getContext('2d');
    const sample=color=>{context.clearRect(0,0,1,1);context.fillStyle=getComputedStyle(e).backgroundColor;context.fillRect(0,0,1,1);context.fillStyle=color;context.fillRect(0,0,1,1);return [...context.getImageData(0,0,1,1).data].slice(0,3);};
    const luminance=rgb=>rgb.map(c=>c/255).map(c=>c<=.04045?c/12.92:((c+.055)/1.055)**2.4).reduce((sum,c,i)=>sum+c*[.2126,.7152,.0722][i],0);
    return {rect:e.getBoundingClientRect().toJSON(),overflow:document.documentElement.scrollWidth-innerWidth,buttons:[...e.querySelectorAll('button')].map(b=>{const r=b.getBoundingClientRect();const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);const style=getComputedStyle(b),fg=luminance(sample(style.color)),bg=luminance(sample(style.backgroundColor));return {w:r.width,h:r.height,hit:b===hit||b.contains(hit),color:style.color,opacity:style.opacity,contrast:(Math.max(fg,bg)+.05)/(Math.min(fg,bg)+.05)};})};
   });
   assert.ok(geometry.rect.bottom<=845&&geometry.rect.top>650);assert.equal(geometry.overflow,0);
   for(const b of geometry.buttons){assert.ok(b.w>=44&&b.h>=44);assert.equal(b.hit,true);assert.equal(b.opacity,'1');assert.ok(b.contrast>=4.5,`Unreadable action: ${JSON.stringify(b)}`);}
   await page.keyboard.press('Escape');
  });
 }
 await check('English/Japanese actions remain translated and fit a narrow phone',async()=>{
  await page.setViewportSize({width:320,height:844});await select('second');
  for(const [locale,name,labels] of [['en','Selected message actions',['Copy','Delete','Cancel']],['ja','選択したメッセージの操作',['コピー','削除','キャンセル']]]){
   await page.evaluate(locale=>window.selectionFixture.setLocale(locale),locale);
   const toolbar=page.getByRole('toolbar',{name});await toolbar.waitFor();
   assert.deepEqual(await toolbar.locator('button').allTextContents(),labels);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth),0);
  }
  await page.evaluate(()=>window.selectionFixture.setLocale('ko'));await bar().waitFor();await page.keyboard.press('Escape');await expect(bar()).toHaveCount(0);
 });
 await page.setViewportSize({width:390,height:844});
 await check('touch long press works with the reduced-height phone viewport',async()=>{
  await page.setViewportSize({width:390,height:420});
  const target=row('second').locator('[data-semantic-part]');await target.scrollIntoViewIfNeeded();const r=await target.boundingBox();
  const cdp=await context.newCDPSession(page);await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:r.x+5,y:r.y+5}]});await page.waitForTimeout(700);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  await expect(bar()).toBeVisible();assert.equal(await row('second').locator('.bubble.selected').count(),1);
  const rect=await bar().boundingBox();assert.ok(rect.y+rect.height<=421);
  await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:false});await cdp.detach();
  await bar().getByRole('button',{name:'선택 취소',exact:true}).click();await page.setViewportSize({width:390,height:844});
 });
 await check('delete cancel issues no request; confirmation sends exact selected IDs via real serializer',async()=>{
  await select('second');await row('third').getByRole('button',{name:'버블 선택',exact:true}).click();
  page.once('dialog',d=>d.dismiss());await bar().getByRole('button',{name:'선택 삭제',exact:true}).click();assert.equal(writes.length,0);
  page.once('dialog',d=>d.accept());await bar().getByRole('button',{name:'선택 삭제',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('.message-selection-toolbar'));
  assert.deepEqual(writes,[{message_ids:['second','third']}]);assert.equal(await row('first').count(),1);assert.equal(await row('second').count(),0);
 });
 await check('insecure-context clipboard fallback copies actual text and restores focus',async()=>{
  await load();await page.evaluate(()=>{window.nativeClipboard=navigator.clipboard;Object.defineProperty(navigator,'clipboard',{value:undefined,configurable:true});});
  await select('second');await bar().getByRole('button',{name:'선택 복사',exact:true}).click();
  assert.equal(await page.evaluate(()=>window.nativeClipboard.readText()),'나\nSecond speech');assert.equal(await bar().getByRole('status').textContent(),'복사 완료');
  assert.equal(await page.locator('[data-clipboard-fallback]').count(),0);
 });
 await check('clipboard failure is visible and never claims success',async()=>{
  await page.evaluate(()=>{document.execCommand=()=>false;});
  await bar().getByRole('button',{name:'선택 복사',exact:true}).click();
  assert.equal(await bar().getByRole('status').textContent(),'복사 실패');
 });
 await check('nonfunctional guest profile and redundant relationship badge removed; authored relationship stays',async()=>{
  await page.evaluate(()=>window.selectionFixture.setMode('drawer'));await page.locator('aside').waitFor();
  assert.equal(await page.getByLabel('유저 프로필 목업').count(),0);assert.equal(await page.getByText('게스트 유저',{exact:true}).count(),0);
  assert.equal(await page.getByRole('button',{name:'다크 모드로 전환',exact:true}).count()+await page.getByRole('button',{name:'라이트 모드로 전환',exact:true}).count(),1);
  await page.evaluate(()=>window.selectionFixture.setMode('state'));await page.getByRole('button',{name:'상태',exact:true}).click();
  assert.equal(await page.getByText('공략맛 프로필',{exact:true}).count(),0);await expect(page.getByText('관계 흐름',{exact:true})).toHaveCount(1);
 });
 assert.deepEqual(errors,[]);console.log(`message selection regression ok: ${passed} checks; synthetic API only; no live runtime`);
}finally{await browser?.close();await server.close();}
