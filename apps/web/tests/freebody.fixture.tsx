/// <reference types="vite/client" />
import { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BubbleList } from '../src/components/organisms/BubbleList';
import { I18nProvider } from '../src/i18n/I18nProvider';
import { conversationApi } from '../src/api/resources';
import { parseInputMarkup } from '../src/utils/text';
import '../src/styles.css';

// Only loaded by the isolated test server, never by the application entrypoint.
function Fixture() {
  const [messages, setMessages] = useState((window as any).fixtureMessages);
  const threadRef = useRef<HTMLDivElement>(null!);
  const bottomRef = useRef<HTMLDivElement>(null!);
  (window as any).setFixtureMessages = setMessages;
  (window as any).parseInputMarkup = parseInputMarkup;
  return <section className="chat-shell page" style={{ height: '100dvh' }}><header className="chat-header">Synthetic fixture</header><div className="chat-body-wrap"><BubbleList
    messages={messages} characters={[{ id: 'c1', name: 'Fixture character' }]} conversation={{ id: 'fixture' }}
    threadRef={threadRef} bottomRef={bottomRef}
    onGenerateTts={(message) => { (window as any).ttsRequests = [...((window as any).ttsRequests || []), message.id]; }}
    onUpdateMessage={async (message, draft) => {
      const updated = await conversationApi.updateMessage('fixture', message.id!, draft);
      (window as any).saved = updated;
    }}
  /></div><footer>Fixture composer</footer></section>;
}
createRoot(document.getElementById('root')!).render(<I18nProvider><Fixture /></I18nProvider>);
