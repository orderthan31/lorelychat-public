/// <reference types="vite/client" />
import { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ConversationDetailView } from '../src/views/ConversationDetail';
import { Drawer } from '../src/components/organisms/Drawer';
import { ConversationInfoDrawer } from '../src/components/organisms/ConversationInfoDrawer';
import { I18nProvider, useI18n } from '../src/i18n/I18nProvider';
import { conversationApi } from '../src/api/resources';
import '../src/styles.css';

const initialMessages = [
  { id: 'first', speaker_type: 'character', speaker_id: 'c1', content: 'AGGREGATE must not duplicate', metadata: { response_protocol: 'free_body_json', raw_body: 'First speech', render_parts: [{type:'action',text:'First action'},{type:'dialogue',text:'First speech'},{type:'thought',text:'First thought'}] } },
  { id: 'second', speaker_type: 'user', speaker_id: 'u1', content: 'Second speech' },
  { id: 'third', speaker_type: 'storytelling', content: 'Third narration' },
];
const characters = [{id:'c1', name:'Fixture character'}];
const noop = () => {};
function Fixture() {
  const [messages, setMessages] = useState(initialMessages);
  const [mode, setMode] = useState('conversation');
  const [selection, setSelection] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [compose, setCompose] = useState('Preserved draft');
  const [status, setStatus] = useState('Ready');
  const threadRef = useRef<HTMLDivElement>(null!);
  const endRef = useRef<HTMLDivElement>(null!);
  const textareaRef = useRef<HTMLTextAreaElement>(null!);
  const {setLocale} = useI18n();
  Object.assign(window, {selectionFixture:{setMode,setBusy,setLocale,setMessages}});
  const clear = () => { setSelection(false); setSelected([]); };
  const toggle = (id: string) => setSelected(ids => ids.includes(id) ? ids.filter(i=>i!==id) : [...ids,id]);
  const enter = (id: string) => {setSelection(true);setSelected(ids => ids.includes(id)?ids:[...ids,id]);};
  const deleteSelected = async () => {
    if(!selected.length || busy || !window.confirm('Delete selected fixture messages?')) return;
    await conversationApi.bulkDeleteMessages('fixture',selected);
    setMessages(rows=>rows.filter(row=>!selected.includes(row.id)));
    clear();
  };
  if(mode==='drawer') return <Drawer route={{page:'home'}} open navigate={noop} close={noop} backendOnline />;
  if(mode==='state') return <ConversationInfoDrawer open participants={[]} characters={characters} participantName={()=>'Fixture character'} conversation={{id:'fixture',title:'Fixture'}} context={{scene:{relationship_archetype:'slow_burn',summary:'[Rolling Story Arc]\n- Fixture story'}}} />;
  return <ConversationDetailView
    conversation={{id:'fixture',title:'Selection fixture',genre_mode:'daily'}} selectedConversationId="fixture" status={status} navigate={noop} openConversationContextDrawer={noop}
    messageSelectionMode={selection} selectedMessageIds={selected} clearMessageSelection={clear} busy={busy} deleteSelectedMessages={deleteSelected}
    messages={messages} characters={characters} participants={[]} setAvatarPreview={noop} chatEndRef={endRef} chatThreadRef={threadRef}
    loadOlderMessages={noop} hasOlderMessages={false} loadingOlderMessages={false} olderPagingReady={false} retryMessage={noop} renderChatImages={false}
    generateMessageTts={noop} regenerateFromMessage={noop} deleteMessageBubble={noop} updateMessageBubble={noop} enterMessageSelectionMode={enter} toggleMessageSelection={toggle}
    setShowJumpToLatest={noop} showJumpToLatest={false} scrollToLatest={noop} sendConversationMessage={noop} speakerId="u1" setSpeakerId={noop} speakerOptions={['u1']}
    participantName={(id:string)=>id==='c1'?'Fixture character':'Fixture user'} setChatSettingsOpen={noop} chatSettingsOpen={false}
    conversationRuntimeSetting={{}} setConversationRuntimeSetting={noop} saveConversationRuntimeSetting={noop} setRenderChatImages={noop} setConversation={noop} compressConversationNow={noop}
    composerTextareaRef={textareaRef} compose={compose} setCompose={setCompose} resizeComposerTextarea={noop} handleComposerKeyDown={noop} composeExpanded={false} setComposeExpanded={noop}
    battleState={null} battleControlDraft={{}} setBattleControlDraft={noop} chatCommands={[]} clearActiveCommand={noop}
  />;
}
createRoot(document.getElementById('root')!).render(<I18nProvider><Fixture /></I18nProvider>);
