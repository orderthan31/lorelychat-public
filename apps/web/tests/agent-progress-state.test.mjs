import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { create } from 'zustand';
const memory = new Map();
globalThis.sessionStorage = { getItem: k => memory.get(k) || null, setItem: (k,v) => memory.set(k,v) };
const timers = new Map(); let clock = 0, tid = 0;
const schedule = (fn, delay) => { const id = ++tid; timers.set(id, { fn, at: clock + delay }); return id; };
const tick = async ms => { clock += ms; for (const [id,t] of [...timers]) if (t.at <= clock) { timers.delete(id); t.fn(); } await flush(); };
const flush = async () => { for (let i=0;i<12;i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise,resolve,reject}; };
let post, gets, posts, liveGets, maxGets;
const api = {
  message: async (_session,id) => { posts.push(id); return post.promise; },
  operation: async (id,signal) => { const d=deferred(); gets.push({id,signal,...d}); liveGets++; maxGets=Math.max(maxGets,liveGets); try { return await d.promise; } finally { liveGets--; } },
};
const code=ts.transpileModule(readFileSync(new URL('../src/agent/store.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
const module={exports:{}};
new Function('require','module','exports','setTimeout','clearTimeout',code)(n => n==='zustand'?{create}:{agentApi:api,AgentHttpError:class extends Error{}},module,module.exports,schedule,id=>timers.delete(id));
const {agentActions:actions,useAgentStore:store}=module.exports;
function reset() { post=deferred(); gets=[];posts=[];liveGets=0;maxGets=0;timers.clear();memory.clear();store.setState({working:false,pending:[],operations:{},activeMessage:null,selected:'s',sessions:{s:{id:'s',drafts:[],messages:[],busy:false}},settings:{enabled:true,llm_model_option_key:'fixture'},input:'Synthetic request'}); }
const operation=(g,stage,sequence,status='running')=>({request_id:g.id,status,kind:'message',progress:{stage,sequence},result:null,error:null});
reset();const send=actions.send();await flush();assert.equal(posts.length,1);assert.equal(gets.length,0);
await tick(1999);assert.equal(gets.length,0);await tick(1);assert.equal(gets.length,1);
await tick(10000);assert.equal(gets.length,1,'slow GET never overlaps and is not starved by timer cancellation');
gets[0].resolve({ ...operation(gets[0],'model',1), progress: { stage: 'model', sequence: 1, round: null } });await flush();assert.equal(store.getState().operations[posts[0]].progress.stage,'model');
await tick(1999);assert.equal(gets.length,1);await tick(1);assert.equal(gets.length,2);
gets[1].resolve(operation(gets[1],'search',2));await flush();
await tick(2000);gets[2].resolve(operation(gets[2],'model',1));await flush();assert.equal(store.getState().operations[posts[0]].progress.stage,'search','old sequence cannot overwrite');
await tick(2000);gets[3].resolve({...operation(gets[3],'draft',3),request_id:'wrong'});await flush();assert.equal(store.getState().operations[posts[0]].progress.stage,'search');
await tick(2000);gets[4].resolve(operation(gets[4],'read',4,'unknown'));await flush();assert.equal(store.getState().operations[posts[0]].progress,null,'unknown does not claim an accepted stage');
await tick(2000);assert.equal(gets.length,6);post.resolve({id:'s',messages:[],drafts:[],busy:false});await send;assert.equal(gets[5].signal.aborted,true);gets[5].resolve(operation(gets[5],'draft',9));await flush();await tick(10000);assert.equal(gets.length,6);assert.equal(posts.length,1);assert.equal(store.getState().activeMessage,null);assert.equal(maxGets,1);
reset();const second=actions.send();await tick(2000);store.setState({selected:'other'});assert.equal(gets[0].signal.aborted,true);gets[0].resolve(operation(gets[0],'draft',1));await flush();assert.deepEqual(store.getState().operations,{});post.reject(new Error('lost POST'));await second;await tick(10000);assert.equal(gets.length,1);assert.equal(posts.length,1);assert.equal(store.getState().pending.length,1,'lost POST keeps GET-only recovery journal');
console.log('PASS real progress owner: serialized completion-based 2s GET, delayed responses, stage/sequence/identity fences, unknown receipt, session-change abort, POST settlement cleanup, no replay.');
