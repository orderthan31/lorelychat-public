import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const transpile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { isChatGenerationProgress } = await import(`data:text/javascript;base64,${Buffer.from(transpile(read('../src/utils/chatFeedback.ts'))).toString('base64')}`);
const app = ts.createSourceFile('App.tsx', read('../src/app/App.tsx'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let generationExpression = '', operationExpression = '', headerExpression = '';
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(app) === 'chatGenerationStatus') generationExpression = node.initializer.getText(app);
  if (ts.isVariableDeclaration(node) && node.name.getText(app) === 'chatFeedback') {
    const find = child => {
      if (ts.isJsxSelfClosingElement(child) && child.tagName.getText(app) === 'OperationFeedback') operationExpression = child.attributes.properties.find(p => p.name?.getText(app) === 'status').initializer.expression.getText(app);
      ts.forEachChild(child, find);
    }; find(node);
  }
  if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(app) === 'ConversationDetailView') headerExpression = node.attributes.properties.find(p => p.name?.getText(app) === 'status').initializer.expression.getText(app);
  ts.forEachChild(node, visit);
} visit(app);
assert.ok(generationExpression && operationExpression && headerExpression);
const route = new Function('status', 't', 'isChatGenerationProgress', `const chatGenerationStatus=${generationExpression};return {header:${headerExpression},operation:${operationExpression}};`);
let cases = 0;
for (const locale of ['ko', 'en', 'ja']) {
  const messages = JSON.parse(read(`../src/i18n/messages.${locale}.json`)); const t = key => messages[key] || key;
  const progress = [t('대화 생성 job 생성 중…'), t('재생성 중…'), t('대화 생성 중…'), ...['대기 중', '응답 작성 중', '응답 재시도 중', '응답 저장 완료'].map(stage => `${t('대화 생성 중…')} ${t(stage)}`)];
  for (const status of progress) { assert.deepEqual(route(status, t, isChatGenerationProgress), { header: status, operation: '' }); cases++; }
  for (const status of ['', t('응답 저장 완료'), t('대화방 제목 변경 완료'), `${t('대화 생성 중…')} Synthetic connection lost; reconcile required`, 'Synthetic save rejected', `${t('캐릭터 초대 완료')} · Synthetic refresh failed`]) {
    assert.deepEqual(route(status, t, isChatGenerationProgress), { header: '', operation: status }); cases++;
  }
}
console.log(`PASS ${cases} localized production feedback-routing cases; no API/provider calls`);
