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
const models=readFileSync('src/views/ModelSettings/index.tsx','utf8');
assert.match(models,/data-model-filter-toggle/,'mobile secondary filters have a discoverable toggle');
assert.match(models,/aria-expanded/,'filter state has accessible semantics');
assert.match(models,/data-model-filter-fields/,'filter content is a distinct controlled region');
assert.doesNotMatch(models,/statusBadge\(t, providerAccounts\[0\]\)/,'group readiness must not impersonate first account');
const card=readFileSync('src/components/molecules/ResourceCard.tsx','utf8');assert.match(card,/onPreview/);assert.match(card,/data-resource-preview/);
console.log('Adaptive workspaces: real unknown/zero formatter and scoped filter/preview contracts passed');
