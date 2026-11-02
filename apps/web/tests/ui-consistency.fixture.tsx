/// <reference types="vite/client" />
import { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Button } from '../src/components/atoms/button';
import { BubbleList, type ChatMessage } from '../src/components/organisms/BubbleList';
import { MessageSelectionToolbar } from '../src/components/organisms/MessageSelectionToolbar';
import { I18nProvider, useI18n } from '../src/i18n/I18nProvider';
import '../src/styles.css';
const messages: ChatMessage[] = [{id:'single',speaker_type:'user',content:'AGGREGATE',thought:'DO NOT DUPLICATE',metadata:{response_protocol:'free_body_json',raw_body:'PRIVATE RAW',private_key:'PRIVATE KEY',render_parts:[{type:'action',text:'First action'},{type:'dialogue',text:'Then speech'},{type:'thought',text:'Fictional thought'}],command_blocks:[{type:'table',title:'Panel',rows:[['A','B']]},{type:'comments',comments:[{author:'Viewer',text:'Comment'}]}]}}];
function Fixture(){
 const [room,setRoom]=useState('room-a'),[count,setCount]=useState(0);
 const threadRef=useRef<HTMLDivElement>(null!),bottomRef=useRef<HTMLDivElement>(null!);
 const {setLocale}=useI18n();
 Object.assign(window,{uiFixture:{setRoom,setLocale}});
 return <main style={{padding:8,display:'grid',gap:8}}>
  <section aria-label="Settings actions" style={{display:'flex',flexWrap:'wrap',gap:8}}>
   {(['default','secondary','ghost','destructive'] as const).map(variant=><Button key={variant} data-variant={variant} onClick={()=>setCount(c=>c+1)} variant={variant}>{variant}</Button>)}
   <Button data-disabled disabled>Disabled</Button><Button data-icon size="icon" aria-label="Icon">+</Button>
   <Button data-override className="bg-card text-foreground border-border w-full">Override</Button>
  </section>
  <section aria-label="List actions"><Button asChild variant="secondary"><a href="#fixture-link" data-link>Open fixture</a></Button></section>
  <form onSubmit={event=>{event.preventDefault();setCount(c=>c+1);}}><Button type="submit" data-submit>Save</Button><button type="button" data-native onClick={()=>setCount(c=>c+1)}>Native</button><button disabled data-native-disabled>Native disabled</button></form>
  <output data-count>{count}</output>
  <div style={{height:320,minWidth:0}}><BubbleList conversation={{id:room}} messages={messages} characters={[]} threadRef={threadRef} bottomRef={bottomRef}/></div>
  <MessageSelectionToolbar messages={messages} participantName={()=>'Fixture'} selectedMessageIds={['single']} busy={false} onDelete={()=>{throw Error('No deletion permitted');}} onCancel={()=>{}} />
 </main>;
}
createRoot(document.getElementById('root')!).render(<I18nProvider><Fixture/></I18nProvider>);
