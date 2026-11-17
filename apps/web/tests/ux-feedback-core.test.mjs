import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const transpile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const load = source => import(`data:text/javascript;base64,${Buffer.from(transpile(source)).toString('base64')}`);
const { queryKeys } = await load(read('../src/api/queryKeys.ts'));
const { RoomWork, GenerationOwners } = await load(read('../src/utils/roomWork.ts'));
const ast = ts.createSourceFile('App.tsx', read('../src/views/ConversationDetail/hooks/useConversationSession.ts'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const app = ast.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'useConversationSession');
const names = ['fetchConversation', 'inviteCharacterToConversation', 'retryConversationRefresh'];
const declarations = app.body.statements.filter(n => ts.isFunctionDeclaration(n) && names.includes(n.name?.text)).map(n => n.getText(ast)).join('\n');
const instantiate = new Function('env', `with(env){${transpile(declarations)};return {${names.join(',')}}}`);
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; };
const flush = async () => { for(let i=0;i<30;i++) await Promise.resolve(); };
function fixture() {
  const roomWork = new RoomWork(); const visit = roomWork.enter('A');
  const env = {roomWork, readyVisitRef:{current:visit}, selectedConversationId:'A', selectedConversationIdRef:{current:'A'},
    conversation:{id:'A'}, messages:[{id:'older'},{id:'tail',failed:true}], participants:[{id:'cast'}], conversationRuntimeSetting:{unsaved:true}, conversationContext:{kept:true},
    chatSettingsOpen:true,contextDrawerOpen:true,olderPagingReady:true,speakerId:'cast',status:'',roomLoadError:'',
    nextScrollModeRef:{current:'idle'},lastMessageTailRef:{current:{conversationId:'A',tailId:'tail',length:2}},
    invitePendingRef:{current:new Set()},generationOwners:new GenerationOwners(),deletedMessageIdsRef:{current:new Map()},
    clearMessageSelection:()=>{env.selectionCleared=true;},setShowJumpToLatest:()=>{},
    unknownSubmissionsForRoom:()=>[],terminalJobsForRoom:()=>[],normalizeRuntimeSetting:v=>v,t:v=>v,locale:'ko',USER_ID:'user',EMPTY_BATTLE_CONTROL_DRAFT:{},
    resumeGeneration:()=>{},markConversationRead:async()=>{},loadBattleState:async()=>{},loadConversationContext:async()=>({}),
    conversationApi:{activeGenerationJobs:async()=>({jobs:[]}),get:async id=>({id}),messages:async()=>[{id:'tail',content:'updated'}],participants:async()=>[{id:'cast'},{id:'new'}],runtimeSetting:async()=>({saved:true}),inviteParticipant:async()=>{}},
  };
  for(const name of ['Conversation','Messages','Participants','ConversationRuntimeSetting','ConversationContext','ChatSettingsOpen','ContextDrawerOpen','OlderPagingReady','SpeakerId','Status','RoomLoadError','Busy','BattleState','BattleControlDraft','HasOlderMessages']) env[`set${name}`]=value=>{const key=name[0].toLowerCase()+name.slice(1);env[key]=typeof value==='function'?value(env[key]):value;};
  env.ownUI=()=>({setBusy:env.setBusy,setStatus:env.setStatus});
  const f=instantiate(env); Object.assign(env,f); return {env,f};
}
let passed=0;
async function test(name,fn){await fn();console.log(`ok ${++passed} - ${name}`);}
await test('same-room refresh keeps mounted data, controls, settings draft and scroll intent while pending and after success', async()=>{
  const {env,f}=fixture();const pending=deferred();env.conversationApi.activeGenerationJobs=()=>pending.promise;
  const result=f.fetchConversation('A');assert.equal(env.conversation?.id,'A');assert.equal(env.messages.length,2);assert.equal(env.chatSettingsOpen,true);assert.equal(env.contextDrawerOpen,true);assert.equal(env.selectionCleared,undefined);
  pending.resolve({jobs:[]});await result;
  assert.deepEqual(env.messages.map(m=>m.id),['older','tail']);assert.equal(env.messages[1].failed,true);assert.equal(env.conversationRuntimeSetting.unsaved,true);assert.equal(env.conversationContext.kept,true);assert.equal(env.speakerId,'cast');assert.equal(env.nextScrollModeRef.current,'preserve');
});
await test('refresh failure keeps the last usable room and offers error rather than success',async()=>{
  const {env,f}=fixture();env.conversationApi.get=async()=>{throw new Error('offline');};
  assert.equal(await f.fetchConversation('A'),false);assert.equal(env.conversation.id,'A');assert.equal(env.roomLoadError,'offline');assert.equal(env.messages.length,2);
});
await test('different-room load resets controls; active jobs are read before snapshot; old visit cannot overwrite revisit',async()=>{
  const {env,f}=fixture();env.roomWork.enter('B');env.selectedConversationIdRef.current='B';const pending=deferred();let snapshots=0;
  env.conversationApi.activeGenerationJobs=()=>pending.promise;env.conversationApi.get=async id=>{snapshots++;return {id};};
  const old=f.fetchConversation('B');assert.equal(env.conversation,null);assert.equal(env.chatSettingsOpen,false);assert.equal(snapshots,0);
  env.roomWork.enter('A');env.selectedConversationIdRef.current='A';env.conversation={id:'A',newVisit:true};pending.resolve({jobs:[]});await old;assert.equal(env.conversation.newVisit,true);assert.equal(snapshots,0);
});
await test('invite is single-flight and refresh failure is not mutation failure or false clean success',async()=>{
  const {env,f}=fixture();const post=deferred();let posts=0;env.conversationApi.inviteParticipant=()=>{posts++;return post.promise;};env.conversationApi.get=async()=>{throw new Error('offline');};
  const first=f.inviteCharacterToConversation('new');await f.inviteCharacterToConversation('new');assert.equal(posts,1);post.resolve({});await first;
  assert.match(env.status,/초대 완료/);assert.match(env.status,/새로고침 실패/);assert.doesNotMatch(env.status,/요청 실패/);assert.equal(env.roomLoadError,'offline');assert.equal(env.invitePendingRef.current.size,0);
});
// Execute the real hook with synthetic effects/setters; no server or DOM required.
const hookText=transpile(read('../src/hooks/useRouteResourceSync.ts')).replace(/^import .*;\n/gm,'').replace('export function useRouteResourceSync','function useRouteResourceSync');
function hookFixture(){let cleanup;const env={queryKeys,useQueryClient:()=>({fetchQuery:({queryFn})=>queryFn()}),useEffect:fn=>{cleanup?.();cleanup=fn();},useI18n:()=>({t:v=>v}),EMPTY_CHARACTER:{},EMPTY_PRESET:{},DEFAULT_TRAIT_SCORES:{},DEFAULT_FORBIDDEN_RULES:[],normalizeChatCommandDraft:v=>v||{},characterApi:{},settingsApi:{}};const hook=new Function('env',`with(env){${hookText};return useRouteResourceSync;}`)(env);const state={status:'',draft:{}};const options={characters:[],presets:[],setDraft:v=>{state.draft=v;},setPresetDraft:v=>{state.draft=v;},setChatCommandDraft:v=>{state.draft=v;},setCharacterAssets:()=>{},loadCharacterAssets:()=>{},setStatus:v=>{state.status=typeof v==='function'?v(state.status):v;},fetchConversation:async()=>{},loadConversationEditDraft:async()=>{}};return {env,state,run:(page,id,extra={})=>hook({...options,route:{page,id},...extra})};}
await test('remote success clears its own loading but not an intervening error; local route fast path removes stale loading',async()=>{
  const h=hookFixture();h.env.characterApi.get=async id=>({id});h.run('characterDetail','A');await flush();assert.equal(h.state.status,'');
  const pending=deferred();h.env.characterApi.get=()=>pending.promise;h.run('characterDetail','B');h.state.status='upload failed';pending.resolve({id:'B'});await flush();assert.equal(h.state.status,'upload failed');
  h.state.status='불러오는 중…';h.run('characterDetail','C',{characters:[{id:'C'}]});assert.equal(h.state.status,'');
});
await test('route hook A→B→A late success and rejection cannot replace current draft or clear its loading',async()=>{
  const h=hookFixture();const first=deferred(),second=deferred(),third=deferred();const queue=[first,second,third];h.env.characterApi.get=()=>queue.shift().promise;
  h.run('characterDetail','A');h.run('characterDetail','B');h.run('characterDetail','A');first.resolve({id:'A',old:true});second.reject(new Error('old error'));await flush();assert.equal(h.state.status,'불러오는 중…');assert.equal(h.state.draft.old,undefined);third.resolve({id:'A',fresh:true});await flush();assert.equal(h.state.status,'');assert.equal(h.state.draft.fresh,true);
});
await test('unknown acceptance keeps the room visible but blocks send readiness until reconciliation',async()=>{
  const {env,f}=fixture();env.unknownSubmissionsForRoom=()=>[{requestId:'unknown'}];env.conversationApi.generationReceipt=async()=>{throw Object.assign(new Error('missing'),{status:404});};
  await f.fetchConversation('A');assert.equal(env.conversation.id,'A');assert.equal(env.readyVisitRef.current,null);assert.match(env.roomLoadError,/does not prove/);
});
await test('read-only refresh retry clears its own failure, never repeats invitation or erases a new error',async()=>{
  const {env,f}=fixture();env.status='offline';env.roomLoadError='offline';env.conversationApi.inviteParticipant=()=>{throw Error('retry must not mutate');};
  await f.retryConversationRefresh();assert.equal(env.status,'');assert.equal(env.roomLoadError,'');
  env.status='old failure';const pending=deferred();env.conversationApi.get=()=>pending.promise;const retry=f.retryConversationRefresh();env.status='new form error';pending.resolve({id:'A'});await retry;assert.equal(env.status,'new form error');
  env.status='unrelated save failed';env.roomLoadError='offline';await f.retryConversationRefresh();assert.equal(env.status,'unrelated save failed','room retry never dismisses a preexisting unrelated failure');assert.equal(env.roomLoadError,'');
});
await test('preset and command remote/local success clear owned loading and preserve newer feedback',async()=>{
  for(const kind of ['preset','chatCommand']){
    const h=hookFixture();h.env.settingsApi.preset=async id=>({id});h.env.settingsApi.chatCommands=async()=>[{id:'A'}];h.run(`${kind}Detail`,'A');await flush();assert.equal(h.state.status,'');
    h.state.status='new error';h.run(`${kind}Detail`,'B',{[kind==='preset'?'presets':'chatCommands']:[{id:'B'}]});assert.equal(h.state.status,'new error');
  }
});
console.log(`${passed} UX feedback core regressions passed`);
