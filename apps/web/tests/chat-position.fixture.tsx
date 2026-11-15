import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { useChatWindow } from '../src/hooks/useChatWindow';
import { useConversationAutoScroll, type ConversationAutoScrollOptions } from '../src/hooks/useConversationAutoScroll';

const history = Array.from({ length: 240 }, (_, i) => ({ id: `row-${i}`, content: `Synthetic message ${i}. `.repeat(i % 5 + 5) }));
function Window({ messages, threadRef, bottomRef }: any) {
  const windowed = useChatWindow(messages, threadRef);
  return <div ref={threadRef} className="fixture-thread" tabIndex={0} style={{ height: '70vh', overflowY: 'auto', overflowAnchor: 'none', width: '90vw', position: 'relative' }}>
    <div ref={windowed.listRef} data-chat-window style={{ height: windowed.total, position: 'relative' }}>
      <div style={{ position: 'absolute', top: windowed.top, width: '100%', display: 'flex', flexDirection: 'column', gap: 7 }}>
        {messages.slice(windowed.start, windowed.end).map((message: any) => <div key={message.id} data-chat-row={message.id} style={{ boxSizing: 'border-box', padding: 12, minHeight: 90, background: '#eee' }}>
          {message.content}
          {message.id === 'row-239' && <img alt="Delayed synthetic geometry" src="/fixture-delayed.svg" style={{ display: 'block', width: '100%', height: 'auto' }} />}
        </div>)}
      </div>
    </div>
    <div ref={bottomRef} />
  </div>;
}
function Fixture() {
  const [mounted, setMounted] = useState(false);
  const [messages, setMessages] = useState(history);
  const [olderPagingReady, setOlderPagingReady] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null!);
  const bottomRef = useRef<HTMLDivElement>(null!);
  const nextScrollModeRef = useRef<ConversationAutoScrollOptions['nextScrollModeRef']['current']>('initial');
  const skipNextAutoScrollRef = useRef(false);
  const lastMessageTailRef = useRef({ conversationId: '', tailId: '', length: 0 });
  useConversationAutoScroll({ route: { page: 'conversationDetail' } as any, selectedConversationId: 'fixture', messages, chatThreadRef: threadRef, chatEndRef: bottomRef, nextScrollModeRef, skipNextAutoScrollRef, lastMessageTailRef, olderPagingReady, setOlderPagingReady, setShowJumpToLatest: () => {} });
  // Deliberately commit the data before the scroller mounts, as a suspended
  // detail boundary can do. Entry must not consume intent against a null ref.
  useEffect(() => { const timer=setTimeout(()=>setMounted(true),120); return ()=>clearTimeout(timer); }, []);
  return <main>
    <button onClick={() => { nextScrollModeRef.current='preserve'; setMessages(current=>current.map(message=>({...message}))); }}>Refresh snapshot</button>
    {mounted && <Window messages={messages} threadRef={threadRef} bottomRef={bottomRef} />}
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><Fixture /></React.StrictMode>);
