import { useEffect, useLayoutEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { conversationApi } from '../../../api/resources';
import { queryKeys } from '../../../api/queryKeys';
import { GenerationLifecycleSubscriptionClosedError, subscribeGenerationJobLifecycle } from '../../../api/generationJobEvents';
import { terminalJobsForRoom, rememberTerminalJob, forgetTerminalJob, unknownSubmissionsForRoom, rememberUnknownSubmission, forgetUnknownSubmission } from '../../../utils/generationRecovery';
import { GenerationOwners, RoomVisit, RoomWork, RoomWorkCancelledError } from '../../../utils/roomWork';
import { createAssetDecisionRefresh } from '../../../utils/assetDecisionRefresh';
import { isChatGenerationProgress } from '../../../utils/chatFeedback';
import { useConversationMutations } from '../../../hooks/useConversationMutations';
import { useConversationAutoScroll } from '../../../hooks/useConversationAutoScroll';
import type { GuardUI } from '../../../hooks/useOperationOwnership';
import type { LegacyRoute } from '../../../router/legacyRouter';
import { useI18n } from '../../../i18n/I18nProvider';
import { formatDangerConfirmation } from '../../../i18n/core';
import { useConversationComposer } from './useConversationComposer';
import { useMessageActions } from './useMessageActions';
import type { ChatMessage } from '../../../components/organisms/BubbleList';
import { normalizeRuntimeSetting, normalizeRuntimeSettingUpdatePayload } from '../../../utils/runtimeSettings';
import { assetUrlFor } from '../../../utils/assets';
import { parseInputMarkup } from '../../../utils/text';
import { BUBBLE_REVEAL_DELAY_MS, DEFAULT_RUNTIME_SETTING, SYSTEM_ID, USER_ID } from '../../../constants/domain';

type SessionOptions = {
  route: LegacyRoute;
  selectedConversationId: string;
  routeWork: RoomWork;
  guardUI: GuardUI;
  busy: boolean;
  status: string;
  setBusy: Dispatch<SetStateAction<boolean>>;
  setStatus: Dispatch<SetStateAction<string>>;
  contextDrawerOpen: boolean;
  setContextDrawerOpen: (open: boolean) => void;
  participantName: (id: string) => string;
  confirmLocalizedDanger: (message: string) => Promise<boolean>;
  confirmDanger: (message: string, options: { title: string; confirmText: string; cancelText: string }) => Promise<boolean>;
};

const JOB_POLL_INTERVAL_MS = 1200;
const JOB_POLL_MAX_ATTEMPTS = 120;
const JOB_PENDING_TIMEOUT_MESSAGE = '응답 생성 지연 중입니다. 완료 후 현재 방 재진입 때 동기화됩니다.';
const EMPTY_BATTLE_CONTROL_DRAFT = { action: 'none', advantage: 0.5, progress_balance: 0.5, current_phase: 'opening' };

function battleControlDraftFromState(state: any) {
  const active = state?.active_match;
  if (!active) return { ...EMPTY_BATTLE_CONTROL_DRAFT };
  const metadata = active.metadata || {};
  const savedAdvantage = metadata.advantage || {};
  const rawAdvantage = Number(savedAdvantage.value ?? 0.5);
  const advantage = Number.isFinite(rawAdvantage) ? Math.max(0.5, Math.min(1, rawAdvantage)) : 0.5;
  const progressBalance = savedAdvantage.favored_character_id === active.participant_a_id
    ? 1 - advantage
    : savedAdvantage.favored_character_id === active.participant_b_id
      ? advantage
      : 0.5;
  return {
    action: 'progress',
    advantage,
    progress_balance: progressBalance,
    current_phase: metadata.current_phase || 'middle',
  };
}
function newClientRequestId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return `req_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}
/** Live-room session owner. App mounts this hook once, outside routed Views.
 * Room visits cancel presentation work; generation receipts/pending ownership
 * remain in this instance across A→B→A and are reconciled rather than resent. */
export function useConversationSession({ route, selectedConversationId, routeWork, guardUI, busy, status, setBusy, setStatus, contextDrawerOpen, setContextDrawerOpen, participantName, confirmLocalizedDanger, confirmDanger }: SessionOptions) {
  const { locale, t } = useI18n();
  const conversationMutations = useConversationMutations();
  const queryClient = useQueryClient();
  const setConversations = (update: (current: any[]) => any[]) => queryClient.setQueryData<any[]>(queryKeys.conversations, current => update(current || []));
  async function loadConversations() { await queryClient.invalidateQueries({ queryKey: queryKeys.conversations }); return queryClient.getQueryData<any[]>(queryKeys.conversations) || []; }
  const [conversationContext, setConversationContext] = useState<any>(null);
  const [battleState, setBattleState] = useState<any>(null);
  const [battleControlDraft, setBattleControlDraft] = useState<any>(() => ({ ...EMPTY_BATTLE_CONTROL_DRAFT }));
  const [contextLoading, setContextLoading] = useState(false);
  const [conversation, setConversation] = useState<any>(null);
  const [participants, setParticipants] = useState<any[]>([]);
  const [messages, setMessages] = useState<any[]>([]);
  const [hasOlderMessages, setHasOlderMessages] = useState(false);
  const [loadingOlderMessages, setLoadingOlderMessages] = useState(false);
  const [olderPagingReady, setOlderPagingReady] = useState(false);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const [speakerId, setSpeakerId] = useState('');
  const [compose, setCompose] = useState('');
  const [composeExpanded, setComposeExpanded] = useState(false);
  const [conversationRuntimeSetting, setConversationRuntimeSetting] = useState<any>({ ...DEFAULT_RUNTIME_SETTING, thinking_level: null });
  const [chatSettingsOpen, setChatSettingsOpen] = useState(false);
  const [userMemoryDraft, setUserMemoryDraft] = useState<any>({ character_id: '__room__', memory_type: 'user_note', content: '', importance: 5 });
  const [roomLoadError, setRoomLoadError] = useState('');
  const invitePendingRef = useRef(new Set<string>());
  const chatEndRef = useRef<any>(null);
  const chatThreadRef = useRef<any>(null);
  const composerTextareaRef = useRef<any>(null);
  const skipNextAutoScrollRef = useRef<boolean>(false);
  const nextScrollModeRef = useRef<any>('idle');
  const lastMessageTailRef = useRef<any>({ conversationId: '', tailId: '', length: 0 });
  const generationOwners = useRef(new GenerationOwners<any>()).current;
  const roomWork = useRef(new RoomWork()).current;
  const readyVisitRef = useRef<RoomVisit | null>(null);
  const deletedMessageIdsRef = useRef(new Map<string, Set<string>>());
  const assetDecisionRefreshRef = useRef<ReturnType<typeof createAssetDecisionRefresh> | null>(null);
  const activeGenerationSubscriptionRef = useRef<any>(null);
  const selectedConversationIdRef = useRef('');
  selectedConversationIdRef.current = selectedConversationId;
  function ownUI(operation: string) { return guardUI(operation, { setConversation, setConversationRuntimeSetting, setChatSettingsOpen, setConversationContext, setUserMemoryDraft, setMessages }); }
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState !== 'visible') return;
      activeGenerationSubscriptionRef.current?.reconcile();
      void resumeGeneration();
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, [locale, route.page, selectedConversationId, t]);
  useLayoutEffect(() => {
    const visit = roomWork.enter(selectedConversationId);
    setLoadingOlderMessages(false);
    setContextLoading(false);
    return () => {
      visit.cancel();
      activeGenerationSubscriptionRef.current?.close();
      activeGenerationSubscriptionRef.current = null;
    };
  }, [selectedConversationId]);

  useLayoutEffect(() => {
    if (!selectedConversationId) return;
    const visit = roomWork.capture();
    const refresh = createAssetDecisionRefresh({
      roomId: selectedConversationId,
      isCurrent: () => visit.current && roomWork.capture() === visit,
      read: async ids => await conversationApi.messagesByIds(selectedConversationId, ids) as ChatMessage[],
      merge: update => setMessages(current => update(current)),
    });
    assetDecisionRefreshRef.current = refresh;
    const visibilityChanged = () => refresh.setVisible(document.visibilityState === 'visible');
    visibilityChanged();
    document.addEventListener('visibilitychange', visibilityChanged);
    return () => {
      refresh.stop();
      document.removeEventListener('visibilitychange', visibilityChanged);
      if (assetDecisionRefreshRef.current === refresh) assetDecisionRefreshRef.current = null;
    };
  }, [selectedConversationId]);
  useLayoutEffect(() => {
    // Do not seed a new visit from the previous room's still-loaded snapshot.
    if (readyVisitRef.current === roomWork.capture()) assetDecisionRefreshRef.current?.update(messages);
  }, [messages, selectedConversationId]);

  const { messageSelectionMode, selectedMessageIds,
    enterMessageSelectionMode, toggleMessageSelection, clearMessageSelection,
    deleteSelectedMessages, updateMessageBubble, deleteMessageBubble, regenerateFromMessage } = useMessageActions({
    roomWork, readyVisitRef, selectedConversationId, busy, setBusy, setStatus, setMessages, nextScrollModeRef,
    contextDrawerOpen, loadConversationContext, loadBattleState, confirmLocalizedDanger, locale, t,
    onMessagesDeleted: (roomId, ids) => {
      const deleted = deletedMessageIdsRef.current.get(roomId) || new Set<string>();
      ids.forEach(id => deleted.add(id)); deletedMessageIdsRef.current.set(roomId, deleted);
    },
    updateMessage: conversationApi.updateMessage,
    deleteMessage: conversationMutations.deleteMessage.mutateAsync,
    bulkDeleteMessages: conversationMutations.bulkDeleteMessages.mutateAsync,
    regeneration: {
      regenerateMessage: async (input) => await conversationMutations.regenerateMessage.mutateAsync(input) as ChatMessage[],
      revealServerMessages,
      loadConversations: async () => queryClient.getQueryData(queryKeys.conversations),
    },
  });

  const { resizeComposerTextarea, handleComposerKeyDown } = useConversationComposer({ compose, composerTextareaRef, sendConversationMessage });
  useConversationAutoScroll({
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
  });
  async function markConversationRead(conversationId) {
    if (!conversationId) return null;
    const readState = await conversationApi.markRead(conversationId).catch(() => null);
    setConversations((current) => current.map((room) => room.id === conversationId ? { ...room, has_unread: false, unread_count: 0 } : room));
    setConversation((current) => current?.id === conversationId ? { ...current, has_unread: false, unread_count: 0 } : current);
    return readState;
  }

  async function loadConversationRuntimeSetting(conversationId = selectedConversationId) {
    if (!conversationId) return null;
    const visit = roomWork.capture();
    const setting = normalizeRuntimeSetting((await conversationApi.runtimeSetting(conversationId)) || {});
    if (!visit.current || visit.roomId !== conversationId) return setting;
    setConversationRuntimeSetting(setting);
    return setting;
  }

  async function saveConversationRuntimeSetting() {
    const { setBusy, setStatus, setConversation, setConversationRuntimeSetting, setChatSettingsOpen } = ownUI('saveConversationRuntimeSetting');
    if (!selectedConversationId || readyVisitRef.current !== roomWork.capture()) return;
    setBusy(true);
    try {
      const savedSetting = await (conversationMutations.saveRuntimeSetting.mutateAsync({
        conversationId: selectedConversationId,
        payload: normalizeRuntimeSettingUpdatePayload(conversationRuntimeSetting, DEFAULT_RUNTIME_SETTING),
      }) as any);
      const savedRoom = await (conversationMutations.updateConversation.mutateAsync({ id: selectedConversationId, payload: { tts_enabled: !!conversation?.tts_enabled } }) as any);
      setConversation(savedRoom);
      setConversationRuntimeSetting(normalizeRuntimeSetting(savedSetting || conversationRuntimeSetting));
      setChatSettingsOpen(false);
    } catch (error) { setStatus(`요청 실패 · ${error.message}`); } finally { setBusy(false); }
  }

  async function loadConversationContext(conversationId = selectedConversationId) {
    if (!conversationId) return null;
    const visit = roomWork.capture();
    if (!visit.current || visit.roomId !== conversationId) return null;
    setContextLoading(true);
    try {
      const context = await conversationApi.context(conversationId);
      if (!visit.current || visit.roomId !== conversationId) return context;
      setConversationContext(context);
      return context;
    } catch (error) {
      if (!visit.current) return null;
      setStatus(`${t('대화 흐름 불러오기 실패 ·')} ${error.message}`);
      return null;
    } finally {
      if (visit.current) setContextLoading(false);
    }
  }

  async function loadBattleState(conversationId = selectedConversationId, room = conversation, syncControl = false) {
    const visit = roomWork.capture();
    if (!visit.current || visit.roomId !== conversationId) return null;
    if (!conversationId || room?.genre_mode !== 'battle') {
      setBattleState(null);
      if (syncControl) setBattleControlDraft({ ...EMPTY_BATTLE_CONTROL_DRAFT });
      return null;
    }
    const state = await conversationApi.battleState(conversationId);
    if (selectedConversationIdRef.current !== conversationId) return state;
    if (!visit.current) return state;
    setBattleState(state);
    if (syncControl) setBattleControlDraft(battleControlDraftFromState(state));
    return state;
  }

  async function clearActiveCommand(commandId = '') {
    if (readyVisitRef.current !== roomWork.capture()) return;
    const { setBusy, setStatus, setConversation } = ownUI('clearActiveCommand');
    if (!selectedConversationId) return;
    setBusy(true);
    try {
      const savedRoom = await conversationApi.clearActiveCommand(selectedConversationId, commandId || undefined) as any;
      setConversation(savedRoom);
      setConversations((current) => current.map((room) => room.id === savedRoom.id ? { ...room, ...savedRoom } : room));
      setStatus(t(commandId ? '활성 커맨드 해제됨' : '활성 커맨드 전체 해제됨'));
    } catch (error) {
      setStatus(`${t('활성 커맨드 해제 실패 ·')} ${error.message}`);
    } finally { setBusy(false); }
  }

  async function compressConversationNow() {
    if (readyVisitRef.current !== roomWork.capture()) return;
    const { setBusy, setStatus, setChatSettingsOpen, setConversationContext } = ownUI('compressConversationNow');
    if (!selectedConversationId || busy) return;
    setBusy(true);
    try {
      setStatus(t('지금 압축 중…'));
      const scene = await conversationApi.compressNow(selectedConversationId);
      setConversationContext((current) => ({ ...(current || {}), scene }));
      await Promise.all([loadConversationContext(selectedConversationId), loadBattleState(selectedConversationId)]);
      setChatSettingsOpen(false);
      setStatus(t('지금 압축 완료 · 메모리/장면 요약 갱신됨'));
    } catch (error) {
      setStatus(`${t('지금 압축 실패 ·')} ${error.message}`);
    } finally { setBusy(false); }
  }

  async function updateSceneSummary(summary, expectedRevision) {
    if (readyVisitRef.current !== roomWork.capture()) return;
    const { setBusy, setStatus, setConversationContext } = ownUI('updateSceneSummary');
    if (!selectedConversationId || busy) return null;
    setBusy(true);
    try {
      const scene = await conversationApi.updateSceneSummary(selectedConversationId, {
        summary,
        expected_revision: expectedRevision,
      }) as any;
      setConversationContext((current) => ({ ...(current || {}), scene }));
      setStatus(t('Story Arc 수정 완료'));
      return scene;
    } catch (error) {
      await loadConversationContext(selectedConversationId);
      setStatus(`${t('Story Arc 수정 실패 ·')} ${error.message}`);
      throw error;
    } finally { setBusy(false); }
  }

  async function createUserMemory(payload) {
    if (readyVisitRef.current !== roomWork.capture()) return;
    const { setBusy, setStatus, setUserMemoryDraft } = ownUI('createUserMemory');
    if (!selectedConversationId || !payload?.content?.trim()) return;
    setBusy(true);
    try {
      await conversationApi.createMemory(selectedConversationId, { character_id: payload.character_id || '__room__', memory_type: payload.memory_type || 'user_note', content: payload.content.trim(), importance: Number(payload.importance || 5) });
      setUserMemoryDraft({ character_id: '__room__', memory_type: 'user_note', content: '', importance: 5 });
      await loadConversationContext(selectedConversationId);
      setStatus(t('유저노트 추가 완료'));
    } catch (error) {
      setStatus(`${t('유저노트 추가 실패 ·')} ${error.message}`);
    } finally { setBusy(false); }
  }

  async function updateConversationMemory(memory, payload) {
    if (readyVisitRef.current !== roomWork.capture()) return;
    const { setBusy, setStatus } = ownUI('updateConversationMemory');
    if (!selectedConversationId || !memory?.id || !payload?.content?.trim()) return;
    setBusy(true);
    try {
      await conversationApi.updateMemory(selectedConversationId, memory.id, { character_id: payload.character_id || '__room__', memory_type: payload.memory_type || memory.memory_type || 'user_note', content: payload.content.trim(), importance: Number(payload.importance || memory.importance || 5) });
      await loadConversationContext(selectedConversationId);
      setStatus(t('유저노트 수정 완료'));
    } catch (error) {
      setStatus(`${t('유저노트 수정 실패 ·')} ${error.message}`);
    } finally { setBusy(false); }
  }

  async function deleteConversationMemory(memory) {
    const actionVisit = routeWork.capture();
    if (readyVisitRef.current !== roomWork.capture()) return;
    const { setBusy, setStatus } = ownUI('deleteConversationMemory');
    if (!selectedConversationId || !memory?.id) return;
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('memory', locale)))) return;
    if (!actionVisit.current) return;
    setBusy(true);
    try {
      await conversationApi.deleteMemory(selectedConversationId, memory.id);
      await loadConversationContext(selectedConversationId);
      setStatus(t('유저노트 삭제 완료'));
    } catch (error) {
      setStatus(`${t('유저노트 삭제 실패 ·')} ${error.message}`);
    } finally { setBusy(false); }
  }

  async function fetchConversation(conversationId) {
    const visit = roomWork.capture();
    if (visit.roomId !== conversationId) return;
    const refreshing = readyVisitRef.current === visit && conversation?.id === conversationId;
    // Retain the visible room, but never admit a new turn while a persisted
    // submission's acceptance is unknown. A successful snapshot restores ready.
    if (unknownSubmissionsForRoom(conversationId).length) readyVisitRef.current = null;
    setRoomLoadError('');
    if (!refreshing) {
      readyVisitRef.current = null;
      setConversation(null); setMessages([]); setParticipants([]);
      setConversationContext(null); setChatSettingsOpen(false); setContextDrawerOpen(false);
      setOlderPagingReady(false);
      clearMessageSelection();
      setShowJumpToLatest(false);
      nextScrollModeRef.current = 'initial';
      lastMessageTailRef.current = { conversationId: '', tailId: '', length: 0 };
    }
    try {
      // Discover before the message snapshot: completion between these reads
      // is either followed as a job or already included in the later snapshot.
      const activeJobs = await visit.wait(conversationApi.activeGenerationJobs(conversationId));
      const [room, roomMessages, roomParticipants, runtimeSetting] = await visit.wait(Promise.all([
        conversationApi.get(conversationId),
        conversationApi.messages(conversationId),
        conversationApi.participants(conversationId),
        conversationApi.runtimeSetting(conversationId),
      ])) as [any, any[], any[], any];
      if (!room || room.id !== conversationId || !Array.isArray(roomMessages) || !Array.isArray(roomParticipants)) throw new Error('Invalid room snapshot');
      visit.assert();
      // A process can die before learning its POST receipt. Persist only the
      // request identity, never another copy of input prose. Do not open a new
      // compose until that uncertain submission is authoritatively reconciled.
      const unknownHints = unknownSubmissionsForRoom(conversationId);
      for (const hint of unknownHints) {
        const retainedKey = generationOwners.pending.get(conversationId)?.payload?.metadata?.client_request_id;
        if (retainedKey === hint.requestId) continue; // Original body can replay safely in this instance.
        let receipt: any;
        try { receipt = await visit.wait(conversationApi.generationReceipt(conversationId, hint.requestId)); }
        catch (error) {
          if (error.status === 410) { visit.assert(); forgetUnknownSubmission(hint); continue; }
          if (error.code === 'RECEIPT_LOOKUP_UNSUPPORTED') throw new Error('This server cannot look up request receipts. Reload after upgrading, or explicitly stop tracking; server work may continue.');
          if (error.status === 404) throw new Error('Submission outcome is still unknown. A missing receipt does not prove the request was never accepted. Reload to reconcile or explicitly stop tracking; server work may continue.');
          throw error;
        }
        visit.assert();
        const receiptRows = receipt.job.status === 'completed'
          ? await loadGeneratedJobMessages(conversationId, receipt.incoming_message, receipt.job, visit)
          : [receipt.incoming_message];
        for (const row of [...receiptRows].reverse()) if (row && !roomMessages.some(message => message.id === row.id)) roomMessages.unshift(row);
        roomMessages.sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')) || String(a.id).localeCompare(String(b.id)));
        // Include terminal jobs too: their source/output may be outside the recent page.
        if (!activeJobs.jobs.some(job => job.id === receipt.job.id)) activeJobs.jobs.push(receipt.job);
        forgetUnknownSubmission(hint);
      }
      // Persisted terminal hints restore Retry after a refresh without storing
      // input prose or replaying POST. Always re-read the known job first.
      await Promise.all(terminalJobsForRoom(conversationId).map(async hint => {
        const source = roomMessages.find(message => message.id === hint.incomingMessageId);
        if (!source) { forgetTerminalJob(conversationId, hint.jobId); return; }
        try {
          const job: any = await visit.wait(conversationApi.generationJob(conversationId, hint.jobId));
          visit.assert();
          if (job.status === 'failed' || job.status === 'cancelled') {
            Object.assign(source, { failed: true, error: job.error_message || 'Generation failed', retryPayload: {}, retryJob: { id: job.id, state_version: job.state_version } });
          } else if (['queued', 'running', 'retrying'].includes(job.status) && !activeJobs.jobs.some(active => active.id === job.id)) activeJobs.jobs.push(job);
          else if (job.status === 'completed' && !activeJobs.jobs.some(active => active.id === job.id)) activeJobs.jobs.push(job);
        } catch (error) {
          if (error.status === 404 || error.status === 410) forgetTerminalJob(conversationId, hint.jobId);
          else if (visit.current) Object.assign(source, { failed: true, error: 'Previous generation status could not be verified. Reload to reconcile.' });
        }
      }));
      visit.assert();
      const discovered = activeJobs.jobs.map(job => ({ conversationId, jobId: job.id, incomingMessage: roomMessages.find(message => message.id === job.incoming_message_id), localMessageId: '', payload: {} }));
      const retained = generationOwners.pending.get(conversationId);
      if (retained && !retained.jobId) {
        const key = retained.payload?.metadata?.client_request_id;
        const matching = key && discovered.find(job => job.incomingMessage?.metadata?.client_request_id === key);
        if (matching) Object.assign(retained, matching);
      }
      if (!retained && discovered.length) generationOwners.pending.set(conversationId, { ...discovered[0], nextJobs: discovered.slice(1) });
      else if (retained) retained.nextJobs = discovered.filter(job => job.jobId !== retained.jobId);
      if (selectedConversationIdRef.current !== conversationId) return;
      if (!visit.current) return;
      if (room.genre_mode === 'battle') await loadBattleState(conversationId, room, !refreshing);
      else {
        setBattleState(null);
        setBattleControlDraft({ ...EMPTY_BATTLE_CONTROL_DRAFT });
      }
      if (!visit.current) return;
      setConversation(current => refreshing
        ? { ...room, tts_enabled: current?.tts_enabled }
        : room);
      // A refresh must not drop loaded history, local turns or retry metadata.
      if (refreshing) nextScrollModeRef.current = 'preserve';
      setMessages(current => {
        if (!visit.current) return current;
        const snapshot = new Map(roomMessages.map(message => [message.id, message]));
        const currentIds = new Set(current.map(message => message.id));
        const merged = refreshing
          ? [...current.map(message => ({ ...message, ...snapshot.get(message.id) })), ...roomMessages.filter(message => !currentIds.has(message.id))]
          : roomMessages;
        return merged.filter(message => !deletedMessageIdsRef.current.get(conversationId)?.has(message.id));
      });
      if (!refreshing) setHasOlderMessages(roomMessages.length > 0);
      setParticipants(roomParticipants);
      if (!refreshing) {
        setConversationRuntimeSetting(normalizeRuntimeSetting(runtimeSetting || {}));
        setConversationContext(null);
      }
      readyVisitRef.current = visit;
      void resumeGeneration();
      if (!refreshing) setSpeakerId(USER_ID);
      await markConversationRead(conversationId);
      if (!visit.current) return false;
      if ('supported' in activeJobs && activeJobs.supported === false) setStatus('대화 표시 중 · 서버가 활성 job 검색을 지원하지 않습니다. 새로고침 후 진행 상태를 다시 확인하세요.');
      return true;
    } catch (error) {
      if (visit.current) { setRoomLoadError(error.message); setStatus(error.message); }
      return false;
    }
  }

  async function retryConversationRefresh() {
    const visit = roomWork.capture();
    const previousStatus = status;
    const retryOwnsStatus = previousStatus === roomLoadError || (!!roomLoadError && previousStatus.includes(roomLoadError)) || previousStatus.includes(t('캐릭터 초대 완료'));
    if (!(await fetchConversation(visit.roomId)) || !visit.current) return;
    if (contextDrawerOpen && !(await loadConversationContext(visit.roomId))) {
      if (visit.current) setRoomLoadError(t('대화 흐름 불러오기 실패 ·'));
      return;
    }
    if (visit.current && retryOwnsStatus) setStatus(current => current === previousStatus ? '' : current);
  }

  async function stopTrackingUnknownSubmission() {
    const visit = roomWork.capture();
    const hints = unknownSubmissionsForRoom(visit.roomId);
    if (!hints.length || generationOwners.pending.has(visit.roomId)) return;
    if (!(await confirmDanger('Stop tracking this unresolved submission? This does not cancel server work. It may still complete, and sending another input may create an additional turn.', { title: 'Stop tracking?', confirmText: 'Stop tracking', cancelText: t('취소') }))) return;
    if (!visit.current) return;
    for (const hint of hints) forgetUnknownSubmission(hint);
    await fetchConversation(visit.roomId);
  }

  async function loadOlderMessages() {
    if (!selectedConversationId || loadingOlderMessages || !hasOlderMessages || messages.length === 0) return;
    const visit = roomWork.capture();
    const oldestPersisted = messages.find((message) => message.id && !String(message.id).startsWith('local_') && !message.is_typing);
    if (!oldestPersisted) return;
    const scroller = chatThreadRef.current;
    const previousScrollHeight = scroller?.scrollHeight || 0;
    const previousScrollTop = scroller?.scrollTop || 0;
    setLoadingOlderMessages(true);
    try {
      const older = await visit.wait(conversationApi.messages(selectedConversationId, oldestPersisted.id)) as any[];
      visit.assert();
      if (!older.length) {
        setHasOlderMessages(false);
        return;
      }
      const existingIds = new Set(messages.map((message) => message.id));
      const uniqueOlder = older.filter((message) => !existingIds.has(message.id));
      if (!uniqueOlder.length) {
        setHasOlderMessages(false);
        return;
      }
      skipNextAutoScrollRef.current = true;
      nextScrollModeRef.current = 'prepend';
      setMessages((current) => {
        if (!visit.current) return current;
        const currentIds = new Set(current.map((message) => message.id));
        return [...uniqueOlder.filter((message) => !currentIds.has(message.id) && !deletedMessageIdsRef.current.get(visit.roomId)?.has(message.id)), ...current];
      });
      requestAnimationFrame(() => {
        if (!visit.current) return;
        const node = chatThreadRef.current;
        if (!node) return;
        // The measured window owns ID-based anchoring; retain the fallback for
        // non-windowed renderers without applying the prepend delta twice.
        if (node.querySelector('[data-chat-window]')) return;
        node.scrollTop = node.scrollHeight - previousScrollHeight + previousScrollTop;
      });
    } catch (error) {
      if (!visit.current) return;
      setStatus(`${t('이전 대화 불러오기 실패 ·')} ${error.message}`);
    } finally {
      if (visit.current) setLoadingOlderMessages(false);
    }
  }

  async function renameConversation(title) {
    if (readyVisitRef.current !== roomWork.capture()) return;
    const { setBusy, setStatus, setConversation } = ownUI('renameConversation');
    if (!selectedConversationId) return;
    setBusy(true);
    try {
      const updated = await conversationMutations.updateConversation.mutateAsync({ id: selectedConversationId, payload: { title } }) as any;
      setConversation(updated);
      setStatus(t('대화방 제목 변경 완료'));
    } catch (error) { setStatus(`요청 실패 · ${error.message}`); } finally { setBusy(false); }
  }

  async function inviteCharacterToConversation(characterId) {
    if (readyVisitRef.current !== roomWork.capture()) return;
    const visit = roomWork.capture();
    const conversationId = selectedConversationId;
    if (!conversationId || !characterId || invitePendingRef.current.has(conversationId)) return;
    const { setBusy, setStatus } = ownUI('inviteCharacterToConversation');
    invitePendingRef.current.add(conversationId);
    setBusy(true);
    const refreshFailure = locale === 'ko' ? '새로고침 실패' : locale === 'ja' ? '再読み込みに失敗しました' : 'Refresh failed';
    let invited = false;
    try {
      await conversationApi.inviteParticipant(conversationId, characterId);
      invited = true;
      if (!visit.current) return;
      const refreshed = await fetchConversation(conversationId);
      if (!visit.current) return;
      if (!refreshed) {
        setStatus(`${t('캐릭터 초대 완료')} · ${refreshFailure} · ${t('재시도')}`);
        return;
      }
      const context = await loadConversationContext(conversationId);
      if (!visit.current) return;
      if (!context) setRoomLoadError(t('대화 흐름 불러오기 실패 ·'));
      setStatus(context ? t('캐릭터 초대 완료') : `${t('캐릭터 초대 완료')} · ${t('대화 흐름 불러오기 실패 ·')} ${t('재시도')}`);
    } catch (error) {
      setStatus(invited ? `${t('캐릭터 초대 완료')} · ${refreshFailure} · ${error.message}` : `요청 실패 · ${error.message}`);
    } finally {
      invitePendingRef.current.delete(conversationId);
      setBusy(false);
    }
  }

  async function removeCharacterFromConversation(characterId) {
    const actionVisit = routeWork.capture();
    if (readyVisitRef.current !== roomWork.capture()) return;
    const { setBusy, setStatus } = ownUI('removeCharacterFromConversation');
    if (!selectedConversationId || !characterId) return;
    const name = participantName(characterId);
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('removeCharacter', locale, name)))) return;
    if (!actionVisit.current) return;
    setBusy(true);
    try {
      await conversationApi.removeCharacterParticipant(selectedConversationId, characterId);
      await fetchConversation(selectedConversationId);
      await loadConversationContext(selectedConversationId);
      setStatus(t('캐릭터 내보내기 완료'));
    } catch (error) { setStatus(`요청 실패 · ${error.message}`); } finally { setBusy(false); }
  }

  async function updateCharacterRoleInConversation(characterId, role) {
    if (readyVisitRef.current !== roomWork.capture()) return;
    const { setBusy, setStatus } = ownUI('updateCharacterRoleInConversation');
    if (!selectedConversationId || !characterId) return;
    setBusy(true);
    try {
      await conversationApi.updateParticipantRole(selectedConversationId, characterId, role || null);
      await fetchConversation(selectedConversationId);
      await loadConversationContext(selectedConversationId);
      setStatus(t('캐릭터 역할 수정 완료'));
    } catch (error) { setStatus(`요청 실패 · ${error.message}`); } finally { setBusy(false); }
  }

  async function generateMessageTts(message) {
    const operationVisit = routeWork.capture();
    if (readyVisitRef.current !== roomWork.capture()) return;
    const { setBusy, setStatus, setMessages } = ownUI('generateMessageTts');
    if (!message?.id || message.id.startsWith('local_') || message.is_typing || !messages.some(item => item.id === message.id)) return;
    setBusy(true);
    try {
      const result = await conversationMutations.generateMessageTts.mutateAsync(message.id) as any;
      setMessages((current) => current.map((item) => item.id === message.id ? { ...item, tts_audio_url: result.audio_url, tts_text: result.tts_text } : item));
      if (!operationVisit.current) return;
      const audio = new Audio(assetUrlFor(result.audio_url));
      await audio.play().catch(() => {});
      setStatus(t('TTS 생성 완료'));
    } catch (error) {
      setStatus(`${t('TTS 생성 실패 ·')} ${error.message}`);
    } finally { setBusy(false); }
  }

  async function revealServerMessages(serverMessages, localMessageId, visit = roomWork.capture()) {
    visit.assert();
    async function delay(ms) { await visit.sleep(ms); }
    const deleted = () => deletedMessageIdsRef.current.get(visit.roomId);
    const incoming = deleted()?.has(serverMessages[0]?.id) ? null : serverMessages[0];
    const generated = serverMessages.slice(1);
    nextScrollModeRef.current = 'append';
    setMessages((current) => {
      if (!visit.current) return current;
      const withoutTransient = current.filter((item) => item.id !== localMessageId && !item.is_typing);
      const existingIds = new Set(withoutTransient.map((item) => item.id));
      return incoming && !existingIds.has(incoming.id) ? [...withoutTransient, incoming] : withoutTransient;
    });
    for (const message of generated) {
      visit.assert();
      if (!message || deleted()?.has(message.id)) continue;
      const typingId = `typing_${message.id}`;
      nextScrollModeRef.current = 'append';
      setMessages((current) => {
        if (!visit.current) return current;
        if (current.some((item) => item.id === message.id || item.id === typingId)) return current;
        return [...current.filter((item) => !item.is_typing), { ...message, id: typingId, content: '', action: '', thought: '', is_typing: true }];
      });
      await delay(BUBBLE_REVEAL_DELAY_MS);
      visit.assert();
      if (deleted()?.has(message.id)) { setMessages(current => current.filter(item => item.id !== typingId)); continue; }
      nextScrollModeRef.current = 'append';
      setMessages((current) => !visit.current ? current : current.some((item) => item.id === message.id)
        ? current.filter((item) => item.id !== typingId)
        : current.map((item) => item.id === typingId ? message : item));
    }
  }

  async function pollGenerationJob(conversationId, jobId) {
    const visit = roomWork.capture();
    if (visit.roomId !== conversationId) throw new RoomWorkCancelledError();
    const deadline = Date.now() + JOB_POLL_INTERVAL_MS * JOB_POLL_MAX_ATTEMPTS;
    for (let attempt = 0; attempt < JOB_POLL_MAX_ATTEMPTS; attempt += 1) {
      visit.assert();
      if (Date.now() >= deadline) break;
      let job: any;
      try {
        job = await visit.wait(conversationApi.generationJob(conversationId, jobId), Math.min(10000, deadline - Date.now()));
      } catch (error) {
        visit.assert();
        if (error.status === 404 || error.status === 410) throw error;
        if (attempt === JOB_POLL_MAX_ATTEMPTS - 1) throw new Error(JOB_PENDING_TIMEOUT_MESSAGE);
        await visit.sleep(JOB_POLL_INTERVAL_MS);
        continue;
      }
      visit.assert();
      if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') return job;
      setStatus(`${t('대화 생성 중…')} ${t(job.status === 'running' ? '응답 작성 중' : '대기 중')}`);
      await visit.sleep(JOB_POLL_INTERVAL_MS);
    }
    throw new Error(JOB_PENDING_TIMEOUT_MESSAGE);
  }

  async function waitForGenerationJob(conversationId, jobId) {
    const visit = roomWork.capture();
    visit.assert();
    if (visit.roomId !== conversationId) throw new RoomWorkCancelledError();
    const statusLabels = { queued: '대기 중', running: '응답 작성 중', retrying: '응답 재시도 중' } as const;
    let subscription: ReturnType<typeof subscribeGenerationJobLifecycle> | null = null;
    try {
      subscription = subscribeGenerationJobLifecycle({
        conversationId,
        jobId,
        onStatus: (event) => {
          if (!visit.current || event.status === 'completed' || event.status === 'failed' || event.status === 'cancelled') return;
          const label = event.status in statusLabels
            ? t(statusLabels[event.status as keyof typeof statusLabels])
            : t('응답 작성 중');
          setStatus(`${t('대화 생성 중…')} ${label}`);
        },
      });
      activeGenerationSubscriptionRef.current = subscription;
      await visit.wait(subscription.promise);
      visit.assert();
      return await visit.wait(conversationApi.generationJob(conversationId, jobId)) as any;
    } catch (error) {
      visit.assert();
      if (error instanceof GenerationLifecycleSubscriptionClosedError) throw error;
      return await pollGenerationJob(conversationId, jobId);
    } finally {
      subscription?.close();
      if (subscription && activeGenerationSubscriptionRef.current === subscription) activeGenerationSubscriptionRef.current = null;
    }
  }

  async function loadGeneratedJobMessages(conversationId, incomingMessage, job, visit = roomWork.capture()) {
    const generatedIds: string[] = job.generated_message_ids || [];
    const found = new Map<string, any>();
    const cursors = new Set<string>();
    let cursor: string | undefined;
    // Fetch complete committed output or retain an explicit reconciliation error.
    for (let page = 0; page < 100; page++) {
      const rows = await visit.wait(conversationApi.messages(conversationId, cursor)) as any[];
      visit.assert();
      if (!Array.isArray(rows)) throw new Error('Invalid message page');
      rows.forEach(message => found.set(message.id, message));
      if (generatedIds.every(id => found.has(id))) break;
      const next = rows[0]?.id;
      if (!next || cursors.has(next)) break;
      cursors.add(next); cursor = next;
    }
    const missing = generatedIds.filter(id => !found.has(id));
    if (missing.length) throw new Error(`Generated output missing ${missing.length} message(s). Reload or reconcile; deleted output is not recreated.`);
    // A deleted source must not be resurrected from a stale POST receipt.
    const incoming = found.get(incomingMessage?.id);
    return [incoming, ...generatedIds.map(id => found.get(id))];
  }

  async function resumeGeneration() {
    const visit = roomWork.capture();
    const activeJob = generationOwners.pending.get(visit.roomId);
    if (!visit.current || readyVisitRef.current !== visit || !activeJob || (activeJob.submitting && !activeJob.jobId)) return;
    return generationOwners.run(activeJob, visit, async () => {
      try {
        // Unknown POST outcome replays only the original idempotency key.
        if (!activeJob.jobId) {
          const created: any = await visit.wait(conversationApi.createMessageJob(activeJob.conversationId, activeJob.payload));
          if (!created?.job?.id || !created?.incoming_message?.id) throw new Error('Invalid generation job receipt');
          Object.assign(activeJob, { jobId: created.job.id, incomingMessage: created.incoming_message });
          forgetUnknownSubmission({ conversationId: activeJob.conversationId, requestId: activeJob.payload?.metadata?.client_request_id });
        }
        // Entry/visibility reconciliation reads durable state before subscribing.
        let job: any;
        try {
          job = await visit.wait(conversationApi.generationJob(activeJob.conversationId, activeJob.jobId));
        } catch (error) {
          visit.assert();
          if (error.status === 404 || error.status === 410) throw error;
          job = await pollGenerationJob(activeJob.conversationId, activeJob.jobId);
        }
        visit.assert();
        if (!['completed', 'failed', 'cancelled'].includes(job.status)) {
          job = await waitForGenerationJob(activeJob.conversationId, activeJob.jobId);
        }
        visit.assert();
        if (job.status === 'failed' || job.status === 'cancelled') {
          generationOwners.finish(activeJob);
          if (activeJob.nextJobs?.length) { const [next, ...rest] = activeJob.nextJobs; generationOwners.pending.set(activeJob.conversationId, { ...next, nextJobs: rest }); }
          rememberTerminalJob({ conversationId: activeJob.conversationId, jobId: activeJob.jobId, incomingMessageId: activeJob.incomingMessage?.id, stateVersion: job.state_version });
          const errorMessage = job.error_message || t(job.status === 'cancelled' ? '응답 생성 job 취소됨' : '응답 생성 job 실패');
          setMessages((current) => !visit.current ? current : current.map((item) => item.id === activeJob.localMessageId || item.id === activeJob.incomingMessage?.id ? { ...item, failed: true, error: errorMessage, retryPayload: activeJob.payload, retryJob: { id: activeJob.jobId, state_version: job.state_version } } : item).filter((item) => !item.is_typing));
          setStatus(`${t('전송 실패 ·')} ${errorMessage}`);
          if (generationOwners.pending.has(activeJob.conversationId)) await resumeGeneration();
          return;
        }
        if (job.status !== 'completed') throw new Error(JOB_PENDING_TIMEOUT_MESSAGE);
        const serverMessages = await loadGeneratedJobMessages(activeJob.conversationId, activeJob.incomingMessage, job, visit);
        await revealServerMessages(serverMessages, activeJob.localMessageId, visit);
        visit.assert();
        generationOwners.finish(activeJob);
        forgetTerminalJob(activeJob.conversationId, activeJob.jobId);
        if (activeJob.nextJobs?.length) { const [next, ...rest] = activeJob.nextJobs; generationOwners.pending.set(activeJob.conversationId, { ...next, nextJobs: rest }); }
        setStatus(current => isChatGenerationProgress(current, t) ? '' : current);
        await markConversationRead(activeJob.conversationId);
        if (!visit.current) return;
        if (activeJob.payload?.battle_control) await loadBattleState(activeJob.conversationId, { genre_mode: 'battle' }, true);
        if (!visit.current) return;
        await loadConversations();
        if (visit.current && generationOwners.pending.has(activeJob.conversationId)) await resumeGeneration();
      } catch (error) {
        if (!visit.current || error instanceof RoomWorkCancelledError || error instanceof GenerationLifecycleSubscriptionClosedError) return;
        if (error.status === 404 || error.status === 410) {
          generationOwners.finish(activeJob);
          setStatus('Generation job no longer exists. Reload room to reconcile.');
          readyVisitRef.current = null;
          setRoomLoadError('Generation job no longer exists. Reload room to reconcile.');
          setConversation(null);
          return;
        }
        // Keep ownership on network failures. Re-entry, visibility and send
        // attempts retry reconciliation rather than creating duplicate jobs.
        setStatus(`${t('대화 생성 중…')} ${error.message || t(JOB_PENDING_TIMEOUT_MESSAGE)}`);
      }
    }).catch((error) => { if (visit.current && !(error instanceof RoomWorkCancelledError)) setStatus(error.message); });
  }

  async function submitConversationPayload(payload, localMessageId = `local_${Date.now()}`) {
    const visit = roomWork.capture();
    const conversationId = visit.roomId;
    if (!conversationId || !visit.current || readyVisitRef.current !== visit) return;
    if (generationOwners.pending.has(conversationId)) { await resumeGeneration(); return; }
    // Reserve synchronously, including the interval before POST returns.
    const activeJob: any = { conversationId, localMessageId, payload, jobId: null, submitting: true };
    generationOwners.pending.set(conversationId, activeJob);
    const clientRequestId = payload?.metadata?.client_request_id || newClientRequestId();
    const payloadWithRequestId = {
      ...payload,
      metadata: { ...(payload?.metadata || {}), client_request_id: clientRequestId },
    };
    activeJob.payload = payloadWithRequestId;
    rememberUnknownSubmission({ conversationId, requestId: clientRequestId });
    const optimisticMessage = { id: localMessageId, conversation_id: conversationId, ...payloadWithRequestId, retryPayload: payloadWithRequestId };
    nextScrollModeRef.current = 'append';
    setMessages((current) => {
      if (!visit.current) return current;
      if (current.some((item) => item.id === localMessageId)) {
        return current.map((item) => item.id === localMessageId ? { ...item, failed: false, error: '', retryPayload: payloadWithRequestId } : item).filter((item) => !item.is_typing);
      }
      return [...current, optimisticMessage];
    });
    setStatus(t('대화 생성 job 생성 중…'));
    try {
      // Do not cancel the server-owned POST when the UI detaches.
      const created = await conversationMutations.createMessageJob.mutateAsync({ conversationId, payload: payloadWithRequestId }) as any;
      const jobId = created?.job?.id;
      const incomingMessage = created?.incoming_message;
      if (!jobId || !incomingMessage) throw new Error(t('응답 생성 job 정보를 받지 못했습니다.'));
      Object.assign(activeJob, { jobId, incomingMessage, submitting: false });
      forgetUnknownSubmission({ conversationId, requestId: clientRequestId });
      if (visit.current) {
        if (payload.battle_control) await loadBattleState(selectedConversationId, conversation, true).catch(() => null);
      }
      // A POST can finish after A→B→A; use the current visit's single runner.
      if (roomWork.capture().roomId === conversationId) await resumeGeneration();
    } catch (error) {
      activeJob.submitting = false;
      // A lost response is not proof the POST failed. Retain the request key.
      if (error.status && error.status < 500 && error.code !== 'PROTOCOL_ERROR') { generationOwners.finish(activeJob); forgetUnknownSubmission({ conversationId, requestId: clientRequestId }); }
      if (!visit.current) return;
      setStatus(`${t('전송 실패 ·')} ${error.message}`);
      setMessages((current) => !visit.current ? current : current.map((item) => item.id === localMessageId ? { ...item, failed: true, error: error.message, retryPayload: payloadWithRequestId } : item).filter((item) => !item.is_typing));
    }
  }

  function scrollToLatest() {
    const node = chatThreadRef.current;
    if (node) node.scrollTo({ top: node.scrollHeight, behavior: 'smooth' });
    else chatEndRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
    setShowJumpToLatest(false);
  }

  async function retryMessage(message) {
    if (!message?.retryPayload || !selectedConversationId || busy || (message.conversation_id && message.conversation_id !== selectedConversationId)) return;
    const visit = roomWork.capture();
    if (readyVisitRef.current !== visit) return;
    if (generationOwners.pending.has(selectedConversationId)) { await resumeGeneration(); return; }
    if (!message.retryJob) { await submitConversationPayload(message.retryPayload, message.id); return; }
    // Explicit terminal retry is CAS; it never creates another source/input.
    const activeJob: any = { conversationId: selectedConversationId, jobId: message.retryJob.id, localMessageId: message.id, incomingMessage: message, payload: message.retryPayload };
    generationOwners.pending.set(selectedConversationId, activeJob);
    try {
      await conversationApi.retryGenerationJob(selectedConversationId, message.retryJob.id, message.retryJob.state_version);
      if (visit.current) await resumeGeneration();
    } catch (error) {
      // A retry response can be lost after CAS; reconcile this known job only.
      if (visit.current) { setStatus(`재시도 확인 필요 · ${error.message}`); await resumeGeneration(); }
    }
  }

  async function sendConversationMessage() {
    if (!compose.trim() || !selectedConversationId) return;
    const visit = roomWork.capture();
    if (readyVisitRef.current !== visit) return;
    const activeJob = generationOwners.pending.get(selectedConversationId);
    if (activeJob?.conversationId === selectedConversationId) {
      setStatus(t('이미 이 대화방에서 응답 생성 job이 진행 중입니다. 완료 후 다시 전송 가능합니다.'));
      void resumeGeneration();
      return;
    }
    const selected = speakerId || USER_ID;
    const parsed = parseInputMarkup(compose.trim());
    const content = parsed.hadMarkup ? parsed.dialogue : compose.trim();
    const action = parsed.hadMarkup ? parsed.action : '';
    if (!content.trim() && !action.trim()) return;
    const battleControlPayload = buildBattleControlPayload();
    const metadata = parsed.hadMarkup && parsed.parts.length ? { render_parts: parsed.parts } : undefined;
    const payload = { speaker_type: selected === SYSTEM_ID ? 'system' : selected === USER_ID ? 'user' : 'character', speaker_id: selected, content, ...(action ? { action } : {}), ...(metadata ? { metadata } : {}), ...(battleControlPayload ? { battle_control: battleControlPayload } : {}) };
    setCompose('');
    setComposeExpanded(false);
    await submitConversationPayload(payload);
    if (visit.current && battleControlPayload?.action === 'end') {
      setBattleControlDraft({ action: 'none', advantage: 0.5, progress_balance: 0.5, current_phase: 'opening' });
    }
  }

  function buildBattleControlPayload() {
    if (conversation?.genre_mode !== 'battle' || !battleControlDraft || battleControlDraft.action === 'none') return null;
    const action = battleControlDraft.action;
    if (action === 'start') {
      if (!battleControlDraft.participant_a_id || !battleControlDraft.participant_b_id) return null;
      return {
        action: 'start',
        participant_a_id: battleControlDraft.participant_a_id,
        participant_b_id: battleControlDraft.participant_b_id,
        advantage: Number(battleControlDraft.advantage ?? 0.5),
        current_phase: battleControlDraft.current_phase || 'opening',
      };
    }
    if (!battleState?.active_match?.id) return null;
    if (action === 'progress') {
      const balance = Number(battleControlDraft.progress_balance ?? 0.5);
      const active = battleState.active_match;
      const favoredCharacterId = balance < 0.45 ? active.participant_a_id : balance > 0.55 ? active.participant_b_id : undefined;
      const advantage = favoredCharacterId ? Math.max(balance, 1 - balance) : 0.5;
      return {
        action: 'progress',
        match_id: active.id,
        favored_character_id: favoredCharacterId,
        advantage,
        current_phase: battleControlDraft.current_phase || 'middle',
      };
    }
    if (action === 'end') {
      if (!battleControlDraft.winner_id) return null;
      return {
        action: 'end',
        match_id: battleState.active_match.id,
        winner_id: battleControlDraft.winner_id,
        decisive_moment: battleControlDraft.decisive_moment || undefined,
      };
    }
    return null;
  }
  return {
    conversationContext, battleState, battleControlDraft, contextLoading, conversation, participants,
    messages, hasOlderMessages, loadingOlderMessages, olderPagingReady, showJumpToLatest, speakerId,
    compose, composeExpanded, conversationRuntimeSetting, chatSettingsOpen, userMemoryDraft, roomLoadError,
    setBattleControlDraft, setConversation, setParticipants, setMessages, setHasOlderMessages, setOlderPagingReady,
    setShowJumpToLatest, setSpeakerId, setCompose, setComposeExpanded, setConversationRuntimeSetting, setChatSettingsOpen,
    setUserMemoryDraft, chatEndRef, chatThreadRef, composerTextareaRef, generationOwners, roomWork,
    saveConversationRuntimeSetting, loadConversationContext, loadBattleState, clearActiveCommand, compressConversationNow, updateSceneSummary,
    createUserMemory, updateConversationMemory, deleteConversationMemory, fetchConversation, retryConversationRefresh, stopTrackingUnknownSubmission,
    loadOlderMessages, renameConversation, inviteCharacterToConversation, removeCharacterFromConversation, updateCharacterRoleInConversation, generateMessageTts,
    scrollToLatest, retryMessage, sendConversationMessage, resizeComposerTextarea, handleComposerKeyDown, messageSelectionMode,
    selectedMessageIds, enterMessageSelectionMode, toggleMessageSelection, clearMessageSelection, deleteSelectedMessages, updateMessageBubble,
    deleteMessageBubble, regenerateFromMessage
  };
}
