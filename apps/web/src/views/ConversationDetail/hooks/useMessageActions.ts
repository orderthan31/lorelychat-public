import { useLayoutEffect, useMemo, useRef, type Dispatch, type SetStateAction, type MutableRefObject } from 'react';
import type { ChatMessage } from '../../../components/organisms/BubbleList';
import type { MessageEditDraft } from '../../../types/domain';
import { formatDangerConfirmation, type AppLocale, type Translator } from '../../../i18n/core';
import type { RoomVisit, RoomWork } from '../../../utils/roomWork';
import { useMessageSelection } from './useMessageSelection';

export type MessageActionsOptions = {
  roomWork: RoomWork;
  onMessagesDeleted?: (roomId: string, ids: string[]) => void;
  readyVisitRef?: MutableRefObject<RoomVisit | null>;
  selectedConversationId: string;
  busy: boolean;
  setBusy: Dispatch<SetStateAction<boolean>>;
  setStatus: Dispatch<SetStateAction<string>>;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  nextScrollModeRef: MutableRefObject<string>;
  contextDrawerOpen: boolean;
  loadConversationContext: (roomId: string) => Promise<unknown>;
  loadBattleState: (roomId: string) => Promise<unknown>;
  confirmLocalizedDanger: (message: string) => Promise<boolean>;
  locale: AppLocale;
  t: Translator;
  updateMessage: (roomId: string, messageId: string, draft: MessageEditDraft) => Promise<ChatMessage>;
  deleteMessage: (input: { conversationId: string; messageId: string }) => Promise<unknown>;
  bulkDeleteMessages: (input: { conversationId: string; messageIds: string[] }) => Promise<unknown>;
  regeneration?: {
    regenerateMessage: (input: { conversationId: string; messageId: string }) => Promise<ChatMessage[]>;
    revealServerMessages: (messages: (ChatMessage | null)[], localMessageId: string, visit: RoomVisit) => Promise<void>;
    loadConversations: () => Promise<unknown>;
  };
};

