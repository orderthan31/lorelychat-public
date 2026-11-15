import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
const text = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const transpile = source => ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React}}).outputText;
let cleanup, timer, cancelled = 0;
const env = {React, useEffect:fn=>{cleanup?.();cleanup=fn();},useI18n:()=>({t:key=>key}),Button:({variant,...props})=>React.createElement('button',props),window:{setTimeout:(fn,ms)=>{assert.equal(ms,5000);timer=fn;return 1;},clearTimeout:()=>{timer=null;cancelled++;}}};
env.ChatLoading = () => React.createElement('section', {'data-chat-loading':'',role:'status','aria-busy':true,'aria-label':'불러오는 중…'});
function module(path,names){const source=transpile(text(path)).replace(/^import .*;\n/gm,'').replace(/export /g,'');return new Function('env',`with(env){${source};return {${names.join(',')}};}`)(env);}
const {OperationFeedback,isCompletionFeedback}=module('../src/components/molecules/OperationFeedback.tsx',['OperationFeedback','isCompletionFeedback']);
const {ResourceDetailFeedback}=module('../src/components/molecules/ResourceDetailFeedback.tsx',['ResourceDetailFeedback']);
const {isChatGenerationProgress}=module('../src/utils/chatFeedback.ts',['isChatGenerationProgress']);
let passed=0;
function test(name,fn){fn();console.log(`ok ${++passed} - ${name}`);}
test('only exact translated completions are transient; compound failure and required next steps persist',()=>{
  for(const locale of ['en','ja']){const messages=JSON.parse(text(`../src/i18n/messages.${locale}.json`));const t=key=>messages[key]||key;assert.equal(isCompletionFeedback(t('캐릭터 초대 완료'),t),true);assert.equal(isCompletionFeedback(`${t('캐릭터 초대 완료')} · refresh failed`,t),false);assert.equal(isCompletionFeedback(t('아바타 업로드 완료. 저장을 눌러 캐릭터에 반영하세요.'),t),false);}
});
test('completion renders accessible dismiss control and five-second expiry; persistent error cancels expiry',()=>{
  let dismissed;const props={status:'캐릭터 초대 완료',onDismiss:expected=>{dismissed=expected;}};
  let markup=renderToStaticMarkup(React.createElement(OperationFeedback,props));assert.match(markup,/role="status"/);assert.match(markup,/aria-live="polite"/);assert.match(markup,/<button[^>]*aria-label="닫기"/);assert.match(markup,/ux-operation-toast/);timer();assert.equal(dismissed,props.status);
  dismissed='';markup=renderToStaticMarkup(React.createElement(OperationFeedback,{...props,status:'Save completed; refreshing failed'}));assert.equal(timer,null);assert.ok(cancelled>0);assert.match(markup,/refreshing failed/);assert.doesNotMatch(markup,/<button/);assert.doesNotMatch(markup,/ux-operation-toast/);
});
test('toast close callback is reachable; empty status creates no permanent Ready chrome',()=>{
  const clicked=[];const node=OperationFeedback({status:'복사 완료',onDismiss:s=>clicked.push(s)});node.props.children[1].props.onClick();assert.deepEqual(clicked,['복사 완료']);
  const markup=renderToStaticMarkup(React.createElement(OperationFeedback,{status:'',onDismiss:()=>{}}));assert.equal(markup,'');
});
test('cold chat delegates to centered loader; forms keep skeleton; errors expose retry without busy state',()=>{
  for(const variant of ['chat','form']){const markup=renderToStaticMarkup(React.createElement(ResourceDetailFeedback,{variant,onRetry:()=>{}}));assert.match(markup,/aria-busy="true"/);assert.match(markup,/aria-label="불러오는 중…"/);if(variant==='form'){assert.match(markup,/data-detail-skeleton="form"/);assert.equal((markup.match(/ux-detail-placeholder--body/g)||[]).length,3);}else{assert.match(markup,/data-chat-loading/);assert.doesNotMatch(markup,/skeleton|placeholder/);}}
  let retried=0;const node=ResourceDetailFeedback({error:'offline',onRetry:()=>retried++});node.props.children[1].props.onClick();assert.equal(retried,1);const markup=renderToStaticMarkup(node);assert.match(markup,/role="alert"/);assert.match(markup,/재시도/);assert.doesNotMatch(markup,/aria-busy|placeholder/);
});
test('actual App dismiss callback cannot erase a newer error or loading status',()=>{
  const ast=ts.createSourceFile('App.tsx',text('../src/app/App.tsx'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);const app=ast.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='App');const decl=app.body.statements.filter(ts.isVariableStatement).flatMap(n=>[...n.declarationList.declarations]).find(n=>n.name.getText(ast)==='dismissStatus');
  let status='offline';const cb=new Function('setStatus',` ${transpile(`const cb = ${decl.initializer.arguments[0].getText(ast)};`)}; return cb;`)(fn=>{status=fn(status);});cb('복사 완료');assert.equal(status,'offline');status='복사 완료';cb('복사 완료');assert.equal(status,'');
});
test('actual App keeps accepted mutation, failed room refresh and unrelated save failures distinct',()=>{
  const ast=ts.createSourceFile('App.tsx',text('../src/app/App.tsx'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  const app=ast.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='App');
  const declarations=app.body.statements.filter(ts.isVariableStatement).flatMap(n=>[...n.declarationList.declarations]);
  const initializer=name=>declarations.find(n=>n.name.getText(ast)===name).initializer.getText(ast);
  const make=new Function('env',`with(env){${transpile(`const roomFailureStatus=${initializer('roomFailureStatus')};const chatGenerationStatus=${initializer('chatGenerationStatus')};const node=${initializer('chatFeedback')};`)}return node;}`);
  const render=status=>renderToStaticMarkup(make({...env,status,roomLoadError:'refresh offline',isCompletionFeedback,isChatGenerationProgress,t:key=>key,OperationFeedback,ResourceDetailFeedback,dismissStatus:()=>{},retryConversationRefresh:()=>{},unknownSubmissionsForRoom:()=>[],selectedConversationId:'A'}));
  let markup=render('캐릭터 역할 수정 완료');assert.match(markup,/ux-operation-toast/);assert.match(markup,/ux-detail-error[^>]*><p>refresh offline/);
  markup=render('캐릭터 초대 완료 · 새로고침 실패');assert.doesNotMatch(markup,/ux-operation-toast|ux-operation-feedback/);assert.match(markup,/캐릭터 초대 완료 · 새로고침 실패/);
  markup=render('unrelated save failed');assert.match(markup,/unrelated save failed/);assert.match(markup,/refresh offline/);
  markup=render('대화 생성 중… 응답 작성 중');assert.doesNotMatch(markup,/대화 생성 중|ux-operation-feedback/);assert.match(markup,/refresh offline/);
  markup=render('대화 생성 중… remote error');assert.match(markup,/remote error/);assert.match(markup,/refresh offline/);
});
console.log(`${passed} UX feedback rendering regressions passed`);
