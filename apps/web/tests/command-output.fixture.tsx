/// <reference types="vite/client" />
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ChatCommandsView } from '../src/views/ChatCommands';
import { useRouteResourceSync } from '../src/hooks/useRouteResourceSync';
import { chatCommandSavePayload, normalizeChatCommandDraft } from '../src/types/chatCommands';
import { settingsApi } from '../src/api/resources';
import { I18nProvider, useI18n } from '../src/i18n/I18nProvider';
import '../src/styles.css';

// Synthetic state only. The browser runner intercepts every API request.
function Fixture() {
  const { setLocale } = useI18n();
  (window as any).setFixtureLocale = setLocale;
  const [draft, setDraft] = useState<any>(normalizeChatCommandDraft());
  const [commands, setCommands] = useState<any[]>((window as any).commands);
  const [route, setRoute] = useState<any>({ page: 'chatCommandDetail', id: 'legacy' });
  const [status, setStatus] = useState('');
  (window as any).currentStatus = status;
  const noop = () => {};
  useRouteResourceSync({ route, characters: [], presets: [], chatCommands: commands,
    setDraft: noop, setCharacterAssets: noop, loadCharacterAssets: noop, setPresetDraft: noop,
    setChatCommandDraft: setDraft, fetchConversation: async () => {}, loadConversationEditDraft: async () => {}, setStatus });
  (window as any).openCommand = (id: string) => setRoute({ page: 'chatCommandDetail', id });
  (window as any).newCommand = () => setRoute({ page: 'chatCommandNew' });
  (window as any).currentDraft = draft;
  return <ChatCommandsView route={route} navigate={noop} chatCommands={commands} chatCommandDraft={draft} setChatCommandDraft={setDraft} loadChatCommands={noop}
    saveChatCommand={async () => {
      const saved: any = await settingsApi.saveChatCommand({ id: draft.id, payload: chatCommandSavePayload(draft) });
      setCommands(current => [...current.filter(item => item.id !== saved.id), saved]);
      setDraft(normalizeChatCommandDraft(saved));
      setRoute({ page: 'chatCommandDetail', id: saved.id });
      (window as any).saved = saved;
    }}
    deleteChatCommand={async command => {
      await settingsApi.deleteChatCommand(command.id!);
      setCommands(current => current.filter(item => item.id !== command.id));
      setDraft(normalizeChatCommandDraft());
      setRoute({ page: 'chatCommandNew' });
    }} />;
}
createRoot(document.getElementById('root')!).render(<I18nProvider><Fixture /></I18nProvider>);
