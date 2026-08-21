import { useEffect, type Dispatch, type RefObject, type SetStateAction } from 'react';
import type { LegacyRoute } from '../router/legacyRouter';

type ScrollMode = 'idle' | 'initial' | 'append' | 'prepend' | 'preserve';
type MessageLike = { id?: string | null; [key: string]: unknown };

type MessageTailState = {
  conversationId: string;
  tailId: string;
  length: number;
};

export type ConversationAutoScrollOptions = {
  route: LegacyRoute;
  selectedConversationId: string;
  messages: MessageLike[];
  chatThreadRef: RefObject<HTMLElement | null>;
  chatEndRef: RefObject<HTMLElement | null>;
  skipNextAutoScrollRef: { current: boolean };
  nextScrollModeRef: { current: ScrollMode };
  lastMessageTailRef: { current: MessageTailState };
  olderPagingReady: boolean;
  setOlderPagingReady: Dispatch<SetStateAction<boolean>>;
  setShowJumpToLatest: Dispatch<SetStateAction<boolean>>;
};

export function useConversationAutoScroll({
  route,
  selectedConversationId,
  messages,
  chatThreadRef,
  chatEndRef,
  skipNextAutoScrollRef,
  nextScrollModeRef,
  lastMessageTailRef,
  olderPagingReady,
  setOlderPagingReady,
  setShowJumpToLatest,
}: ConversationAutoScrollOptions): void {
  useEffect(() => {
    if (route.page !== 'conversationDetail') return;
    const node = chatThreadRef.current;
    const tail = messages[messages.length - 1];
    const tailId = tail?.id || '';
    const previousTail = lastMessageTailRef.current;
    const explicitMode = nextScrollModeRef.current;
    const isNewConversation = previousTail.conversationId !== selectedConversationId;
    const appendedAtTail = previousTail.conversationId === selectedConversationId && previousTail.tailId !== tailId && messages.length >= previousTail.length;
    const shouldStickToBottom = explicitMode === 'initial' || explicitMode === 'append' || isNewConversation || appendedAtTail;

    if (skipNextAutoScrollRef.current || explicitMode === 'prepend' || explicitMode === 'preserve') {
      skipNextAutoScrollRef.current = false;
    } else if (shouldStickToBottom) {
      if (node) node.scrollTop = node.scrollHeight;
      else chatEndRef.current?.scrollIntoView({ block: 'end', behavior: 'auto' });
      setShowJumpToLatest(false);
    }

    lastMessageTailRef.current = { conversationId: selectedConversationId, tailId, length: messages.length };
    nextScrollModeRef.current = 'idle';

    if (messages.length && !olderPagingReady) {
      const timer = window.setTimeout(() => setOlderPagingReady(true), 350);
      return () => window.clearTimeout(timer);
    }
  }, [messages, selectedConversationId, route.page, olderPagingReady]);
}