export function useMessageActions(options: MessageActionsOptions) {
  const selection = useMessageSelection(options.roomWork, options.setStatus, options.t);
  const live = useRef({ ...options, ...selection });
  const mounted = useRef(true);
  const owner = useRef<{ visit: RoomVisit; serial: number } | null>(null);
  const serial = useRef(0);
  useLayoutEffect(() => { live.current = { ...options, ...selection }; });
  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useLayoutEffect(() => {
    // App enters/cancels the RoomVisit before this effect. Release only our old
    // visit's busy reservation; its later finally must never release newer work.
    if (owner.current && !owner.current.visit.current) {
      owner.current = null;
      options.setBusy(false);
    }
  }, [options.selectedConversationId]);

  const actions = useMemo(() => {
    function capture() {
      const o = live.current;
      const visit = o.roomWork.capture();
      if (!mounted.current || !visit.current || !o.selectedConversationId || visit.roomId !== o.selectedConversationId || o.busy || owner.current) return null;
      if (o.readyVisitRef && o.readyVisitRef.current !== visit) return null;
      return { o, visit, conversationId: visit.roomId, serial: serial.current };
    }
    type Capture = NonNullable<ReturnType<typeof capture>>;
    function current(c: Capture) {
      return mounted.current && c.visit.current && live.current.roomWork.capture() === c.visit;
    }
    function begin(c: Capture) {
      if (!current(c) || live.current.busy || owner.current || serial.current !== c.serial) return null;
      if (live.current.readyVisitRef && live.current.readyVisitRef.current !== c.visit) return null;
      const work = { visit: c.visit, serial: ++serial.current };
      owner.current = work;
      c.o.setBusy(true);
      return work;
    }
    function finish(c: Capture, work: NonNullable<typeof owner.current>) {
      if (current(c) && owner.current === work) {
        owner.current = null;
        c.o.setBusy(false);
      }
    }
    async function refreshContext(c: Capture) {
      if (current(c) && c.o.contextDrawerOpen) {
        await Promise.all([c.o.loadConversationContext(c.conversationId), c.o.loadBattleState(c.conversationId)]);
      }
    }
    async function deleteSelectedMessages() {
      const c = capture();
      if (!c || !c.o.selectedMessageIds.length) return;
      const { o } = c;
      const ids = [...o.selectedMessageIds];
      if (!(await o.confirmLocalizedDanger(formatDangerConfirmation('messages', o.locale, ids.length)))) return;
      const work = begin(c);
      if (!work) return;
      const serverIds = ids.filter((id) => !id.startsWith('local_'));
      try {
        // Submitted mutations belong to the server; do not abort or redirect them.
        if (serverIds.length) await o.bulkDeleteMessages({ conversationId: c.conversationId, messageIds: serverIds });
        o.onMessagesDeleted?.(c.conversationId, ids);
        if (!current(c)) return;
        o.nextScrollModeRef.current = 'preserve';
        o.setMessages((messages) => c.visit.current ? messages.filter((item) => !ids.includes(item.id || '')) : messages);
        o.clearMessageSelection();
        await refreshContext(c);
        if (current(c)) o.setStatus(o.t('선택한 메시지 삭제 완료'));
      } catch (error) {
        if (current(c)) o.setStatus(`${o.t('선택 버블 삭제 실패 ·')} ${errorMessage(error)}`);
      } finally { finish(c, work); }
    }
    async function updateMessageBubble(message: ChatMessage, draft: MessageEditDraft) {
      const c = capture();
      const messageId = message?.id;
      if (!c || !messageId) return null;
      const { o } = c;
      const work = begin(c);
      if (!work) return null;
      try {
        const updated = await o.updateMessage(c.conversationId, messageId, draft);
        if (!current(c)) return null;
        o.nextScrollModeRef.current = 'preserve';
        o.setMessages((messages) => c.visit.current ? messages.map((item) => item.id === updated.id ? { ...item, ...updated } : item) : messages);
        o.setStatus(o.t('버블 수정 완료'));
        return updated;
      } catch (error) {
        if (current(c)) o.setStatus(`${o.t('버블 수정 실패 ·')} ${errorMessage(error)}`);
        // The edit form owns its error display. Preserve the transport rejection.
        throw error;
      } finally { finish(c, work); }
    }
    async function deleteMessageBubble(message: ChatMessage) {
      const c = capture();
      const messageId = message?.id;
      if (!c || !messageId) return;
      const { o } = c;
      if (messageId.startsWith('local_')) {
        o.nextScrollModeRef.current = 'preserve';
        o.setMessages((messages) => c.visit.current ? messages.filter((item) => item.id !== messageId) : messages);
        o.setStatus(o.t('임시 실패 버블 삭제 완료'));
        return;
      }
      if (!(await o.confirmLocalizedDanger(formatDangerConfirmation('message', o.locale)))) return;
      const work = begin(c);
      if (!work) return;
      try {
        await o.deleteMessage({ conversationId: c.conversationId, messageId });
        o.onMessagesDeleted?.(c.conversationId, [messageId]);
        if (!current(c)) return;
        o.nextScrollModeRef.current = 'preserve';
        o.setMessages((messages) => c.visit.current ? messages.filter((item) => item.id !== messageId) : messages);
        await refreshContext(c);
        if (current(c)) o.setStatus(o.t('버블 삭제 완료'));
      } catch (error) {
        if (current(c)) o.setStatus(`${o.t('버블 삭제 실패 ·')} ${errorMessage(error)}`);
      } finally { finish(c, work); }
    }
    async function regenerateFromMessage(message: ChatMessage) {
      const c = capture();
      const messageId = message?.id;
      if (!c || !messageId || messageId.startsWith('local_') || !c.o.regeneration) return;
      const { o } = c;
      const work = begin(c);
      if (!work) return;
      try {
        o.setStatus(o.t('재생성 중…'));
        const serverMessages = await o.regeneration.regenerateMessage({ conversationId: c.conversationId, messageId });
        if (!current(c)) return;
        await o.regeneration.revealServerMessages([null, ...(serverMessages || [])], `regen_${messageId}`, c.visit);
        if (!current(c)) return;
        o.setStatus(o.t('재생성 완료'));
        await o.regeneration.loadConversations();
        if (!current(c)) return;
        await refreshContext(c);
      } catch (error) {
        if (current(c)) o.setStatus(`${o.t('재생성 실패 ·')} ${errorMessage(error)}`);
      } finally { finish(c, work); }
    }
    return { deleteSelectedMessages, updateMessageBubble, deleteMessageBubble, regenerateFromMessage };
  }, []);
  return { ...selection, ...actions };
}

function errorMessage(error: unknown) { return error instanceof Error ? error.message : String(error); }
