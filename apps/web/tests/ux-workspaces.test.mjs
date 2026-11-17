import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import vm from 'node:vm';

// Exercise production formatting, not a reimplementation: unknown usage is not zero.
const source = ts.createSourceFile('Statistics.tsx',readFileSync('src/views/Statistics/index.tsx','utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
let expression;
function find(node){if(!expression&&ts.isVariableDeclaration(node)&&node.name.getText(source)==='statValue')expression=node.initializer.getText(source);ts.forEachChild(node,find)}find(source);assert.ok(expression);
const context=vm.createContext({locale:'en',Intl,Number});vm.runInContext(ts.transpileModule(`globalThis.formatUsage=${expression}`,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,context);
for(const value of [undefined,null,NaN])assert.equal(context.formatUsage(value),'—','unknown usage must not claim measured zero');
assert.equal(context.formatUsage(0),'0','a real measured zero stays zero');assert.equal(context.formatUsage(1280),'1,280');
console.log('PASS production usage formatter: unknown is not measured zero');
