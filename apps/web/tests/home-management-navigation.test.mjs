import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import * as icons from 'lucide-react';
const text = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const env = {AgentLauncher:()=>null,AgentSurface:()=>null,React,...icons,useEffect:()=>{},useUiStore:selector=>selector({theme:'light',toggleTheme:()=>{}}),useI18n:()=>({t:key=>key}),pageTitle:route=>route.page,lorelyHorizontalLogoUrlForTheme:()=>'/logo.svg',WebpImage:props=>React.createElement('img',props),Button:({variant,size,children,...props})=>React.createElement('button',props,children)};
function load(path,names){const source=ts.transpileModule(text(path),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React}}).outputText.replace(/^import .*;\n/gm,'').replace(/export /g,'');return new Function('env',`with(env){${source};return {${names.join(',')}};}`)(env);}
Object.assign(env,load('../src/router/managementNavigation.ts',['MANAGEMENT_NAV_ITEMS','isManagementRoute']));
Object.assign(env,load('../src/components/organisms/Drawer.tsx',['Drawer']));
const {AppShell}=load('../src/components/organisms/AppShell.tsx',['AppShell']);
for(const page of ['home','characters','worldSettings','conversations','conversationDetail','settings','modelSettings','chatCommands','presets']){
 const common={route:{page},navigate:()=>{},backendOnline:true,drawerOpen:false,setDrawerOpen:()=>{},children:'content'};
 const shell=renderToStaticMarkup(React.createElement(AppShell,common));
 const drawer=renderToStaticMarkup(React.createElement(env.Drawer,{route:{page},navigate:()=>{},backendOnline:true,open:true,close:()=>{}}));
 assert.equal(shell.includes('class="app-tool-nav"'),page==='home',`${page}: desktop home management links`);
 for(const item of env.MANAGEMENT_NAV_ITEMS){
  assert.equal(shell.includes(`href="${item.path}"`),page==='home',`${page}: desktop ${item.path}`);
  assert.equal(drawer.includes(`>${item.label}</button>`),page==='home',`${page}: mobile ${item.label}`);
 }
 assert.ok(drawer.includes('주요 메뉴')&&drawer.includes('앱 상태와 테마'));
}
console.log('PASS home-only management navigation across nine production route cases; original primary navigation and footer retained');
