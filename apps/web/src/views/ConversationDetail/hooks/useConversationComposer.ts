import { useCallback, useEffect, type KeyboardEvent, type RefObject } from 'react';

export type ConversationComposerOptions = {
  compose: string;
  composerTextareaRef: RefObject<HTMLTextAreaElement | null>;
  sendConversationMessage: () => void | Promise<void>;
};

export type ConversationComposerState = {
  resizeComposerTextarea: (node?: HTMLTextAreaElement | null) => void;
  handleComposerKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
};

export function useConversationComposer({ compose, composerTextareaRef, sendConversationMessage }: ConversationComposerOptions): ConversationComposerState {
  const resizeComposerTextarea = useCallback((node = composerTextareaRef.current) => {
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = `${Math.min(node.scrollHeight, 118)}px`;
  }, [composerTextareaRef]);

  const handleComposerKeyDown = useCallback((event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      sendConversationMessage();
    }
  }, [sendConversationMessage]);

  useEffect(() => {
    resizeComposerTextarea();
  }, [compose, resizeComposerTextarea]);

  return { resizeComposerTextarea, handleComposerKeyDown };
}
