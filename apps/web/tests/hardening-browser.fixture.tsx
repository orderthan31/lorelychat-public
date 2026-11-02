/// <reference types="vite/client" />
import React, {useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {App} from '../src/app/App';
import {AppProviders} from '../src/app/AppProviders';
import {BubbleList, type ChatMessage} from '../src/components/organisms/BubbleList';
import {MessageSelectionToolbar} from '../src/components/organisms/MessageSelectionToolbar';
import {useUiStore} from '../src/stores/uiStore';
import {I18nProvider} from '../src/i18n/I18nProvider';
import {conversationApi} from '../src/api/resources';
import {http, api} from '../src/api/http';
import {RuntimeSettingsPanel} from '../src/components/organisms/RuntimeSettings';
import '../src/styles.css';
const params=new URLSearchParams(location.search);
const history: ChatMessage[]=params.has('bubbles') ? Array.from({length:10000},(_,i)=>({id:`history-${i}`,speaker_type:'user',content:`Unique ${i} narrative: `+'The garden held the scarlet ribbon, a remembered promise. '.repeat(30)})) : [];
history.push({id:'malformed',speaker_type:'user',content:'Surrounding good prose survives',assets:[null] as any,metadata:{command_blocks:[{type:'table',rows:[null]} as any,{type:'cards',cards:[{text:{bad:true}}]} as any]}});
history.push({id:'huge',speaker_type:'user',content:'UNIQUE-LONG-START **'+ 'Carefully held memory & expressive detail. '.repeat(2500)+'** UNIQUE-LONG-END',metadata:{command_blocks:[{type:'table',rows:Array.from({length:1000},(_,i)=>[`unique cell ${i}`,'value'])}]}});
function Bubbles(){const threadRef=useRef<HTMLDivElement>(null!),bottomRef=useRef<HTMLDivElement>(null!);const [messages,setMessages]=useState(history);Object.assign(window,{hardening:{count:()=>messages.length,snapshot:()=>({rows:messages.length,characters:messages.reduce((n,m)=>n+(m.content?.length||0),0),images:useUiStore.getState().renderChatImages}),theme:()=>useUiStore.getState().toggleTheme(),setMessages}});return <main style={{height:600}}><style>{`.chat-thread{height:520px!important;overflow-y:auto!important;display:flex;flex-direction:column}`}</style><BubbleList messages={messages} characters={[]} conversation={{id:'synthetic'}} threadRef={threadRef} bottomRef={bottomRef}/><MessageSelectionToolbar messages={messages} selectedMessageIds={['history-0','huge']} participantName={()=>'Synthetic'} busy={false} onDelete={()=>{}} onCancel={()=>{}}/></main>}
Object.assign(window,{contract:{conversationApi,http,api}});
function UnavailableSettings(){const [setting,setSetting]=useState<any>({fallback_model_key:'requested-chat',compression_fallback_model_key:'requested-compression',chat_fallback_status:'unavailable',compression_fallback_status:'unavailable'});Object.assign(window,{hardeningSettings:()=>setting});return <RuntimeSettingsPanel setting={setting} onChange={setSetting}/>;}
if(params.has('bubbles'))createRoot(document.getElementById('root')!).render(<I18nProvider><Bubbles/></I18nProvider>);
else if(params.has('unavailable-settings'))createRoot(document.getElementById('root')!).render(<I18nProvider><UnavailableSettings/></I18nProvider>);
else createRoot(document.getElementById('root')!).render(<AppProviders><App/></AppProviders>);
