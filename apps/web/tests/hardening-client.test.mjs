import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const base = new URL('../', import.meta.url);
const text = (p) => readFileSync(new URL(p, base), 'utf8');
const transpile = (s) => ts.transpileModule(s, {compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const load = (s) => import(`data:text/javascript;base64,${Buffer.from(transpile(s)).toString('base64')}`);
const {RoomWork, GenerationOwners, RoomWorkCancelledError} = await load(text('src/utils/roomWork.ts'));
const source = ts.createSourceFile('App.tsx',text('src/app/App.tsx'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const app = source.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='App');
const names=['renameConversation','clearActiveCommand','compressConversationNow','updateSceneSummary','loadConversationEditDraft','loadCharacterAssets','ownUI','saveConversationRuntimeSetting','fetchConversation','stopTrackingUnknownSubmission','retryMessage','loadGeneratedJobMessages','pollGenerationJob','resumeGeneration','submitConversationPayload','saveCharacter','savePreset','patchCharacterAsset'];
const declarations=app.body.statements.filter(n=>ts.isFunctionDeclaration(n)&&names.includes(n.name?.text)).map(n=>n.getText(source)).join('\n');
const instantiate=new Function('env',`with(env){${transpile(declarations)};return {${names.join(',')}}}`);
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
let passed=0,failed=0;
async function test(name,fn){try{await fn();console.log('PASS',name);passed++}catch(e){console.error('FAIL',name,e.message);failed++}}
function setup(){const roomWork=new RoomWork();const visit=roomWork.enter('A'); const env={RoomWorkCancelledError,roomWork,generationOwners:new GenerationOwners(),readyVisitRef:{current:visit},selectedConversationId:'A',selectedConversationIdRef:{current:'A'},conversation:{id:'A'},conversationRuntimeSetting:{},messages:[{id:'old-A'}],statuses:[],busy:false,route:{page:'characterDetail'},routeId:'B',draft:{id:'A',name:'A',persona:'A'},presetDraft:{id:'A',title:'A',content:'A'},t:s=>s,normalizeRuntimeSetting:s=>s,normalizeRuntimeSettingUpdatePayload:s=>s,normalizeCharacterForApi:s=>s,DEFAULT_RUNTIME_SETTING:{},EMPTY_BATTLE_CONTROL_DRAFT:{},USER_ID:'user',nextScrollModeRef:{},lastMessageTailRef:{},activeGenerationSubscriptionRef:{},JOB_POLL_INTERVAL_MS:1,JOB_POLL_MAX_ATTEMPTS:3,JOB_PENDING_TIMEOUT_MESSAGE:'pending',unknownSubmissionsForRoom:()=>[],rememberUnknownSubmission:()=>{},forgetUnknownSubmission:()=>{},terminalJobsForRoom:()=>[],rememberTerminalJob:()=>{},forgetTerminalJob:()=>{},newClientRequestId:()=> 'request',locale:'en',formatUiCount:n=>String(n),clearMessageSelection:()=>{},loadBattleState:async()=>{},markConversationRead:async()=>{},loadConversations:async()=>{},loadCharacters:async()=>{},navigate:()=>{},revealServerMessages:async()=>{},conversationApi:{get:async()=>({id:'A'}),messages:async()=>[],participants:async()=>[],runtimeSetting:async()=>({}),activeGenerationJobs:async()=>({jobs:[]})},conversationMutations:{saveRuntimeSetting:{mutateAsync:async()=>({})},updateConversation:{mutateAsync:async()=>({id:'A'})}},characterMutations:{saveCharacter:{mutateAsync:async()=>{throw Error('should not mutate')}}}};
for(const k of ['Conversation','ConversationRuntimeSetting','Messages','Busy','RoomLoadError']) env['set'+k]=v=>{const key=k[0].toLowerCase()+k.slice(1);env[key]=typeof v==='function'?v(env[key]):v};
env.setStatus=s=>env.statuses.push(s);
for(const k of ['PresetDraft','ChatCommandDraft','ConversationThumbnailUrl','WorldSettingDraft','UserMemoryDraft','RoomEditDraft','CharacterAssets','CharacterAssetsLoading','AssetDraft','ConversationTitle','SelectedWorldSettingId','ContextDrawerOpen','OlderPagingReady','ShowJumpToLatest','HasOlderMessages','Participants','ConversationContext','BattleState','BattleControlDraft','SpeakerId','ChatSettingsOpen'])env['set'+k]=()=>{};
env.ownUI=()=>{const owner=roomWork.capture();const f={};for(const k of Object.keys(env).filter(k=>k.startsWith('set')||k==='navigate'))f[k]=(...args)=>{if(owner.current)return env[k](...args)};return f};
env.routeWork=roomWork;env.operationVersions=new Map();env.busyOwner={current:null};env.deletedMessageIdsRef={current:new Map()};
const f=instantiate(env);Object.assign(env,f);return {env,f,visit};}
await test('failed room load clears previous room snapshot',async()=>{const {env,f}=setup();env.conversationApi.get=async()=>{throw Error('503')};await f.fetchConversation('A');assert.equal(env.conversation,null);assert.deepEqual(env.messages,[]);assert.equal(env.roomLoadError,'503')});
await test('late room settings cannot install A into B or clear B busy',async()=>{const {env,f}=setup(),d=deferred();env.conversationMutations.saveRuntimeSetting.mutateAsync=()=>d.promise;const p=f.saveConversationRuntimeSetting();env.roomWork.enter('B');env.conversation={id:'B'};env.busy=true;d.resolve({});await p;assert.equal(env.conversation.id,'B');assert.equal(env.busy,true)});
await test('draft identity must match target before character mutation',async()=>{const {env,f}=setup();let calls=0;env.characterMutations.saveCharacter.mutateAsync=async()=>{calls++;return {id:'B'}};await f.saveCharacter();assert.equal(calls,0)});
await test('completed output missing IDs is explicit, never partial success',async()=>{const {env,f}=setup();env.conversationApi.messages=async()=>[{id:'incoming'}];await assert.rejects(f.loadGeneratedJobMessages('A',{id:'incoming'},{generated_message_ids:['missing']}),/missing|누락|확인/i)});
await test('permanent 404 ends polling immediately',async()=>{const {env,f}=setup();let calls=0;env.conversationApi.generationJob=async()=>{calls++;throw Object.assign(Error('missing'),{status:404})};await assert.rejects(f.pollGenerationJob('A','job'));assert.equal(calls,1)});
await test('terminal retry uses CAS job endpoint, never create',async()=>{const {env,f}=setup();let calls=[];env.conversationApi.retryGenerationJob=async(...args)=>{calls.push(args);return {id:'job',status:'failed',state_version:2}};env.conversationApi.generationJob=async()=>({status:'failed'});await f.retryMessage({id:'incoming',retryPayload:{},retryJob:{id:'job',state_version:1}});assert.equal(calls.length,1);assert.deepEqual(calls[0],['A','job',1])});
await test('ambiguous create failure retains key; next send reconciles same POST only',async()=>{const {env,f}=setup();let creates=0,replays=0;env.conversationMutations.createMessageJob={mutateAsync:async()=>{creates++;throw Error('lost response')}};await f.submitConversationPayload({content:'one input'},'local_input');assert.equal(env.generationOwners.pending.size,1);env.conversationApi.createMessageJob=async(_id,payload)=>{replays++;assert.equal(payload.metadata.client_request_id,'request');return {job:{id:'job'},incoming_message:{id:'incoming'}}};env.conversationApi.generationJob=async()=>({status:'failed',state_version:3});await f.submitConversationPayload({content:'must not create'},'local_duplicate');assert.equal(creates,1);assert.equal(replays,1);assert.equal(env.generationOwners.pending.size,0);assert.equal(env.messages.some(m=>m.id==='local_duplicate'),false)});
await test('A→B→A stale settings cannot write into newer A visit',async()=>{const {env,f}=setup(),d=deferred();env.conversationMutations.saveRuntimeSetting.mutateAsync=()=>d.promise;const p=f.saveConversationRuntimeSetting();env.roomWork.enter('B');env.roomWork.enter('A');env.conversation={id:'new-A'};env.busy=true;d.resolve({});await p;assert.equal(env.conversation.id,'new-A');assert.equal(env.busy,true)});
await test('caught character save failure keeps draft and exposes visible status',async()=>{const {env,f}=setup();env.draft.id='B';env.characterMutations.saveCharacter.mutateAsync=async()=>{throw Error('validation fields')};await f.saveCharacter();assert.equal(env.draft.persona,'A');assert.ok(env.statuses.some(s=>s.includes('validation fields')));assert.equal(env.busy,false)});
await test('completed reconciliation pages to all IDs in job order without resurrecting deleted source',async()=>{const {env,f}=setup();env.conversationApi.messages=async(_id,cursor)=>cursor?[{id:'r1'}]:[{id:'r2'}];const rows=await f.loadGeneratedJobMessages('A',{id:'deleted-source',content:'stale receipt'},{generated_message_ids:['r1','r2']});assert.deepEqual(rows.map(m=>m?.id),[undefined,'r1','r2'])});
await test('fresh app entry discovers durable job before authoritative message snapshot',async()=>{const {env,f}=setup();const order=[];env.conversationApi.activeGenerationJobs=async()=>{order.push('active');return {jobs:[{id:'durable',incoming_message_id:'source',status:'running'}]}};env.conversationApi.messages=async()=>{order.push('messages');return [{id:'source'}]};env.conversationApi.generationJob=async()=>({status:'failed',state_version:2});await f.fetchConversation('A');await new Promise(r=>setTimeout(r,0));assert.deepEqual(order.slice(0,2),['active','messages']);assert.equal(env.messages[0].failed,true);assert.equal(env.messages[0].retryJob.id,'durable')});
await test('actual mutation hook invalidates world/command sibling page keys',async()=>{const {QueryClient}=await import('@tanstack/react-query');const client=new QueryClient();for(const family of ['world-settings','chat-commands'])for(const suffix of [['include_disabled'],['page',1,'query'],['page',2,'other']])client.setQueryData([family,...suffix],[]);const hook=text('src/hooks/useSettingsMutations.ts').split('\n').filter(l=>!l.startsWith('import ')).join('\n').replace('export function useSettingsMutations','function useSettingsMutations');const factory=new Function('useQueryClient','useMutation','settingsApi','queryKeys',transpile(hook).replace('export {};','')+';return useSettingsMutations();');const mutations=factory(()=>client,x=>x,{},{});for(const key of ['saveWorldSetting','deleteWorldSetting','saveChatCommand','deleteChatCommand'])await mutations[key].onSuccess();for(const q of client.getQueryCache().getAll())assert.equal(q.state.isInvalidated,true);client.clear()});
await test('remaining production room/context/edit/assets owners reject late A→B→A writes',async()=>{
 for(const name of ['renameConversation','clearActiveCommand','compressConversationNow','updateSceneSummary','loadConversationEditDraft','loadCharacterAssets']){
  const {env,f}=setup(),d=deferred();let calls=0;const request=()=>{calls++;return d.promise};env.routeId='A';env.resourceRouteIdRef={current:'A'};env.characterApi={assets:request};env.contextDrawerOpen=false;env.loadConversationContext=async()=>{};env.sceneDraftFromContext=room=>({id:room.id,title:'source'});env.setConversations=()=>{};
  env.conversationApi.clearActiveCommand=request;env.conversationApi.compressNow=request;env.conversationApi.updateSceneSummary=request;env.conversationApi.get=request;env.conversationApi.context=async()=>({});env.conversationMutations.updateConversation.mutateAsync=request;
  const work=f[name](name==='updateSceneSummary'?'Authored summary':'A',1);await Promise.resolve();assert.equal(calls,1,`${name} must execute its actual transport`);env.roomWork.enter('B');env.roomWork.enter('A');env.conversation={id:'new-A'};env.busy=true;env.statuses=[];d.resolve(name==='loadCharacterAssets'?[]:{id:'A'});await work;assert.equal(env.conversation.id,'new-A',name);assert.equal(env.busy,true,name);assert.deepEqual(env.statuses,[],name);
 }
});
await test('unknown reload receipt finds failed job outside recent/active pages, never POSTs',async()=>{
 const {env,f}=setup();let forgotten=0,lookups=0;env.unknownSubmissionsForRoom=()=>[{conversationId:'A',requestId:'key'}];env.forgetUnknownSubmission=()=>forgotten++;env.conversationApi.generationReceipt=async(id,key)=>{lookups++;assert.deepEqual([id,key],['A','key']);return {job:{id:'old-job',status:'failed',incoming_message_id:'old-source'},incoming_message:{id:'old-source',metadata:{client_request_id:key}}}};env.conversationApi.generationJob=async()=>({status:'failed',state_version:4});await f.fetchConversation('A');await new Promise(r=>setTimeout(r,0));assert.equal(forgotten,1);assert.equal(lookups,1);assert.equal(env.messages[0].retryJob.id,'old-job');assert.equal(env.generationOwners.pending.size,0);assert.equal(env.roomLoadError,'');
});
await test('unknown completed receipt loads older output and releases owner without creating another turn',async()=>{
 const {env,f}=setup();let forgotten=0,revealed;env.unknownSubmissionsForRoom=()=>[{conversationId:'A',requestId:'key'}];env.forgetUnknownSubmission=()=>forgotten++;env.conversationApi.generationReceipt=async()=>({job:{id:'old-job',status:'completed',incoming_message_id:'old-source',generated_message_ids:['old-reply']},incoming_message:{id:'old-source'}});env.conversationApi.messages=async(_id,cursor)=>cursor?[{id:'old-source',created_at:'1'},{id:'old-reply',created_at:'2'}]:[{id:'new-source',created_at:'3'}];env.conversationApi.generationJob=async()=>({status:'completed',generated_message_ids:['old-reply']});env.revealServerMessages=async rows=>{revealed=rows};await f.fetchConversation('A');await new Promise(r=>setTimeout(r,0));assert.equal(forgotten,1);assert.deepEqual(env.messages.map(m=>m.id),['old-source','old-reply','new-source']);assert.equal(revealed[1].id,'old-reply');assert.equal(env.generationOwners.pending.size,0);
});
await test('missing receipt never clears unknown hint or permits new logical turn; deleted receipt is distinct',async()=>{
 for(const status of [404,410]){const {env,f}=setup();let forgotten=0;env.unknownSubmissionsForRoom=()=>[{conversationId:'A',requestId:'key'}];env.forgetUnknownSubmission=()=>forgotten++;env.conversationApi.generationReceipt=async()=>{throw Object.assign(Error('missing'),{status})};await f.fetchConversation('A');assert.equal(forgotten,status===410?1:0);if(status===404){assert.match(env.roomLoadError,/does not prove/);assert.equal(env.readyVisitRef.current,null);assert.equal(env.conversation,null)}else assert.equal(env.roomLoadError,'');}
});
await test('stop tracking explicitly confirms no cancellation and never writes server state; stale confirmation is inert',async()=>{
 for(const outcome of ['cancel','confirm','stale']){const {env,f}=setup();let hints=[{conversationId:'A',requestId:'key'}],forgotten=0;env.unknownSubmissionsForRoom=()=>hints;env.forgetUnknownSubmission=()=>{forgotten++;hints=[]};env.confirmDanger=async text=>{assert.match(text,/does not cancel server work/);if(outcome==='stale')env.roomWork.enter('B');return outcome!=='cancel'};await f.stopTrackingUnknownSubmission();assert.equal(forgotten,outcome==='confirm'?1:0);}
});
await test('asset PATCH failure belongs to its visit, including A→B→A', async () => {
  for (const destinations of [['B'], ['B', 'A']]) {
    const { env, f } = setup();
    const pending = deferred();
    let calls = 0;
    env.routeId = 'A';
    env.characterAssets = [{ id: 'asset-A' }];
    env.characterMutations.patchAsset = { mutateAsync: () => { calls++; return pending.promise; } };
    const work = f.patchCharacterAsset('asset-A', { enabled: false });
    assert.equal(calls, 1, 'the production mutation must actually start');
    for (const id of destinations) env.routeWork.enter(id);
    env.statuses = ['new visit status'];
    env.busy = true;
    pending.reject(new Error('old visit failure'));
    await work;
    assert.deepEqual(env.statuses, ['new visit status']);
    assert.equal(env.busy, true);
  }
});
await test('current asset PATCH failure remains visible and releases its busy owner', async () => {
  const { env, f } = setup();
  env.routeId = 'A';
  env.characterAssets = [{ id: 'asset-A' }];
  env.characterMutations.patchAsset = { mutateAsync: async () => { throw new Error('current patch rejected'); } };
  await f.patchCharacterAsset('asset-A', { enabled: false });
  assert.equal(env.statuses.length, 1);
  assert.match(env.statuses[0], /current patch rejected/);
  assert.equal(env.busy, false);
});
await test('superseded asset PATCH failure cannot overwrite or finish a newer operation', async () => {
  const { env, f } = setup();
  const oldRequest = deferred(), newRequest = deferred();
  let calls = 0;
  env.routeId = 'A';
  env.characterAssets = [{ id: 'asset-A' }];
  env.characterMutations.patchAsset = { mutateAsync: () => (++calls === 1 ? oldRequest.promise : newRequest.promise) };
  const oldWork = f.patchCharacterAsset('asset-A', { enabled: false });
  const newWork = f.patchCharacterAsset('asset-A', { priority: 60 });
  assert.equal(calls, 2);
  oldRequest.reject(new Error('superseded failure'));
  await oldWork;
  assert.deepEqual(env.statuses, []);
  assert.equal(env.busy, true);
  newRequest.reject(new Error('new operation failure'));
  await newWork;
  assert.equal(env.statuses.length, 1);
  assert.match(env.statuses[0], /new operation failure/);
  assert.equal(env.busy, false);
});
await test('asset PATCH rejects mismatched draft identity and foreign assets before transport', async () => {
  const { env, f } = setup();
  let calls = 0;
  env.characterAssets = [{ id: 'asset-A' }];
  env.characterMutations.patchAsset = { mutateAsync: async () => { calls++; } };
  await f.patchCharacterAsset('asset-A', { enabled: false });
  env.routeId = 'A';
  await f.patchCharacterAsset('foreign-asset', { enabled: false });
  assert.equal(calls, 0);
  assert.deepEqual(env.statuses, []);
  assert.equal(env.busy, false);
});
await test('production handlers using ownUI also own their bare status writes', async () => {
  for (const handler of app.body.statements.filter(ts.isFunctionDeclaration)) {
    if (!handler.body || !handler.getText(source).includes('setStatus(')) continue;
    const owned = [];
    const visit = node => {
      if (ts.isVariableDeclaration(node) && ts.isObjectBindingPattern(node.name)
          && node.initializer && ts.isCallExpression(node.initializer)
          && node.initializer.expression.getText(source) === 'ownUI') {
        owned.push(...node.name.elements.map(element => element.name.getText(source)));
      }
      ts.forEachChild(node, visit);
    };
    visit(handler.body);
    if (owned.length) assert.ok(owned.includes('setStatus'), `${handler.name.text} must capture its status owner`);
  }
});
console.log(JSON.stringify({passed,failed}));assert.equal(failed,0);
