import assert from 'node:assert/strict';
import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {createServer} from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';
import {chromium,expect} from '@playwright/test';

const out=process.env.UX_ARTIFACT_DIR;if(out)mkdirSync(out,{recursive:true});
const external=process.env.UX_BASE_URL;
const plugin={name:'home-task-flow',configureServer(server){server.middlewares.use(async(req,res,next)=>{if(!/^\/(?:$|conversations(?:\/|$)|characters(?:\/|$)|world-settings(?:\/|$)|presets(?:\/|$)|model-settings)/.test(req.url))return next();res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml(req.url,'<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/hardening-browser.fixture.tsx"></script></body></html>'));});}};
const server=external?null:await createServer({configFile:false,envFile:false,plugins:[plugin,react(),tailwind()],server:{host:'127.0.0.1',port:0,watch:null,hmr:false},logLevel:'error'});
let browser;const cases=[],errors=[],unexpectedWrites=[];
try{
 if(server)await server.listen();const origin=external||`http://127.0.0.1:${server.httpServer.address().port}`;
 browser=await chromium.launch({headless:true});
 for(const [width,height]of [[320,568],[390,844],[768,1024],[820,1180],[1024,768],[1180,820],[1440,1000]]){
  const page=await browser.newPage({viewport:{width,height}});page.on('pageerror',e=>errors.push(e.message));
  const theme=width===1024?'dark':'light';await page.addInitScript(theme=>{localStorage.setItem('lorechat-language','ko');localStorage.setItem('lorely-chat-theme',theme);},theme);
  const characters=Array.from({length:5},(_,i)=>({id:`c${i}`,name:`Synthetic character ${i}`,persona:'Authored persona',description:'An authored character description',message_count:10,room_count:2}));
  const worlds=Array.from({length:4},(_,i)=>({id:`w${i}`,title:`Synthetic world ${i}`,genre_mode:'romance',enabled:true}));
  const rooms=Array.from({length:12},(_,i)=>({id:`r${i}`,title:`Synthetic room ${i} with a distinguishable title`,world_setting_id:'w0',genre_mode:'romance',participants:[{id:'c0',type:'character'}],has_unread:i===1,unread_count:i===1?2:0}));
  const creates=[];let bootstrapDelay=true;
  await page.route('**/*',async route=>{
   const req=route.request(),u=new URL(req.url()),path=u.pathname;
   if(u.origin!==origin)return route.abort();
   if(!path.startsWith('/api/'))return ['GET','HEAD'].includes(req.method())?route.continue():route.abort();
   if(path==='/api/images/prepare')return route.fulfill({json:{image_url:'/images/cache/synthetic/preview.webp'}});
   if(path.startsWith('/api/images/cache/'))return route.fulfill({contentType:'image/webp',body:readFileSync('public/brand/lorely-app-icon-light.webp')});
   if(path==='/api/conversations'&&req.method()==='POST'){const payload=JSON.parse(req.postData());creates.push(payload);return route.fulfill({json:{id:'created',...payload}});}
   if(req.method()==='POST'&&path.endsWith('/read'))return route.fulfill({json:{}});
   if(!['GET','HEAD'].includes(req.method())){unexpectedWrites.push(path);return route.abort();}
   let data=[];
   if(path==='/api/conversations'){if(bootstrapDelay){bootstrapDelay=false;await new Promise(resolve=>setTimeout(resolve,600));}data=rooms;}
   else if(path==='/api/characters'||path==='/api/characters/usage-ranking')data=characters;
   else if(path==='/api/world-settings')data=worlds;
   else if(path==='/api/health'||path==='/api/ready')data={status:'ok'};
   else if(path.startsWith('/api/runtime-settings'))data={};
   else if(path.endsWith('/generation-jobs/active'))data={jobs:[]};
   else if(path.endsWith('/context'))data={scene:{summary:'Synthetic context'},memories:[]};
   else if(/^\/api\/conversations\/[^/]+$/.test(path)){const id=path.split('/').at(-1);data=id==='created'?{id,...creates.at(-1)}:rooms.find(room=>room.id===id);}
   else if(path.endsWith('/participants'))data=[{id:'c0',type:'character'}];
   return route.fulfill({json:data});
  });
  const noOverflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth),0);
  await page.goto(origin+'/');
  const newChat=page.locator('.app-actions').getByRole('button',{name:'새 대화방',exact:true});
  await expect(newChat).toBeVisible();await expect(page.locator('[data-resource-list-skeleton=home]')).toBeVisible();
  await expect(page.locator('button.home-room-link')).toHaveCount(6);
  await expect(page.locator('button.home-resource-link')).toHaveCount(6);
  await page.locator('.home-dashboard').evaluate(async node=>{await Promise.all(node.getAnimations().map(animation=>animation.finished.catch(()=>{})));});
  assert.equal(await page.locator('.recommend-scroll,.home-quick-grid').count(),0,'no competing carousel or buried duplicate start action');
  const primary=await newChat.boundingBox();assert.ok(primary.y>=0&&primary.y+primary.height<=height&&primary.height>=44);
  const {recent,resources}=await page.evaluate(()=>({recent:document.querySelector('.home-recent').getBoundingClientRect().toJSON(),resources:document.querySelector('.home-resources').getBoundingClientRect().toJSON()}));
  if(width>=1024){assert.ok(recent.x+recent.width<=resources.x+1);assert.ok(Math.abs(recent.y-resources.y)<2,JSON.stringify({width,recent,resources}));}
  else assert.ok(recent.y+recent.height<=resources.y+1,'portrait keeps reading order, not a compressed desktop sidebar');
  await noOverflow();
  if(out)await page.screenshot({path:`${out}/home-${width}.png`});
  await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));await expect(newChat).toBeInViewport();
  // Existing rooms remain the primary direct action; a label does not replace the handler.
  await page.locator('button.home-room-link').first().click();await expect(page).toHaveURL(/\/conversations\/r0$/);await expect(page.locator('.chat-workspace')).toBeVisible();
  await page.goto(origin+'/');await expect(page.locator('button.home-resource-link')).toHaveCount(6);
  await page.locator('button.home-resource-link').first().click();await expect(page).toHaveURL(/\/characters\/c0$/);
  await page.goto(origin+'/');await expect(page.locator('button.home-resource-link')).toHaveCount(6);
  await page.locator('.home-section').filter({has:page.getByRole('heading',{name:'캐릭터',exact:true})}).getByRole('button',{name:'새 캐릭터',exact:true}).click();await expect(page).toHaveURL(/\/characters\/new$/);
  await page.goto(origin+'/');await expect(page.locator('button.home-room-link')).toHaveCount(6);
  await page.locator('.app-actions').getByRole('button',{name:'새 대화방',exact:true}).click();
  const world=page.locator('select[data-world-select]');await expect(world).toBeVisible();await expect(world).toHaveValue('');
  const start=page.locator('.app-actions').getByRole('button',{name:'대화 시작',exact:true});
  await expect(page.locator('#create-world-hint')).toHaveCount(0);
  await world.selectOption('w0');
  const cast=page.locator('.conversation-participants');
  const castFields=cast.locator('.cast-character select');
  for(let i=0;i<await castFields.count();i++)await castFields.nth(i).selectOption(`c${i}`);
  const rowsBefore=await cast.locator('.conversation-cast-row').count();
  await cast.getByRole('button',{name:/추가/}).click();await expect(cast.locator('.conversation-cast-row')).toHaveCount(rowsBefore+1);
  await cast.locator('.cast-remove').last().click();await expect(cast.locator('.conversation-cast-row')).toHaveCount(rowsBefore);
  const role=cast.locator('.cast-role select').first();await role.selectOption({index:1});const chosenRole=await role.inputValue();
  assert.ok((await role.locator('option').allTextContents()).every(text=>!text.includes(' · ')),'role options are readable labels, definitions remain in disclosure');
  const summary=cast.locator('.editor-help summary');await summary.focus();await page.keyboard.press('Enter');await expect(cast.locator('.editor-help')).toHaveAttribute('open','');
  assert.equal(await cast.locator('.cast-role-help li').count(),await role.locator('option').count());await summary.click();
  const title=page.locator('.conversation-create input').first(),persona=page.locator('.conversation-create textarea');
  await title.fill('Tablet fixture room');await persona.fill('Authored persona retained');await expect(start).toBeEnabled();
  const worldBox=await world.boundingBox(),castBox=await cast.boundingBox();if(width>=1024)assert.ok(worldBox.x<castBox.x);else assert.ok(worldBox.y<castBox.y);
  if(out)await page.screenshot({path:`${out}/create-${width}.png`});
  await page.setViewportSize({width:width>=1024?820:1180,height:width>=1024?1180:820});
  await expect(title).toHaveValue('Tablet fixture room');await expect(persona).toHaveValue('Authored persona retained');await expect(world).toHaveValue('w0');await expect(role).toHaveValue(chosenRole);
  await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));await expect(start).toBeInViewport();await noOverflow();
  await start.click();await expect.poll(()=>creates.length).toBe(1);
  assert.equal(creates[0].title,'Tablet fixture room');assert.equal(creates[0].world_setting_id,'w0');assert.equal(creates[0].scene.user_description,'Authored persona retained');
  assert.equal(creates[0].participants.filter(p=>p.type==='character')[0].role,chosenRole);
  await expect(page).toHaveURL(/\/conversations\/created$/);
  cases.push({width,height,theme,home:'visible sticky primary, direct resume, explicit resource edit and create',creation:'optional world selected explicitly, contextual add/remove, keyboard role help, resize retention, exact synthetic payload'});
  await page.close();
 }
 assert.deepEqual(errors,[]);assert.deepEqual(unexpectedWrites,[]);
 const result={status:'PASS',cases,pageErrors:errors,unexpectedWrites,liveApiRequestsForwarded:0,typography:'fallback fonts; external requests blocked'};
 if(out)writeFileSync(`${out}/result.json`,JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
}finally{await browser?.close();if(server)await server.close()}
