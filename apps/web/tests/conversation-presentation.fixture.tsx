import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ConversationDetailView } from '../src/views/ConversationDetail';
import { useConversationComposer } from '../src/views/ConversationDetail/hooks/useConversationComposer';
import { useMessageActions } from '../src/views/ConversationDetail/hooks/useMessageActions';
import type { BattleControlDraft, ChatConversation } from '../src/views/ConversationDetail/contracts';
import type { ChatMessage } from '../src/components/organisms/BubbleList';
import { I18nProvider, useI18n } from '../src/i18n/I18nProvider';
import { RoomWork } from '../src/utils/roomWork';
import '../src/styles.css';

const noop = () => {};
const characters = [{ id: 'c1', name: 'First fighter' }, { id: 'c2', name: 'Second fighter' }];
const commands = [{ id: 'cmd', name: 'look', display_name: 'Look', description: 'Look around', enabled: true }];
const events: unknown[] = [];
function Fixture() {
  const { t, locale } = useI18n();
  const roomWork = useRef(new RoomWork()).current;
  useLayoutEffect(() => { const visit = roomWork.enter('fixture'); return () => visit.cancel(); }, []);
  const [conversation, setConversation] = useState<ChatConversation | null>({ id: 'fixture', title: 'Presentation fixture', genre_mode: 'battle', active_command_ids: ['cmd'] });
  const [messages, setMessages] = useState<ChatMessage[]>([
    { id: 'm1', speaker_type: 'character', speaker_id: 'c1', content: 'Semantic speech', action: 'Semantic action', thought: 'Semantic thought' },
    { id: 'm2', speaker_type: 'character', speaker_id: 'c2', content: 'Free body', metadata: { response_protocol: 'free_body_json', raw_body: 'Free body', render_parts: [{ type: 'dialogue', text: 'Free body' }] } },
  ]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('Ready');
  const [compose, setCompose] = useState('first line\nsecond line\nthird line\nfourth line');
  const [composeExpanded, setComposeExpanded] = useState(false);
  const [chatSettingsOpen, setChatSettingsOpen] = useState(false);
  const [speakerId, setSpeakerId] = useState('u1');
  const [battleControlDraft, setBattleControlDraft] = useState<BattleControlDraft>({ action: 'none', progress_balance: 0.5 });
  const [renderChatImages, setRenderChatImages] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null!), endRef = useRef<HTMLDivElement>(null!);
  const composerTextareaRef = useRef<HTMLTextAreaElement>(null!);
  const nextScrollModeRef = useRef('idle');
  const sendConversationMessage = () => { events.push(['send', compose, speakerId]); };
  const composer = useConversationComposer({ compose, composerTextareaRef, sendConversationMessage });
  const actions = useMessageActions({
    roomWork, selectedConversationId: 'fixture', busy, setBusy, setStatus, setMessages, nextScrollModeRef,
    contextDrawerOpen: false, loadConversationContext: async () => {}, loadBattleState: async () => {},
    confirmLocalizedDanger: async () => false, locale, t,
    updateMessage: async (room, id, draft) => { events.push(['edit', room, id, draft]); return { id, ...draft }; },
    deleteMessage: async () => {}, bulkDeleteMessages: async () => {},
  });
  Object.assign(window, { presentation: { ...actions, events, setBusy, compose, composeExpanded, battleControlDraft, conversation, setMessages } });
  // Parent passive effects run after the child's Escape-listener replacement.
  // A disabled DOM control alone does not prove that listener has committed.
  useEffect(() => { Object.assign(window, { presentationBusyEffectsCommitted: busy }); }, [busy]);
  return <ConversationDetailView
    {...actions} {...composer}
    conversation={conversation} setConversation={setConversation} selectedConversationId="fixture" status={status}
    navigate={(path) => events.push(['navigate', path])} openConversationContextDrawer={() => events.push(['context'])}
    busy={busy} messages={messages} characters={characters} participants={[]} setAvatarPreview={noop}
    chatEndRef={endRef} chatThreadRef={threadRef} loadOlderMessages={noop} hasOlderMessages={false} loadingOlderMessages={false} olderPagingReady={false}
    retryMessage={(message) => events.push(['retry', message.id])} renderChatImages={renderChatImages}
    generateMessageTts={(message) => events.push(['tts', message.id])} regenerateFromMessage={(message) => events.push(['regenerate', message.id])}
    setShowJumpToLatest={noop} showJumpToLatest={true} scrollToLatest={() => events.push(['latest'])}
    sendConversationMessage={sendConversationMessage} speakerId={speakerId} setSpeakerId={setSpeakerId} speakerOptions={['u1', 'c1']}
    participantName={(id) => characters.find((c) => c.id === id)?.name || 'User'} setChatSettingsOpen={setChatSettingsOpen} chatSettingsOpen={chatSettingsOpen}
    conversationRuntimeSetting={{}} setConversationRuntimeSetting={noop} saveConversationRuntimeSetting={noop}
    setRenderChatImages={setRenderChatImages} compressConversationNow={noop}
    composerTextareaRef={composerTextareaRef} compose={compose} setCompose={setCompose} composeExpanded={composeExpanded} setComposeExpanded={setComposeExpanded}
    battleState={{ participants: characters }} battleControlDraft={battleControlDraft} setBattleControlDraft={setBattleControlDraft}
    chatCommands={commands} clearActiveCommand={(id) => events.push(['clear-command', id])}
  />;
}
createRoot(document.getElementById('root')!).render(<I18nProvider><Fixture /></I18nProvider>);
