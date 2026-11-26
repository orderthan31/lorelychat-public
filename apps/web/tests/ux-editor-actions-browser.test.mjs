import assert from 'node:assert/strict';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { chromium, expect } from '@playwright/test';

// Production editors, synthetic props only. No application/API or provider is contacted.
const fixture = `
import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {I18nProvider} from '/src/i18n/I18nProvider';
import {Button} from '/src/components/atoms';
import {CharacterForm} from '/src/components/organisms/CharacterForm';
import {PresetForm} from '/src/components/organisms/PresetForm';
import {RuntimeSettingsPanel, ChatSettingsBubble} from '/src/components/organisms/RuntimeSettings';
import {WorldSettingsDetailView} from '/src/views/WorldSettings';
import {ChatCommandsView} from '/src/views/ChatCommands';
import {ConversationCreateView} from '/src/views/ConversationCreate';
import {ConversationEditView} from '/src/views/ConversationEdit';
import '/src/styles.css';
const kind=new URLSearchParams(location.search).get('kind');
const option={key:'synthetic',label:'Synthetic model'};
function Fixture(){
 const [draft,setDraft]=useState({id:'fixture',name:'Synthetic',title:'Synthetic',persona:'Authored persona',content:'Authored preset',world_seed:'Authored world',generation_prompt:'Authored command',postprocess_prompt:'Authored postprocess',participants:[{id:'c1',type:'character'}],world_setting_id:'w1',model_key:'synthetic',compression_model_key:'synthetic',options:[option],compression_options:[option],thinking_level:null});
 const [asset,setAsset]=useState({}); const [ids,setIds]=useState(['c1']);
 const [busy,setBusy]=useState(false); const [open,setOpen]=useState(true);
 window.snapshot=draft; window.setBusy=setBusy; window.setOpen=setOpen;
 const save=()=>{window.saved=(window.saved||0)+1;window.payload=draft;};
 const del=()=>{window.deleted=(window.deleted||0)+1;};
 const common={draft,setDraft,onSave:save,onDelete:del,saving:busy};
 let editor;
 if(kind==='character') editor=<CharacterForm {...common} assetDraft={asset} setAssetDraft={setAsset}/>;
 if(kind==='preset') editor=<PresetForm {...common}/>;
 if(kind==='runtime') editor=<RuntimeSettingsPanel setting={draft} onChange={setDraft} onSave={save} saving={busy}/>;
 if(kind==='embedded') editor=<div style={{height:'65vh',width:'min(100%, 380px)',overflowY:'auto'}} data-local-scroll><ChatSettingsBubble embedded open={open} setting={draft} onChange={setDraft} onSave={save} saving={busy} onClose={()=>setOpen(false)}/></div>;
 if(kind==='world') editor=<WorldSettingsDetailView route={{page:'worldSettingDetail',id:'fixture'}} worldSettings={[draft]} worldSettingDraft={draft} setWorldSettingDraft={setDraft} saveWorldSetting={save} deleteWorldSetting={del} busy={busy} navigate={()=>{}} uploadWorldThumbnail={()=>{}}/>;
 if(kind==='command') editor=<ChatCommandsView route={{page:'chatCommandDetail',id:'fixture'}} chatCommands={[draft]} chatCommandDraft={draft} setChatCommandDraft={setDraft} saveChatCommand={save} deleteChatCommand={del} busy={busy} navigate={()=>{}} loadChatCommands={()=>{}}/>;
 if(kind==='create') editor=<ConversationCreateView busy={busy} navigate={()=>{}} startConversationRoom={save} setConversationTitle={()=>{}} selectedWorldSettingId="w1" setSelectedWorldSettingId={()=>{}} worldSettings={[{id:'w1',title:'World'}]} scene={{}} setScene={()=>{}} setUserDescription={()=>{}} multiCharacterIds={ids} updateMultiCharacterSlot={(i,v)=>setIds(ids.map((x,n)=>n===i?v:x))} updateMultiCharacterRole={()=>{}} removeMultiCharacterSlot={(i)=>setIds(ids.filter((_,n)=>n!==i))} addMultiCharacterSlot={()=>setIds([...ids,''])} selectOptions={<><option value="">Select</option><option value="c1">Character</option></>}/>;
 if(kind==='edit') editor=<ConversationEditView busy={busy} saveConversationRoomEdit={save} roomEditDraft={draft} setRoomEditDraft={setDraft} characters={[{id:'c1',name:'Character'}]} worldSettings={[{id:'w1',title:'World'}]} applyRoomEditPreset={()=>{}}/>;
 return <><header className="app-bar" style={{minHeight:72}}><span>Synthetic shell</span>{kind==='create'&&<Button onClick={save} disabled={busy}>대화 시작</Button>}</header><main className="shell">{editor}</main></>;
}
createRoot(document.getElementById('root')).render(<I18nProvider><Fixture/></I18nProvider>);
`;
const id='/virtual-ux-editor-actions.tsx';
const plugin={name:'ux-editor-actions-fixture',resolveId(source){if(source===id)return id;},load(source){if(source===id)return fixture;},configureServer(server){server.middlewares.use('/ux-editor-actions-fixture',async(req,res)=>{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/ux-editor-actions-fixture','<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="'+id+'"></script></body></html>'));});}};
const server=await createServer({configFile:false,envFile:false,plugins:[plugin,react(),tailwindcss()],server:{host:'127.0.0.1',port:0,hmr:false,watch:null},logLevel:'error'});
let browser;
try {
 await server.listen(); browser=await chromium.launch({headless:true});
 const origin='http://127.0.0.1:'+server.httpServer.address().port;
 let cases=0;
 for(const [width,height] of [[320,568],[390,844],[820,1180],[1024,768],[1440,1000]]){
  const page=await browser.newPage({viewport:{width,height}}); const errors=[];
  page.on('pageerror',e=>{errors.push(e.message); console.error('fixture error',e.message);});
  await page.addInitScript(()=>localStorage.setItem('lorechat-language','ko'));
  await page.route('**/*',route=>{const u=new URL(route.request().url());return u.origin!==origin||u.pathname.startsWith('/api/')?route.abort():route.continue();});
  for(const kind of ['character','preset','runtime','embedded','world','command','create','edit']){
   await page.goto(origin+'/ux-editor-actions-fixture?kind='+kind);
   const bar=page.locator(kind==='create'?'.app-bar':'.editor-action-bar'); await expect(bar).toHaveCount(1);
   if(kind==='character') await page.getByRole('button',{name:'프롬프트',exact:true}).click();
   const primary=kind==='create'?page.getByRole('button',{name:'대화 시작',exact:true}):bar.getByRole('button').first();
   await expect(primary).toBeEnabled();
   assert.equal(await primary.count(),1,'single primary action');
   // User-resized long text fields and many participants exercise long-editor ownership.
   if(await page.locator('textarea').count()) await page.locator('textarea').first().evaluate(el=>{el.style.minHeight='1200px';});
   await page.evaluate(()=>{const local=document.querySelector('[data-local-scroll]');if(local)local.scrollTop=local.scrollHeight;else window.scrollTo(0,document.documentElement.scrollHeight);});
   const box=await primary.boundingBox(); assert.ok(box&&box.y>=0&&box.y+box.height<=height,kind+' save reachable '+width);
   assert.ok(box.height>=44,kind+' action touch target');
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),kind+' horizontal overflow '+width);
   const lastField=page.locator('input:not([type=file]):visible, select:visible, textarea:visible').last();
   await lastField.scrollIntoViewIfNeeded();
   const fieldBox=await lastField.boundingBox(); const barBox=await bar.boundingBox();
   assert.ok(fieldBox.y>=barBox.y+barBox.height-1,kind+' last field is not covered by actions');
   await primary.click(); await expect.poll(()=>page.evaluate(()=>window.saved)).toBe(1);
   assert.equal(await page.evaluate(()=>window.payload.thinking_level),null,'save preserves null');
   await page.evaluate(()=>window.setBusy(true)); await expect(primary).toBeDisabled();
   await page.evaluate(()=>window.setBusy(false)); await expect(primary).toBeEnabled();
   if(kind==='character'){
    const inline = page.getByRole('textbox',{name:'페르소나',exact:true});
    await inline.fill('Changed authored persona');
    if (width === 320 || width === 1440) {
      const expand = page.getByRole('button', {name:'페르소나 · 크게 쓰기',exact:true});
      await expand.click();
      const dialog = page.getByRole('dialog', {name:'페르소나',exact:true});
      const editor = dialog.getByRole('textbox');
      await expect(editor).toBeFocused();
      await expect.poll(async () => Math.round((await dialog.boundingBox()).width)).toBe(width === 320 ? width : 720);
      const input = await editor.elementHandle();
      await page.setViewportSize({width: width === 320 ? 820 : 690, height});
      await expect(editor).toBeFocused();
      assert.equal(await input.evaluate(node => node === document.activeElement), true, 'editor stays mounted across mode change');
      await page.setViewportSize({width,height});
      await expect(dialog.getByRole('button',{name:'닫기',exact:true})).toBeInViewport();
      await expect(editor).toHaveValue('Changed authored persona');
      await editor.fill('확대해서 쓴 한글 초안');
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden();
      await expect(expand).toBeFocused();
      await expect(inline).toHaveValue('확대해서 쓴 한글 초안');
      await inline.fill('Changed authored persona');
    }
    await page.getByRole('button',{name:'기본',exact:true}).click();
    await page.getByRole('button',{name:'프롬프트',exact:true}).click();
    await expect(page.getByRole('textbox',{name:'페르소나',exact:true})).toHaveValue('Changed authored persona');
   }
   if(kind==='command'){
    const nameField=page.locator('input').first();
    await nameField.fill(''); await expect(primary).toBeDisabled();
    await nameField.fill('Synthetic'); await expect(primary).toBeEnabled();
    const summary=page.locator('.editor-help summary'); await summary.focus(); await page.keyboard.press('Enter');
    await expect(page.locator('.editor-help')).toHaveAttribute('open','');
    assert.equal(await page.evaluate(()=>Object.hasOwn(window.snapshot,'context_sources')),false,'opening help does not alter inheritance');
   }
   if(kind==='world'){
    const titleField=page.locator('input').first();
    await titleField.fill(''); await expect(primary).toBeDisabled();
    await titleField.fill('Synthetic'); await expect(primary).toBeEnabled();
   }
   if(kind==='create'||kind==='edit'){
    const before=await page.locator('select').count(); await (kind==='create'?page.locator('.conversation-participants').getByRole('button',{name:/추가/}):page.getByRole('button',{name:'참여 캐릭터 · 추가',exact:true})).click();
    assert.ok(await page.locator('select').count()>before,'Add Character retains handler');
   }
   if(kind==='runtime'||kind==='embedded'){
    await page.locator('select:has(option[value="high"]):not(:has(option[value="xhigh"]))').selectOption('high');
    assert.equal(await page.evaluate(()=>window.snapshot.thinking_level),null,'unrelated runtime edit preserves inheritance');
   }
   if(kind==='embedded'){
    await expect(page.getByRole('dialog')).toHaveCount(0);
    assert.equal(await page.locator('.chat-settings-embedded').evaluate(el=>getComputedStyle(el).position),'static');
    await page.evaluate(()=>window.setOpen(false)); await expect(bar).toHaveCount(0);
   }
   assert.deepEqual(errors,[],kind+' browser errors'); cases++;
  }
  await page.close();
 }
 console.log('PASS '+cases+' synthetic editor/viewport cases; persistence, busy state, draft tabs, help, add and embedding');
} finally {await browser?.close();await server.close();}
