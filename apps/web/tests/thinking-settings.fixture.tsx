import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ModelSettingsView } from '../src/views/ModelSettings';
import { ConversationComposer } from '../src/views/ConversationDetail/ConversationComposer';
import { useRuntimeDefaultSetting } from '../src/hooks/useRuntimeDefaultSetting';
import { normalizeRuntimeSettingUpdatePayload } from '../src/utils/runtimeSettings';
import { settingsApi, conversationApi } from '../src/api/resources';
import { DEFAULT_RUNTIME_SETTING } from '../src/constants/domain';
import { I18nProvider } from '../src/i18n/I18nProvider';
import type { RuntimeSettingValue } from '../src/components/organisms/RuntimeSettings';
import '../src/styles.css';

// Synthetic transport only: the browser test intercepts every API request.
function Fixture() {
  const [busy, setBusy] = useState(false);
  const [, setStatus] = useState('');
  const common = useRuntimeDefaultSetting({ setBusy, setStatus, settingsMutations: { updateRuntimeDefault: { mutateAsync: payload => settingsApi.updateRuntimeDefault(payload) } } });
  const [room, setRoom] = useState<RuntimeSettingValue>();
  const [open, setOpen] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  async function refetch() { const saved = await settingsApi.runtimeDefault(); if (saved) common.setRuntimeDefaultSetting(saved); }
  useEffect(() => { void refetch(); void conversationApi.runtimeSetting('synthetic-room').then(saved => { if (saved) setRoom(saved); }); }, []);
  if (!room) return null;
  if (!new URLSearchParams(location.search).has('room')) return <ModelSettingsView runtimeDefaultSetting={common.runtimeDefaultSetting} setRuntimeDefaultSetting={common.setRuntimeDefaultSetting} saveRuntimeDefaultSetting={common.saveRuntimeDefaultSetting} refetchRuntimeDefaultSetting={refetch} busy={busy} />;
  return <section className="chat-shell page"><div className="chat-body-wrap">Synthetic room</div><ConversationComposer
    conversation={{ id: 'synthetic-room', mode: 'user_character' }} setConversation={() => {}} busy={busy}
    sendConversationMessage={() => { throw Error('Generation forbidden in fixture'); }} speakerId="user" setSpeakerId={() => {}} speakerOptions={['user']} participantName={() => 'Synthetic user'}
    chatSettingsOpen={open} setChatSettingsOpen={setOpen} conversationRuntimeSetting={room} setConversationRuntimeSetting={setRoom}
    inheritedThinkingLevel={common.runtimeDefaultSetting.effective_thinking_level}
    saveConversationRuntimeSetting={async () => { setBusy(true); try { const saved = await conversationApi.saveRuntimeSetting('synthetic-room', normalizeRuntimeSettingUpdatePayload(room, DEFAULT_RUNTIME_SETTING)); if (saved) setRoom(saved); setOpen(false); } finally { setBusy(false); } }}
    renderChatImages={true} setRenderChatImages={() => {}} compressConversationNow={() => { throw Error('Compression forbidden in fixture'); }} composerTextareaRef={textarea}
    compose="" setCompose={() => {}} resizeComposerTextarea={() => {}} handleComposerKeyDown={() => {}} composeExpanded={false} setComposeExpanded={() => {}}
  /></section>;
}
createRoot(document.getElementById('root')!).render(<I18nProvider><main className="shell"><Fixture /></main></I18nProvider>);
