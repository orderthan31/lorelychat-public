import React, { useEffect, useMemo, useRef, useState } from 'react';
import Swal from 'sweetalert2';
import withReactContent from 'sweetalert2-react-content';
import 'sweetalert2/dist/sweetalert2.min.css';
import { characterApi, conversationApi, settingsApi } from '../api/resources';
import { GenerationLifecycleSubscriptionClosedError, subscribeGenerationJobLifecycle } from '../api/generationJobEvents';
import { useAppDerivedData } from '../hooks/useAppDerivedData';
import { useBootstrapResources } from '../hooks/useBootstrapResources';
import { useBootstrapSync } from '../hooks/useBootstrapSync';
import { useCharacterMutations } from '../hooks/useCharacterMutations';
import { useConversationAutoScroll } from '../hooks/useConversationAutoScroll';
import { useLegacyNavigation } from '../hooks/useLegacyNavigation';
import { usePreviewModals } from '../hooks/usePreviewModals';
import { useRouteResourceSync } from '../hooks/useRouteResourceSync';
import { useRuntimeDefaultSetting } from '../hooks/useRuntimeDefaultSetting';
import { useConversationMutations } from '../hooks/useConversationMutations';
import { useSettingsMutations } from '../hooks/useSettingsMutations';
import { useUiStore } from '../stores/uiStore';
import { useI18n } from '../i18n/I18nProvider';
import { formatDangerConfirmation, formatUiCount } from '../i18n/core';
import { Button } from '../components/atoms';
import { Field, GenreModeField, ResourceListBoundary, TextareaWithExpand } from '../components/molecules';
import { CharacterCardPreviewModal } from '../components/organisms';
import { AvatarPreviewModal } from '../components/organisms';
import { AppShell } from '../components/organisms';


import { ConversationInfoDrawer } from '../components/organisms';
import { HomeView } from '../views/Home';
import { ConversationCreateView } from '../views/ConversationCreate';
import { CharactersListView } from '../views/Characters';
import { ConversationsListView } from '../views/Conversations';
import { SettingsView } from '../views/Settings';
import { ModelSettingsView } from '../views/ModelSettings';
import { StatisticsView } from '../views/Statistics';
import { ConversationDetailView } from '../views/ConversationDetail';
import { PresetsView } from '../views/Presets';
import { PresetDetailView } from '../views/PresetDetail';
import { ChatCommandsView } from '../views/ChatCommands';
import { WorldSettingsDetailView, WorldSettingsListView, normalizeWorld } from '../views/WorldSettings';
import { ConversationEditView } from '../views/ConversationEdit';
import { CharacterDetailView } from '../views/CharacterDetail';
import { CharacterAssetsView } from '../views/CharacterAssets';
import { useConversationComposer } from '../views/ConversationDetail/hooks/useConversationComposer';
import { assetUrlFor, avatarUrlFor } from '../utils/assets';
import { normalizeCharacterForApi } from '../utils/character';
import { genreModeLabel, normalizeGenreMode } from '../utils/genre';
import { pageCount } from '../utils/pagination';
import { arrayToLines, compactText, linesToArray, parseBabeChatInputMarkup, parseTagInput, safeJsonParse, tagText } from '../utils/text';
import {
  API_BASE,
  BUBBLE_REVEAL_DELAY_MS,
  CAST_ROLE_PRESETS,
  DEFAULT_FORBIDDEN_RULES,
  DEFAULT_GENRE_MODE,
  DEFAULT_RUNTIME_SETTING,
  DEFAULT_TRAIT_SCORES,
  EMPTY_CHARACTER,
  EMPTY_PRESET,
  FALLBACK_TTS_MODELS,
  FALLBACK_TTS_VOICES,
  MESSAGE_PAGE_TURNS,
  SYSTEM_ID,
  USER_ID,
} from '../constants/domain';

async function delay(ms) { return new Promise((resolve) => window.setTimeout(resolve, ms)); }
const JOB_POLL_INTERVAL_MS = 1200;
const JOB_POLL_MAX_ATTEMPTS = 120;
const JOB_PENDING_TIMEOUT_MESSAGE = '응답 생성 지연 중입니다. 완료 후 현재 방 재진입 때 동기화됩니다.';
function newClientRequestId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return `req_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}
const Alert = withReactContent(Swal);
type ConfirmDangerOptions = { title: string; confirmText: string; cancelText: string };

async function confirmDanger(message: string, options: ConfirmDangerOptions) {
  const result = await Alert.fire({
    title: options.title,
    text: message,
    icon: 'warning',
    showCancelButton: true,
    confirmButtonText: options.confirmText,
    cancelButtonText: options.cancelText,
    reverseButtons: true,
    focusCancel: true,
    buttonsStyling: false,
    customClass: {
      popup: 'danger-confirm-popup',
      title: 'danger-confirm-title',
      htmlContainer: 'danger-confirm-body',
      actions: 'danger-confirm-actions',
      confirmButton: 'danger-confirm-button',
      cancelButton: 'danger-cancel-button',
    },
  });
  return result.isConfirmed;
}
export function App() {
  const { locale, t } = useI18n();
  const confirmLocalizedDanger = (message: string) => confirmDanger(message, { title: t('삭제 확인'), confirmText: t('삭제'), cancelText: t('취소') });
  const bootstrap = useBootstrapResources();
  const characterMutations = useCharacterMutations();
  const conversationMutations = useConversationMutations();
  const settingsMutations = useSettingsMutations();
  const { route, navigate } = useLegacyNavigation();
  const { avatarPreview, setAvatarPreview, cardPreview, setCardPreview } = usePreviewModals();
  const [backendOnline, setBackendOnline] = useState(false);
  const drawerOpen = useUiStore((state) => state.drawerOpen);
  const setDrawerOpen = useUiStore((state) => state.setDrawerOpen);
  const contextDrawerOpen = useUiStore((state) => state.contextDrawerOpen);
  const setContextDrawerOpen = useUiStore((state) => state.setContextDrawerOpen);
  const [conversationContext, setConversationContext] = useState<any>(null);
  const [battleState, setBattleState] = useState<any>(null);
  const [battleControlDraft, setBattleControlDraft] = useState<any>({ action: 'none', advantage: 0.5, progress_balance: 0.5, current_phase: 'opening' });
  const [contextLoading, setContextLoading] = useState(false);
  const [characters, setCharacters] = useState<any[]>([]);
  const [characterUsageRanking, setCharacterUsageRanking] = useState<any[]>([]);
  const [presets, setPresets] = useState<any[]>([]);
  const [chatCommands, setChatCommands] = useState<any[]>([]);
  const [worldSettings, setWorldSettings] = useState<any[]>([]);
  const [worldSettingDraft, setWorldSettingDraft] = useState<any>({ title: '', thumbnail_url: '', description: '', genre_mode: DEFAULT_GENRE_MODE, location: '', mood: '', world_seed: '', opening_scene: '', opening_line: '', tone_preset: '', relationship_archetype: '', compression_focus: '', tags: [], enabled: true });
  const [selectedWorldSettingId, setSelectedWorldSettingId] = useState('');
  const [ttsVoices, setTtsVoices] = useState<any[]>(FALLBACK_TTS_VOICES);
  const [ttsModels, setTtsModels] = useState<any[]>(FALLBACK_TTS_MODELS);
  const [presetDraft, setPresetDraft] = useState(EMPTY_PRESET);
  const [chatCommandDraft, setChatCommandDraft] = useState<any>({ name: '', display_name: '', description: '', prompt: '', generation_prompt: '', postprocess_prompt: '', postprocess_target: 'last_bubble', postprocess_probability: 100, postprocess_context_options: ['scene', 'world', 'turn_messages'], enabled: true, priority: 0 });
  const [presetQuery, setPresetQuery] = useState('');
  const [presetPage, setPresetPage] = useState(1);
  const [conversations, setConversations] = useState<any[]>([]);
  const [conversation, setConversation] = useState<any>(null);
  const [participants, setParticipants] = useState<any[]>([]);
  const [messages, setMessages] = useState<any[]>([]);
  const [hasOlderMessages, setHasOlderMessages] = useState(false);
  const [loadingOlderMessages, setLoadingOlderMessages] = useState(false);
  const [olderPagingReady, setOlderPagingReady] = useState(false);
  const [messageSelectionMode, setMessageSelectionMode] = useState(false);
  const [selectedMessageIds, setSelectedMessageIds] = useState<string[]>([]);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const [draft, setDraft] = useState<any>(EMPTY_CHARACTER);
  const [characterAssets, setCharacterAssets] = useState<any[]>([]);
  const [characterAssetsLoading, setCharacterAssetsLoading] = useState(false);
  const [assetDraft, setAssetDraft] = useState({ label: '', description: '', image_url: '', tags: '', mood_tags: '', scene_tags: '', outfit_tags: '', pose_tags: '', expression_tags: '', priority: 50, enabled: true, is_default: false });
  const [genreMode, setGenreMode] = useState(DEFAULT_GENRE_MODE);
  const [conversationTitle, setConversationTitle] = useState('');
  const [conversationThumbnailUrl, setConversationThumbnailUrl] = useState('');
  const [userDescription, setUserDescription] = useState('사용자는 개발자이고, 캐릭터와 편하게 대화하는 사용자다.');
  const [roomEditDraft, setRoomEditDraft] = useState<any>({ title: '', world_setting_id: '', user_description: '', participants: [] });
  const [characterAId, setCharacterAId] = useState('');
  const [characterBId, setCharacterBId] = useState('');
  const [multiCharacterIds, setMultiCharacterIds] = useState<string[]>(['']);
  const [multiCharacterRoles, setMultiCharacterRoles] = useState<string[]>(['primary']);
  const [scene, setScene] = useState<any>({ location: '작업방', mood: '개발 테스트', seed: '오늘의 개발 방향을 짧게 정리합니다.', opening_scene: '', opening_line: '', tone_preset: '', relationship_archetype: '', compression_focus: '', tts_enabled: false });
  const [speakerId, setSpeakerId] = useState('');
  const [compose, setCompose] = useState('');
  const [composeExpanded, setComposeExpanded] = useState(false);
  const [conversationRuntimeSetting, setConversationRuntimeSetting] = useState<any>(DEFAULT_RUNTIME_SETTING);
  const [chatSettingsOpen, setChatSettingsOpen] = useState(false);
  const renderChatImages = useUiStore((state) => state.renderChatImages);
  const setRenderChatImages = useUiStore((state) => state.setRenderChatImages);
  const [userMemoryDraft, setUserMemoryDraft] = useState<any>({ character_id: '__room__', memory_type: 'user_note', content: '', importance: 5 });
  const [status, setStatus] = useState(() => t('준비됨'));
  const [busy, setBusy] = useState(false);
  const { runtimeDefaultSetting, setRuntimeDefaultSetting, saveRuntimeDefaultSetting } = useRuntimeDefaultSetting({ settingsMutations: settingsMutations as any, setBusy, setStatus });
  const chatEndRef = useRef<any>(null);
  const chatThreadRef = useRef<any>(null);
  const composerTextareaRef = useRef<any>(null);
  const skipNextAutoScrollRef = useRef<boolean>(false);
  const nextScrollModeRef = useRef<any>('idle');
  const lastMessageTailRef = useRef<any>({ conversationId: '', tailId: '', length: 0 });
  const activeGenerationJobRef = useRef<any>(null);
  const activeGenerationSubscriptionRef = useRef<any>(null);
  const appRoute = route as typeof route & { id?: string };
  const routeId = appRoute.id || '';
  const selectedConversationId = route.page === 'conversationDetail' ? routeId : '';
  const characterById = useMemo(() => new Map(characters.map((c) => [c.id, c])), [characters]);
  const { resizeComposerTextarea, handleComposerKeyDown } = useConversationComposer({ compose, composerTextareaRef, sendConversationMessage });
  const participantName = (id) => id === SYSTEM_ID ? t('시스템') : id === 'storyteller' ? t('스토리텔링') : id === USER_ID ? t('나') : characterById.get(id)?.name || `${t('삭제된 캐릭터')} · ${id.slice(-4)}`;
  const defaultCastRoleForSlot = (index) => CAST_ROLE_PRESETS.filter((preset) => preset.key)[index]?.key || 'support';

  async function refetchRuntimeDefaultSetting() {
    const result = await bootstrap.runtimeDefault.refetch();
    if (result.data) setRuntimeDefaultSetting(result.data as any);
    return result.data;
  }

  async function loadCharacters() {
    const [list, usageRanking] = await Promise.all([characterApi.list(), characterApi.usageRanking().catch(() => [])]) as [any[], any[]];
    setCharacters(list);
    setCharacterUsageRanking(usageRanking);
    setCharacterAId((current) => current || list[0]?.id || '');
    setCharacterBId((current) => current || list[1]?.id || '');
    setMultiCharacterIds((current) => {
      const defaults = [list[0]?.id || ''];
      const next = current.length >= 1 ? [...current] : [''];
      return next.map((id, index) => id || defaults[index] || '');
    });
    setMultiCharacterRoles((current) => (current.length >= 1 ? current : ['primary']));
    return list;
  }
  async function loadConversations() {
    const list = await conversationApi.list() as any[];
    const enriched = await Promise.all(list.slice(0, 12).map(async (room) => ({
      ...room,
      participants: await conversationApi.participants(room.id).catch(() => []),
    })));
    const enrichedById = new Map(enriched.map((room) => [room.id, room]));
    const next = list.map((room) => enrichedById.get(room.id) || room);
    setConversations(next);
    return next;
  }
  async function markConversationRead(conversationId) {
    if (!conversationId) return null;
    const readState = await conversationApi.markRead(conversationId).catch(() => null);
    setConversations((current) => current.map((room) => room.id === conversationId ? { ...room, has_unread: false, unread_count: 0 } : room));
    setConversation((current) => current?.id === conversationId ? { ...current, has_unread: false, unread_count: 0 } : current);
    return readState;
  }
  useEffect(() => {
    if (route.page !== 'home' || !conversations.length) return;
    const needsParticipantPreview = conversations.slice(0, 10).some((room) => !Array.isArray(room.participants));
    if (needsParticipantPreview) loadConversations().catch((error) => setStatus(`${t('최근 대화방 참여자 불러오기 실패 ·')} ${error.message}`));
  }, [route.page, conversations, t]);
  useEffect(() => {
    const handleVisibilityChange = async () => {
      if (document.visibilityState !== 'visible') return;
      const activeJob = activeGenerationJobRef.current;
      if (!activeJob || route.page !== 'conversationDetail' || activeJob.conversationId !== selectedConversationId) return;
      try {
        const job = await conversationApi.generationJob(activeJob.conversationId, activeJob.jobId) as any;
        if (job.status === 'failed' || job.status === 'cancelled') {
          activeGenerationJobRef.current = null;
          const errorMessage = job.error_message || t(job.status === 'cancelled' ? '응답 생성 job 취소됨' : '응답 생성 job 실패');
          setMessages((current) => current.map((item) => item.id === activeJob.localMessageId ? { ...item, failed: true, error: errorMessage } : item).filter((item) => !item.is_typing));
          setStatus(`${t('전송 실패 ·')} ${errorMessage}`);
          return;
        }
        if (job.status !== 'completed') return;
        const serverMessages = await loadGeneratedJobMessages(activeJob.conversationId, activeJob.incomingMessage, job);
        await revealServerMessages(serverMessages, activeJob.localMessageId);
        activeGenerationJobRef.current = null;
        await markConversationRead(activeJob.conversationId);
        setStatus(`${t('대화 생성 완료 ·')} ${formatUiCount(Math.max(0, serverMessages.length - 1), 'messages', locale)}`);
      } catch (error) {
        setStatus(`${t('생성 job 동기화 실패 ·')} ${error.message}`);
      }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, [locale, route.page, selectedConversationId, t]);
  useEffect(() => () => {
    activeGenerationSubscriptionRef.current?.close();
    activeGenerationSubscriptionRef.current = null;
  }, [route.page, selectedConversationId]);

  async function uploadConversationThumbnail(file) {
    if (!file) return;
    setBusy(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const result = await conversationApi.uploadThumbnail(formData) as any;
      setConversationThumbnailUrl(result.thumbnail_url || '');
      setStatus(t('방 썸네일 업로드 완료'));
    } catch (error) {
      setStatus(`${t('방 썸네일 업로드 실패 ·')} ${error.message}`);
    } finally {
      setBusy(false);
    }
  }
  async function uploadWorldThumbnail(file) {
    if (!file) return;
    setBusy(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const result = await settingsApi.uploadWorldThumbnail(formData) as any;
      setWorldSettingDraft((current) => ({ ...normalizeWorld(current), thumbnail_url: result.thumbnail_url || '' }));
      setStatus(t('세계관 썸네일 업로드 완료'));
    } catch (error) {
      setStatus(`${t('세계관 썸네일 업로드 실패 ·')} ${error.message}`);
    } finally {
      setBusy(false);
    }
  }
  async function loadPresets() { const list = await settingsApi.presets() as any[]; setPresets(list); return list; }
  async function loadChatCommands() { const list = await settingsApi.chatCommands() as any[]; setChatCommands(list); return list; }
  async function loadWorldSettings() { const list = await settingsApi.worldSettings() as any[]; setWorldSettings(list); return list; }
  async function loadTtsVoices() { const list = await settingsApi.ttsVoices() as any[]; setTtsVoices(list?.length ? list : FALLBACK_TTS_VOICES); return list; }
  async function loadTtsModels() { const list = await settingsApi.ttsModels() as any[]; setTtsModels(list?.length ? list : FALLBACK_TTS_MODELS); return list; }
  async function loadConversationRuntimeSetting(conversationId = selectedConversationId) {
    if (!conversationId) return null;
    const setting = await conversationApi.runtimeSetting(conversationId) as any;
    setConversationRuntimeSetting(setting);
    return setting;
  }
  async function saveConversationRuntimeSetting() {
    if (!selectedConversationId) return;
    setBusy(true);
    try {
      const savedSetting = await (conversationMutations.saveRuntimeSetting.mutateAsync({ conversationId: selectedConversationId, payload: { model_key: conversationRuntimeSetting.model_key, fallback_model_key: conversationRuntimeSetting.fallback_model_key || null, compression_model_key: conversationRuntimeSetting.compression_model_key || DEFAULT_RUNTIME_SETTING.compression_model_key, compression_fallback_model_key: conversationRuntimeSetting.compression_fallback_model_key || null, response_length_preset: conversationRuntimeSetting.response_length_preset || DEFAULT_RUNTIME_SETTING.response_length_preset, compression_interval_turns: Number(conversationRuntimeSetting.compression_interval_turns || DEFAULT_RUNTIME_SETTING.compression_interval_turns) } }) as any);
      const savedRoom = await (conversationMutations.updateConversation.mutateAsync({ id: selectedConversationId, payload: { tts_enabled: !!conversation?.tts_enabled } }) as any);
      setConversation(savedRoom);
      setConversationRuntimeSetting(savedSetting);
      setChatSettingsOpen(false);
      setStatus(t('이 방 채팅 설정 저장 완료'));
    } finally { setBusy(false); }
  }
  async function loadConversationContext(conversationId = selectedConversationId) {
    if (!conversationId) return null;
    setContextLoading(true);
    try {
      const context = await conversationApi.context(conversationId);
      setConversationContext(context);
      return context;
    } catch (error) {
      setStatus(`${t('대화 흐름 불러오기 실패 ·')} ${error.message}`);
      return null;
    } finally {
      setContextLoading(false);
    }
  }
  async function loadBattleState(conversationId = selectedConversationId, room = conversation) {
    if (!conversationId || room?.genre_mode !== 'battle') { setBattleState(null); return null; }
    const state = await conversationApi.battleState(conversationId);
    setBattleState(state);
    return state;
  }
  async function clearActiveCommand(commandId = '') {
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
  async function openConversationContextDrawer() {
    setContextDrawerOpen(true);
    await Promise.all([loadConversationContext(), loadBattleState()]);
  }
  async function compressConversationNow() {
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
    if (!selectedConversationId || !memory?.id) return;
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('memory', locale)))) return;
    setBusy(true);
    try {
      await conversationApi.deleteMemory(selectedConversationId, memory.id);
      await loadConversationContext(selectedConversationId);
      setStatus(t('유저노트 삭제 완료'));
    } catch (error) {
      setStatus(`${t('유저노트 삭제 실패 ·')} ${error.message}`);
    } finally { setBusy(false); }
  }
  async function openConversation(conversationId) { navigate(`/conversations/${conversationId}`); }
  async function fetchConversation(conversationId) {
    setOlderPagingReady(false);
    setMessageSelectionMode(false);
    setSelectedMessageIds([]);
    setShowJumpToLatest(false);
    nextScrollModeRef.current = 'initial';
    lastMessageTailRef.current = { conversationId: '', tailId: '', length: 0 };
    const [room, roomMessages, roomParticipants, runtimeSetting] = await Promise.all([
      conversationApi.get(conversationId),
      conversationApi.messages(conversationId),
      conversationApi.participants(conversationId),
      conversationApi.runtimeSetting(conversationId),
    ]) as [any, any[], any[], any];
    setConversation(room);
    setMessages(roomMessages);
    setHasOlderMessages(roomMessages.length > 0);
    setParticipants(roomParticipants);
    setConversationRuntimeSetting(runtimeSetting);
    setConversationContext(null);
    if (room.genre_mode === 'battle') await loadBattleState(conversationId, room);
    else setBattleState(null);
    setSpeakerId(USER_ID);
    await markConversationRead(conversationId);
    setStatus(t('대화 표시 중'));
  }
  async function loadOlderMessages() {
    if (!selectedConversationId || loadingOlderMessages || !hasOlderMessages || messages.length === 0) return;
    const oldestPersisted = messages.find((message) => message.id && !String(message.id).startsWith('local_') && !message.is_typing);
    if (!oldestPersisted) return;
    const scroller = chatThreadRef.current;
    const previousScrollHeight = scroller?.scrollHeight || 0;
    const previousScrollTop = scroller?.scrollTop || 0;
    setLoadingOlderMessages(true);
    try {
      const older = await conversationApi.messages(selectedConversationId, oldestPersisted.id) as any[];
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
        const currentIds = new Set(current.map((message) => message.id));
        return [...uniqueOlder.filter((message) => !currentIds.has(message.id)), ...current];
      });
      requestAnimationFrame(() => {
        const node = chatThreadRef.current;
        if (!node) return;
        node.scrollTop = node.scrollHeight - previousScrollHeight + previousScrollTop;
      });
      setStatus(t('이전 대화 불러옴'));
    } catch (error) {
      setStatus(`${t('이전 대화 불러오기 실패 ·')} ${error.message}`);
    } finally {
      setLoadingOlderMessages(false);
    }
  }

  async function renameConversation(title) {
    if (!selectedConversationId) return;
    setBusy(true);
    try {
      const updated = await conversationMutations.updateConversation.mutateAsync({ id: selectedConversationId, payload: { title } }) as any;
      setConversation(updated);
      await loadConversations();
      setStatus(t('대화방 제목 변경 완료'));
    } finally { setBusy(false); }
  }
  async function loadConversationEditDraft(conversationId = routeId) {
    if (!conversationId) return;
    setBusy(true);
    try {
      const [room, context, roomParticipants] = await Promise.all([conversationApi.get(conversationId), conversationApi.context(conversationId).catch(() => null), conversationApi.participants(conversationId).catch(() => [])]) as [any, any, any[]];
      setRoomEditDraft(sceneDraftFromContext(room, context, roomParticipants));
      setStatus(t('수정 준비 완료'));
    } finally { setBusy(false); }
  }
  async function saveConversationRoomEdit() {
    if (!routeId || !roomEditDraft.title.trim()) { setStatus(t('대화방 제목은 필수입니다.')); return; }
    if (!roomEditDraft.world_setting_id) { setStatus(t('세계관 선택은 필수입니다.')); return; }
    const draftParticipants = roomEditDraft.participants || [];
    const characterParticipants = draftParticipants.filter((participant) => participant.type === 'character');
    const selectedCharacterIds = characterParticipants.map((participant) => participant.id).filter(Boolean);
    if (characterParticipants.length < 1 || selectedCharacterIds.length !== characterParticipants.length) { setStatus(t('캐릭터를 1명 이상 선택해야 합니다.')); return; }
    if (new Set(selectedCharacterIds).size !== selectedCharacterIds.length) { setStatus(t('서로 다른 캐릭터를 선택해야 합니다.')); return; }
    setBusy(true);
    try {
      const normalizedParticipants = draftParticipants
        .filter((participant) => participant.type !== 'character' || participant.id)
        .map((participant, index) => ({
          type: participant.type,
          id: participant.id,
          role: participant.type === 'character' ? participant.role || null : null,
          order_index: index,
        }));
      const updated = await conversationMutations.updateConversation.mutateAsync({ id: routeId, payload: {
        title: roomEditDraft.title.trim(),
        world_setting_id: roomEditDraft.world_setting_id,
        genre_mode: normalizeGenreMode(worldSettings.find((world) => world.id === roomEditDraft.world_setting_id)?.genre_mode || roomEditDraft.genre_mode),
        participants: normalizedParticipants,
        scene: { user_description: roomEditDraft.user_description || null },
      } }) as any;
      setConversation(updated);
      await loadConversations();
      setStatus(t('대화방 수정 완료'));
      navigate('/conversations');
    } finally { setBusy(false); }
  }
  async function inviteCharacterToConversation(characterId) {
    if (!selectedConversationId || !characterId) return;
    setBusy(true);
    try {
      await conversationApi.inviteParticipant(selectedConversationId, characterId);
      await fetchConversation(selectedConversationId);
      await loadConversationContext(selectedConversationId);
      setStatus(t('캐릭터 초대 완료'));
    } finally { setBusy(false); }
  }
  async function removeCharacterFromConversation(characterId) {
    if (!selectedConversationId || !characterId) return;
    const name = participantName(characterId);
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('removeCharacter', locale, name)))) return;
    setBusy(true);
    try {
      await conversationApi.removeCharacterParticipant(selectedConversationId, characterId);
      await fetchConversation(selectedConversationId);
      await loadConversationContext(selectedConversationId);
      setStatus(t('캐릭터 내보내기 완료'));
    } finally { setBusy(false); }
  }
  async function updateCharacterRoleInConversation(characterId, role) {
    if (!selectedConversationId || !characterId) return;
    setBusy(true);
    try {
      await conversationApi.updateParticipantRole(selectedConversationId, characterId, role || null);
      await fetchConversation(selectedConversationId);
      await loadConversationContext(selectedConversationId);
      setStatus(t('캐릭터 역할 수정 완료'));
    } finally { setBusy(false); }
  }
  async function saveCharacter() {
    if (!draft.name.trim() || !draft.persona.trim()) { setStatus(t('이름과 페르소나는 필수입니다.')); return; }
    setBusy(true);
    try {
      const isNew = route.page === 'characterNew';
      const saved = await characterMutations.saveCharacter.mutateAsync({ id: isNew ? null : routeId, payload: normalizeCharacterForApi(draft) }) as any;
      await loadCharacters(); setStatus(t('캐릭터 저장 완료')); navigate(`/characters/${saved.id}`);
    } finally { setBusy(false); }
  }
  async function deleteCharacter() {
    if (!routeId) return;
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('character', locale, draft.name || routeId)))) return;
    await characterMutations.deleteCharacter.mutateAsync(routeId);
    await loadCharacters(); navigate('/characters');
  }
  async function playCharacterTtsSample(characterDraft = draft) {
    if (!routeId) return;
    const provider = characterDraft.tts_provider || 'supertonic';
    const providerModels = ttsModels.filter((item) => (item.provider || 'supertonic') === provider);
    const providerVoices = ttsVoices.filter((item) => (item.provider || 'supertonic') === provider);
    const providerModelKeys = new Set(providerModels.map((item) => item.key));
    const providerVoiceKeys = new Set(providerVoices.map((item) => item.key));
    const model = providerModelKeys.has(characterDraft.tts_model) ? characterDraft.tts_model : (providerModels[0]?.key || (provider === 'gemini' ? 'gemini-3.1-flash-tts-preview' : 'supertonic-3'));
    const voiceStyle = providerVoiceKeys.has(characterDraft.tts_voice_style) ? characterDraft.tts_voice_style : (providerVoices[0]?.key || (provider === 'gemini' ? 'Kore' : 'F1'));
    setBusy(true);

    try {
      const result = await characterMutations.playTtsSample.mutateAsync({ id: routeId, payload: { text: characterDraft.tts_sample_text || EMPTY_CHARACTER.tts_sample_text, tts_provider: provider, tts_model: model, voice_style: voiceStyle } }) as any;
      const audio = new Audio(assetUrlFor(result.audio_url));
      await audio.play();
      setStatus(t('TTS 샘플 재생'));
    } catch (error) {
      setStatus(`${t('TTS 샘플 실패 ·')} ${error.message}`);
    } finally { setBusy(false); }
  }
  async function generateMessageTts(message) {
    if (!message?.id || message.id.startsWith('local_') || message.is_typing) return;
    setBusy(true);
    try {
      const result = await conversationMutations.generateMessageTts.mutateAsync(message.id) as any;
      setMessages((current) => current.map((item) => item.id === message.id ? { ...item, tts_audio_url: result.audio_url, tts_text: result.tts_text } : item));
      const audio = new Audio(assetUrlFor(result.audio_url));
      await audio.play().catch(() => {});
      setStatus(t('TTS 생성 완료'));
    } catch (error) {
      setStatus(`${t('TTS 생성 실패 ·')} ${error.message}`);
    } finally { setBusy(false); }
  }
  async function uploadAvatar(file, updateDraft) {
    if (!file) return;
    setBusy(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const uploaded = await characterMutations.uploadAvatar.mutateAsync(formData) as any;
      updateDraft('avatar_url', uploaded.avatar_url);
      setStatus(t('아바타 업로드 완료. 저장을 눌러 캐릭터에 반영하세요.'));
    } catch (error) {
      setStatus(`${t('아바타 업로드 실패 ·')} ${error.message}`);
    } finally {
      setBusy(false);
    }
  }
  async function loadCharacterAssets(characterId = routeId) {
    if (!characterId) return [];
    setCharacterAssetsLoading(true);
    try {
      const list = await characterApi.assets(characterId) as any[];
      setCharacterAssets(list);
      return list;
    } catch (error) {
      setStatus(`${t('에셋 불러오기 실패 ·')} ${error.message}`);
      return [];
    } finally {
      setCharacterAssetsLoading(false);
    }
  }
  function normalizeAssetDraft() {
    return {
      label: assetDraft.label.trim(),
      description: assetDraft.description?.trim() || null,
      image_url: assetDraft.image_url.trim(),
      tags: parseTagInput(assetDraft.tags),
      mood_tags: parseTagInput(assetDraft.mood_tags),
      scene_tags: parseTagInput(assetDraft.scene_tags),
      outfit_tags: parseTagInput(assetDraft.outfit_tags),
      pose_tags: parseTagInput(assetDraft.pose_tags),
      expression_tags: parseTagInput(assetDraft.expression_tags),
      priority: Number(assetDraft.priority || 50),
      enabled: assetDraft.enabled !== false,
      is_default: !!assetDraft.is_default,
    };
  }
  async function createCharacterAsset() {
    if (!routeId || !assetDraft.label.trim() || !assetDraft.image_url.trim()) return;
    setBusy(true);
    try {
      await characterMutations.createAsset.mutateAsync({ id: routeId, payload: normalizeAssetDraft() });
      setAssetDraft({ label: '', description: '', image_url: '', tags: '', mood_tags: '', scene_tags: '', outfit_tags: '', pose_tags: '', expression_tags: '', priority: 50, enabled: true, is_default: false });
      await Promise.all([loadCharacterAssets(routeId), loadCharacters()]);
      setStatus(t('에셋 추가 완료'));
    } finally { setBusy(false); }
  }
  async function uploadCharacterAsset(file) {
    if (!file || !routeId) return;
    setBusy(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('label', assetDraft.label || '이미지 에셋');
      formData.append('description', assetDraft.description || '');
      formData.append('tags', assetDraft.tags || '');
      formData.append('mood_tags', assetDraft.mood_tags || '');
      formData.append('scene_tags', assetDraft.scene_tags || '');
      formData.append('outfit_tags', assetDraft.outfit_tags || '');
      formData.append('pose_tags', assetDraft.pose_tags || '');
      formData.append('expression_tags', assetDraft.expression_tags || '');
      formData.append('priority', String(assetDraft.priority || 50));
      formData.append('enabled', String(assetDraft.enabled !== false));
      formData.append('is_default', String(!!assetDraft.is_default));
      await characterMutations.uploadAsset.mutateAsync({ id: routeId, formData });
      await Promise.all([loadCharacterAssets(routeId), loadCharacters()]);
      setStatus(t('에셋 업로드 완료'));
    } finally { setBusy(false); }
  }
  async function patchCharacterAsset(assetId, patch) {
    setBusy(true);
    try {
      await characterMutations.patchAsset.mutateAsync({ assetId, patch });
      await Promise.all([loadCharacterAssets(routeId), loadCharacters()]);
    } finally { setBusy(false); }
  }
  async function deleteCharacterAsset(assetId) {
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('asset', locale)))) return;
    setBusy(true);
    try {
      await characterMutations.deleteAsset.mutateAsync(assetId);
      await loadCharacterAssets(routeId);
      setStatus(t('에셋 삭제 완료'));
    } finally { setBusy(false); }
  }
  async function setDefaultCharacterAsset(assetId) {
    setBusy(true);
    try {
      await characterMutations.setDefaultAsset.mutateAsync(assetId);
      await Promise.all([loadCharacterAssets(routeId), loadCharacters()]);
      setStatus(t('기본 프사 변경 완료'));
    } finally { setBusy(false); }
  }

  async function savePreset() {
    if (!presetDraft.title.trim() || !presetDraft.content.trim()) { setStatus(t('프리셋 제목과 내용은 필수입니다.')); return; }
    setBusy(true);
    try {
      const isNew = route.page === 'presetNew';
      const body = { preset_type: presetDraft.preset_type || 'room_seed', title: presetDraft.title.trim(), content: presetDraft.content.trim(), description: presetDraft.description?.trim() || null, enabled: presetDraft.enabled !== false };
      const saved = await settingsMutations.savePreset.mutateAsync({ id: isNew ? null : routeId, payload: body }) as any;
      await loadPresets(); setStatus(t('프리셋 저장 완료')); navigate(`/presets/${saved.id}`);
    } finally { setBusy(false); }
  }
  async function deletePreset(id = routeId) { if (!(await confirmLocalizedDanger(formatDangerConfirmation('preset', locale)))) return; await settingsMutations.deletePreset.mutateAsync(id); await loadPresets(); navigate('/presets'); }
  async function saveChatCommand() {
    const generationPrompt = chatCommandDraft.generation_prompt || '';
    const postprocessPrompt = chatCommandDraft.postprocess_prompt || chatCommandDraft.prompt || '';
    if (!chatCommandDraft.name?.trim() || !(generationPrompt.trim() || postprocessPrompt.trim())) { setStatus(t('커맨드명과 생성/후처리 프롬프트 중 하나는 필수입니다.')); return; }
    setBusy(true);
    try {
      const body = {
        ...chatCommandDraft,
        name: chatCommandDraft.name.trim().replace(/^!/, ''),
        display_name: chatCommandDraft.name.trim().replace(/^!/, ''),
        description: chatCommandDraft.description || '',
        prompt: postprocessPrompt || generationPrompt,
        generation_prompt: generationPrompt,
        postprocess_prompt: postprocessPrompt,
        postprocess_target: chatCommandDraft.postprocess_target || 'last_bubble',
        postprocess_probability: Math.max(0, Math.min(100, Number(chatCommandDraft.postprocess_probability ?? 100))),
        postprocess_context_options: Array.isArray(chatCommandDraft.postprocess_context_options) ? chatCommandDraft.postprocess_context_options : ['scene', 'world', 'turn_messages'],
        priority: Number(chatCommandDraft.priority || 0),
        enabled: chatCommandDraft.enabled !== false,
      };
      const saved = await settingsMutations.saveChatCommand.mutateAsync({ id: chatCommandDraft.id || null, payload: body }) as any;
      await loadChatCommands(); setChatCommandDraft(saved); setStatus(t('커맨드 저장 완료')); navigate(`/chat-commands/${saved.id}`);
    } finally { setBusy(false); }
  }
  async function deleteChatCommand(command) {
    if (!command?.id) return;
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('command', locale, command.display_name || command.name)))) return;
    await settingsMutations.deleteChatCommand.mutateAsync(command.id);
    await loadChatCommands(); setChatCommandDraft({ name: '', display_name: '', description: '', prompt: '', generation_prompt: '', postprocess_prompt: '', postprocess_target: 'last_bubble', postprocess_probability: 100, postprocess_context_options: ['scene', 'world', 'turn_messages'], enabled: true, priority: 0 }); setStatus(t('커맨드 삭제 완료')); navigate('/chat-commands');
  }
  async function saveWorldSetting() {
    if (!worldSettingDraft.title?.trim() || !worldSettingDraft.world_seed?.trim()) { setStatus(t('세계관 이름과 세계관/규칙은 필수입니다.')); return; }
    setBusy(true);
    try {
      const payload = { ...worldSettingDraft, title: worldSettingDraft.title.trim(), thumbnail_url: worldSettingDraft.thumbnail_url || null, description: worldSettingDraft.description || null, genre_mode: normalizeGenreMode(worldSettingDraft.genre_mode), tags: Array.isArray(worldSettingDraft.tags) ? worldSettingDraft.tags : [], enabled: worldSettingDraft.enabled !== false };
      const saved = await settingsMutations.saveWorldSetting.mutateAsync({ id: worldSettingDraft.id || null, payload }) as any;
      await loadWorldSettings(); setWorldSettingDraft(saved); setStatus(t('세계관 저장 완료'));
    } finally { setBusy(false); }
  }
  async function deleteWorldSetting(world) {
    if (!world?.id) return;
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('world', locale, world.title || t('세계관'))))) return;
    await settingsMutations.deleteWorldSetting.mutateAsync(world.id);
    await loadWorldSettings(); setWorldSettingDraft({ title: '', thumbnail_url: '', description: '', genre_mode: DEFAULT_GENRE_MODE, location: '', mood: '', world_seed: '', opening_scene: '', opening_line: '', tone_preset: '', relationship_archetype: '', compression_focus: '', tags: [], enabled: true }); setStatus(t('세계관 삭제 완료'));
  }
  function applyPreset(target, content) {
    if (target === 'room_seed') setScene((current) => ({ ...current, seed: content }));
    if (target === 'compression_focus') setScene((current) => ({ ...current, compression_focus: content }));
    if (target === 'user_setting') setUserDescription(content);
    if (target === 'speech_style') setDraft((current) => ({ ...current, speech_style: [current.speech_style, content].filter(Boolean).join('\n') }));
  }
  function applyRoomEditPreset(target, content) {
    setRoomEditDraft((current) => target === 'user_setting'
      ? { ...current, user_description: content }
      : target === 'room_seed'
        ? { ...current, seed: content }
        : target === 'compression_focus'
          ? { ...current, compression_focus: content }
          : current);
  }
  function extractUserDescriptionFromScene(sceneState) {
    if (sceneState?.user_description) return sceneState.user_description;
    const summary = sceneState?.summary || '';
    return summary.startsWith('User description: ') ? summary.replace(/^User description:\s*/, '') : '';
  }
  function sceneDraftFromContext(room, context, roomParticipants = []) {
    const sceneState = context?.scene || {};
    return {
      title: room?.title || '',
      thumbnail_url: room?.thumbnail_url || '',
      genre_mode: normalizeGenreMode(room?.genre_mode),
      world_setting_id: room?.world_setting_id || '',
      participants: roomParticipants.map((participant, index) => ({ ...participant, role: participant.role || '', order_index: participant.order_index ?? index })),
      user_description: extractUserDescriptionFromScene(sceneState),
    };
  }
  function updateMultiCharacterSlot(index, value) {
    setMultiCharacterIds((current) => current.map((id, slotIndex) => slotIndex === index ? value : id));
  }
  function updateMultiCharacterRole(index, value) {
    setMultiCharacterRoles((current) => current.map((role, slotIndex) => slotIndex === index ? value : role));
  }
  function addMultiCharacterSlot() { setMultiCharacterIds((current) => [...current, '']); setMultiCharacterRoles((current) => [...current, defaultCastRoleForSlot(current.length)]); }
  function removeMultiCharacterSlot(index) {
    setMultiCharacterIds((current) => current.length <= 1 ? current : current.filter((_, slotIndex) => slotIndex !== index));
    setMultiCharacterRoles((current) => current.length <= 1 ? current : current.filter((_, slotIndex) => slotIndex !== index));
  }
  async function deleteConversation(conversationId, event) {
    event?.stopPropagation();
    if (!conversationId || busy) return;
    const room = conversations.find((item) => item.id === conversationId);
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('conversation', locale, room?.title || conversationId)))) return;
    setBusy(true);
    try {
      await conversationMutations.deleteConversation.mutateAsync(conversationId);
      if (selectedConversationId === conversationId) navigate('/conversations');
      await loadConversations();
      setStatus(t('대화방 삭제 완료'));
    } catch (error) {
      setStatus(`${t('대화방 삭제 실패 ·')} ${error.message}`);
    } finally {
      setBusy(false);
    }
  }
  function requireCharacter(selectedId) {
    const character = characters.find((c) => c.id === selectedId);
    if (!character) throw new Error(t('캐릭터를 먼저 선택하세요.'));
    return character;
  }
  async function revealServerMessages(serverMessages, localMessageId) {
    const incoming = serverMessages[0];
    const generated = serverMessages.slice(1);
    nextScrollModeRef.current = 'append';
    setMessages((current) => {
      const withoutTransient = current.filter((item) => item.id !== localMessageId && !item.is_typing);
      const existingIds = new Set(withoutTransient.map((item) => item.id));
      return incoming && !existingIds.has(incoming.id) ? [...withoutTransient, incoming] : withoutTransient;
    });
    for (const message of generated) {
      const typingId = `typing_${message.id}`;
      setStatus(t('대화 표시 중'));
      nextScrollModeRef.current = 'append';
      setMessages((current) => {
        if (current.some((item) => item.id === message.id || item.id === typingId)) return current;
        return [...current.filter((item) => !item.is_typing), { ...message, id: typingId, content: '', action: '', thought: '', is_typing: true }];
      });
      await delay(BUBBLE_REVEAL_DELAY_MS);
      nextScrollModeRef.current = 'append';
      setMessages((current) => current.some((item) => item.id === message.id)
        ? current.filter((item) => item.id !== typingId)
        : current.map((item) => item.id === typingId ? message : item));
    }
  }
  async function startConversationRoom() {
    setBusy(true);
    try {
      const selectedCharacters = multiCharacterIds
        .map((id, index) => ({ id, index }))
        .filter((slot) => !!slot.id)
        .map((slot) => ({ character: requireCharacter(slot.id), role: multiCharacterRoles[slot.index] || null }));
      const uniqueIds = new Set(selectedCharacters.map(({ character }) => character.id));
      if (selectedCharacters.length < 1) throw new Error(t('참여 캐릭터를 1명 이상 선택해야 합니다.'));
      if (uniqueIds.size !== selectedCharacters.length) throw new Error(t('서로 다른 캐릭터를 선택해야 합니다.'));
      if (!selectedWorldSettingId) throw new Error(t('세계관을 먼저 선택해야 합니다.'));
      const createdRoomMode = selectedCharacters.length === 1 ? 'user_character' : 'character_character';
      const participants = selectedCharacters.length === 1
        ? [{ type: 'user', id: USER_ID, order_index: 0 }, { type: 'character', id: selectedCharacters[0].character.id, role: selectedCharacters[0].role, order_index: 1 }]
        : selectedCharacters.map(({ character, role }, index) => ({ type: 'character', id: character.id, role, order_index: index }));
      const selectedWorld = worldSettings.find((world) => world.id === selectedWorldSettingId);
      const conversation = await conversationMutations.createConversation.mutateAsync({ mode: createdRoomMode, genre_mode: normalizeGenreMode(selectedWorld?.genre_mode || genreMode), world_setting_id: selectedWorldSettingId, title: conversationTitle.trim() || null, thumbnail_url: null, participants, tts_enabled: !!scene.tts_enabled, scene: { user_description: userDescription || null } }) as any;
      setConversationTitle('');
      setConversationThumbnailUrl('');
      setSelectedWorldSettingId('');
      setConversation(conversation); setMessages([]); setHasOlderMessages(false); setOlderPagingReady(false); setParticipants(participants); await loadConversations();
      navigate(`/conversations/${conversation.id}`);
      setStatus(t('대화방 생성 완료'));
    } catch (error) { setStatus(error.message); }
    finally { setBusy(false); }
  }

  async function pollGenerationJob(conversationId, jobId) {
    let job: any = null;
    for (let attempt = 0; attempt < JOB_POLL_MAX_ATTEMPTS; attempt += 1) {
      job = await conversationApi.generationJob(conversationId, jobId) as any;
      if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') return job;
      setStatus(`${t('대화 생성 중…')} ${t(job.status === 'running' ? '응답 작성 중' : '대기 중')}`);
      await delay(JOB_POLL_INTERVAL_MS);
    }
    throw new Error(JOB_PENDING_TIMEOUT_MESSAGE);
  }

  async function waitForGenerationJob(conversationId, jobId) {
    const statusLabels = { queued: '대기 중', running: '응답 작성 중', retrying: '응답 재시도 중' } as const;
    let subscription: ReturnType<typeof subscribeGenerationJobLifecycle> | null = null;
    try {
      subscription = subscribeGenerationJobLifecycle({
        conversationId,
        jobId,
        onStatus: (event) => {
          const label = event.status in statusLabels
            ? t(statusLabels[event.status as keyof typeof statusLabels])
            : t(event.status === 'completed' ? '응답 저장 완료' : '응답 작성 중');
          setStatus(`${t('대화 생성 중…')} ${label}`);
        },
      });
      activeGenerationSubscriptionRef.current = subscription;
      await subscription.promise;
      return await conversationApi.generationJob(conversationId, jobId) as any;
    } catch (error) {
      if (error instanceof GenerationLifecycleSubscriptionClosedError) throw error;
      setStatus(t('실시간 상태 연결이 불안정해 polling으로 계속 확인 중…'));
      return await pollGenerationJob(conversationId, jobId);
    } finally {
      if (subscription && activeGenerationSubscriptionRef.current === subscription) activeGenerationSubscriptionRef.current = null;
    }
  }

  async function loadGeneratedJobMessages(conversationId, incomingMessage, job) {
    const latestMessages = await conversationApi.messages(conversationId) as any[];
    const generatedIds = new Set(job.generated_message_ids || []);
    const incoming = latestMessages.find((message) => message.id === incomingMessage?.id) || incomingMessage;
    const generated = latestMessages.filter((message) => generatedIds.has(message.id));
    return [incoming, ...generated].filter(Boolean);
  }

  async function submitConversationPayload(payload, localMessageId = `local_${Date.now()}`) {
    const clientRequestId = payload?.metadata?.client_request_id || newClientRequestId();
    const payloadWithRequestId = {
      ...payload,
      metadata: { ...(payload?.metadata || {}), client_request_id: clientRequestId },
    };
    const optimisticMessage = { id: localMessageId, conversation_id: selectedConversationId, ...payloadWithRequestId, retryPayload: payloadWithRequestId };
    nextScrollModeRef.current = 'append';
    setMessages((current) => {
      if (current.some((item) => item.id === localMessageId)) {
        return current.map((item) => item.id === localMessageId ? { ...item, failed: false, error: '', retryPayload: payloadWithRequestId } : item).filter((item) => !item.is_typing);
      }
      return [...current, optimisticMessage];
    });
    setStatus(t('대화 생성 job 생성 중…'));
    try {
      const created = await conversationMutations.createMessageJob.mutateAsync({ conversationId: selectedConversationId, payload: payloadWithRequestId }) as any;
      const jobId = created?.job?.id;
      const incomingMessage = created?.incoming_message;
      if (!jobId || !incomingMessage) throw new Error(t('응답 생성 job 정보를 받지 못했습니다.'));
      activeGenerationJobRef.current = { conversationId: selectedConversationId, jobId, localMessageId, incomingMessage, payload: payloadWithRequestId };
      setStatus(t('대화 생성 중… 백그라운드 job 확인 중'));
      const job = await waitForGenerationJob(selectedConversationId, jobId);
      if (job.status === 'failed' || job.status === 'cancelled') throw new Error(job.error_message || t(job.status === 'cancelled' ? '응답 생성 job 취소됨' : '응답 생성 job 실패'));
      const serverMessages = await loadGeneratedJobMessages(selectedConversationId, incomingMessage, job);
      await revealServerMessages(serverMessages, localMessageId);
      activeGenerationJobRef.current = null;
      setStatus(`${t('대화 생성 완료 ·')} ${formatUiCount(Math.max(0, serverMessages.length - 1), 'messages', locale)}`);
      await markConversationRead(selectedConversationId);
      await loadConversations();
      if (payload.battle_control) await loadBattleState(selectedConversationId);
      if (contextDrawerOpen) await Promise.all([loadConversationContext(), loadBattleState()]);
    } catch (error) {
      if (error instanceof GenerationLifecycleSubscriptionClosedError) {
        setStatus(t('화면 이동됨 · 생성 job은 서버에서 계속 진행 중'));
        return;
      }
      if (error.message === JOB_PENDING_TIMEOUT_MESSAGE && activeGenerationJobRef.current?.localMessageId === localMessageId) {
        setStatus(`${t('대화 생성 중…')} ${t(JOB_PENDING_TIMEOUT_MESSAGE)}`);
        setMessages((current) => current.map((item) => item.id === localMessageId ? { ...item, failed: false, error: '', retryPayload: payloadWithRequestId } : item).filter((item) => !item.is_typing));
        return;
      }
      activeGenerationJobRef.current = null;
      setStatus(`${t('전송 실패 ·')} ${error.message}`);
      setMessages((current) => current.map((item) => item.id === localMessageId ? { ...item, failed: true, error: error.message, retryPayload: payloadWithRequestId } : item).filter((item) => !item.is_typing));
    }
  }

  async function regenerateFromMessage(message) {
    if (!selectedConversationId || !message?.id || String(message.id).startsWith('local_') || busy) return;
    setBusy(true);
    try {
      setStatus(t('재생성 중…'));
      const serverMessages = await conversationMutations.regenerateMessage.mutateAsync({ conversationId: selectedConversationId, messageId: message.id }) as any[];
      await revealServerMessages([null, ...(serverMessages || [])], `regen_${message.id}`);
      setStatus(t('재생성 완료'));
      await loadConversations();
      if (contextDrawerOpen) await Promise.all([loadConversationContext(), loadBattleState()]);
    } catch (error) {
      setStatus(`${t('재생성 실패 ·')} ${error.message}`);
    } finally { setBusy(false); }
  }
  function enterMessageSelectionMode(messageId) {
    if (!messageId) return;
    setMessageSelectionMode(true);
    setSelectedMessageIds((current) => current.includes(messageId) ? current : [...current, messageId]);
    setStatus(t('버블 선택모드 · 삭제할 버블을 선택하세요'));
  }
  function toggleMessageSelection(messageId) {
    if (!messageId) return;
    setSelectedMessageIds((current) => current.includes(messageId) ? current.filter((id) => id !== messageId) : [...current, messageId]);
  }
  function clearMessageSelection() {
    setMessageSelectionMode(false);
    setSelectedMessageIds([]);
  }
  async function deleteSelectedMessages() {
    if (!selectedMessageIds.length || busy) return;
    const ids = [...selectedMessageIds];
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('messages', locale, ids.length)))) return;
    const localIds = ids.filter((id) => String(id).startsWith('local_'));
    const serverIds = ids.filter((id) => !String(id).startsWith('local_'));
    setBusy(true);
    try {
      if (serverIds.length) {
        await conversationMutations.bulkDeleteMessages.mutateAsync({ conversationId: selectedConversationId, messageIds: serverIds });
      }
      nextScrollModeRef.current = 'preserve';
      setMessages((current) => current.filter((item) => !ids.includes(item.id)));
      clearMessageSelection();
      if (contextDrawerOpen) await Promise.all([loadConversationContext(), loadBattleState()]);
      setStatus(t('선택한 메시지 삭제 완료'));
    } catch (error) {
      setStatus(`${t('선택 버블 삭제 실패 ·')} ${error.message}`);
    } finally { setBusy(false); }
  }
  async function updateMessageBubble(message, draft) {
    if (!message?.id || !selectedConversationId || busy) return null;
    setBusy(true);
    try {
      const updated = await conversationApi.updateMessage(selectedConversationId, message.id, draft) as any;
      nextScrollModeRef.current = 'preserve';
      setMessages((current) => current.map((item) => item.id === updated.id ? { ...item, ...updated } : item));
      setStatus(t('버블 수정 완료'));
      return updated;
    } catch (error) {
      setStatus(`${t('버블 수정 실패 ·')} ${error.message}`);
      throw error;
    } finally { setBusy(false); }
  }
  async function deleteMessageBubble(message) {
    if (!message?.id || busy) return;
    if (String(message.id).startsWith('local_') || message.failed) {
      nextScrollModeRef.current = 'preserve';
      setMessages((current) => current.filter((item) => item.id !== message.id));
      setStatus(t('임시 실패 버블 삭제 완료'));
      return;
    }
    if (!selectedConversationId) return;
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('message', locale)))) return;
    setBusy(true);
    try {
      await conversationMutations.deleteMessage.mutateAsync({ conversationId: selectedConversationId, messageId: message.id });
      nextScrollModeRef.current = 'preserve';
      setMessages((current) => current.filter((item) => item.id !== message.id));
      if (contextDrawerOpen) await Promise.all([loadConversationContext(), loadBattleState()]);
      setStatus(t('버블 삭제 완료'));
    } catch (error) {
      setStatus(`${t('버블 삭제 실패 ·')} ${error.message}`);
    } finally { setBusy(false); }
  }
  function scrollToLatest() {
    const node = chatThreadRef.current;
    if (node) node.scrollTo({ top: node.scrollHeight, behavior: 'smooth' });
    else chatEndRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
    setShowJumpToLatest(false);
  }
  async function retryMessage(message) {
    if (!message?.retryPayload || !selectedConversationId || busy) return;
    await submitConversationPayload(message.retryPayload, message.id);
  }

  async function sendConversationMessage() {
    if (!compose.trim() || !selectedConversationId) return;
    const activeJob = activeGenerationJobRef.current;
    if (activeJob?.conversationId === selectedConversationId) {
      setStatus(t('이미 이 대화방에서 응답 생성 job이 진행 중입니다. 완료 후 다시 전송 가능합니다.'));
      return;
    }
    const selected = speakerId || USER_ID;
    const parsed = parseBabeChatInputMarkup(compose.trim());
    const content = parsed.hadMarkup ? parsed.dialogue : compose.trim();
    const action = parsed.hadMarkup ? parsed.action : '';
    if (!content.trim() && !action.trim()) return;
    const battleControlPayload = buildBattleControlPayload();
    const metadata = parsed.hadMarkup && parsed.parts.length ? { render_parts: parsed.parts } : undefined;
    const payload = { speaker_type: selected === SYSTEM_ID ? 'system' : selected === USER_ID ? 'user' : 'character', speaker_id: selected, content, ...(action ? { action } : {}), ...(metadata ? { metadata } : {}), ...(battleControlPayload ? { battle_control: battleControlPayload } : {}) };
    setCompose('');
    setComposeExpanded(false);
    await submitConversationPayload(payload);
    if (battleControlPayload?.action === 'end') {
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
  useBootstrapSync({
    bootstrap,
    setBackendOnline,
    setCharacters,
    setCharacterUsageRanking,
    setCharacterAId,
    setCharacterBId,
    setMultiCharacterIds,
    setConversations,
    setPresets,
    setChatCommands,
    setWorldSettings,
    setTtsVoices,
    setTtsModels,
    setRuntimeDefaultSetting,
    setConversationRuntimeSetting,
    setStatus,
  });
  useRouteResourceSync({
    route,
    characters,
    presets,
    chatCommands,
    setDraft,
    setCharacterAssets,
    loadCharacterAssets,
    setPresetDraft,
    setChatCommandDraft,
    fetchConversation,
    loadConversationEditDraft,
    setStatus,
  });
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

  const {
    selectOptions,
    speechPresets,
    filteredPresets,
    visiblePresets,
    speakerOptions,
  } = useAppDerivedData({
    characters,
    presets,
    participants,
    presetQuery,
    presetPage,
  });

  const appOverlays = <>
    <ConversationInfoDrawer open={contextDrawerOpen} onClose={() => setContextDrawerOpen(false)} context={conversationContext} loading={contextLoading} conversation={conversation} participants={participants} characters={characters} participantName={participantName} onRenameConversation={renameConversation} onInviteCharacter={inviteCharacterToConversation} onRemoveCharacter={removeCharacterFromConversation} onUpdateCharacterRole={updateCharacterRoleInConversation} battleState={battleState} onUpdateSceneSummary={updateSceneSummary} userMemoryDraft={userMemoryDraft} onUserMemoryDraftChange={setUserMemoryDraft} onCreateUserMemory={createUserMemory} onUpdateMemory={updateConversationMemory} onDeleteMemory={deleteConversationMemory} busy={busy} />
    <AvatarPreviewModal preview={avatarPreview as any} onClose={() => setAvatarPreview(null)} />
    <CharacterCardPreviewModal character={cardPreview as any} onClose={() => setCardPreview(null)} />
  </>;

  const selectedMultiCharacterIds = multiCharacterIds.filter(Boolean);
  const canStartConversation = selectedMultiCharacterIds.length >= 1 && new Set(selectedMultiCharacterIds).size === selectedMultiCharacterIds.length && !!selectedWorldSettingId;
  const headerActions = (() => {
    if (route.page === 'home') return null;
    if (route.page === 'conversations') return <Button type="button" onClick={() => navigate('/conversations/new')}>{t('새 대화방')}</Button>;
    if (route.page === 'conversationNew') return <Button type="button" disabled={busy || !canStartConversation} onClick={startConversationRoom}>{t(busy ? '생성 중…' : '대화 시작')}</Button>;
    if (route.page === 'conversationEdit') return null;
    if (route.page === 'characters') return <Button type="button" onClick={() => navigate('/characters/new')}>{t('새 캐릭터')}</Button>;
    if (route.page === 'characterDetail' || route.page === 'characterNew') return <><Button type="button" variant="ghost" onClick={() => navigate('/characters')}>{t('목록으로')}</Button><Button type="button" variant="ghost" onClick={() => setCardPreview(draft)}>{t('카드 미리보기')}</Button></>;
    if (route.page === 'characterAssets') return <Button type="button" variant="ghost" onClick={() => navigate(`/characters/${route.id}`)}>{t('캐릭터 상세로')}</Button>;
    if (route.page === 'worldSettings') return <Button type="button" onClick={() => navigate('/world-settings/new')}>{t('새 세계관')}</Button>;
    if (route.page === 'worldSettingNew' || route.page === 'worldSettingDetail') return <Button type="button" variant="ghost" onClick={() => navigate('/world-settings')}>{t('목록으로')}</Button>;
    if (route.page === 'presets') return <Button type="button" onClick={() => navigate('/presets/new')}>{t('프리셋 추가')}</Button>;
    if (route.page === 'presetNew' || route.page === 'presetDetail') return <Button type="button" variant="ghost" onClick={() => navigate('/presets')}>{t('목록으로')}</Button>;
    if (route.page === 'chatCommands') return <Button type="button" onClick={() => navigate('/chat-commands/new')}>{t('새 커맨드')}</Button>;
    if (route.page === 'chatCommandNew' || route.page === 'chatCommandDetail') return <Button type="button" variant="ghost" onClick={() => navigate('/chat-commands')}>{t('목록')}</Button>;
    return null;
  })();

  return <AppShell route={route} backendOnline={backendOnline} drawerOpen={drawerOpen} setDrawerOpen={setDrawerOpen} navigate={navigate} overlays={appOverlays} headerActions={headerActions}>
    {route.page === 'home' && <ResourceListBoundary variant="home"><HomeView conversations={conversations} characters={characterUsageRanking.length ? characterUsageRanking : characters} allCharacters={characters} worldSettings={worldSettings} navigate={navigate} openConversation={openConversation} /></ResourceListBoundary>}
    {route.page === 'conversationNew' && <ConversationCreateView busy={busy} navigate={navigate} status={status} startConversationRoom={startConversationRoom} conversationTitle={conversationTitle} setConversationTitle={setConversationTitle} selectedWorldSettingId={selectedWorldSettingId} setSelectedWorldSettingId={setSelectedWorldSettingId} worldSettings={worldSettings} scene={scene} setScene={setScene} userDescription={userDescription} setUserDescription={setUserDescription} applyPreset={applyPreset} multiCharacterIds={multiCharacterIds} multiCharacterRoles={multiCharacterRoles} updateMultiCharacterSlot={updateMultiCharacterSlot} updateMultiCharacterRole={updateMultiCharacterRole} removeMultiCharacterSlot={removeMultiCharacterSlot} addMultiCharacterSlot={addMultiCharacterSlot} selectOptions={selectOptions} />}

    {route.page === 'characters' && <ResourceListBoundary><CharactersListView characters={characters} navigate={navigate} /></ResourceListBoundary>}
    {(route.page === 'characterDetail' || route.page === 'characterNew') && <CharacterDetailView route={route} draft={draft} setDraft={setDraft} ttsVoices={ttsVoices} ttsModels={ttsModels} busy={busy} navigate={navigate} setCardPreview={setCardPreview} saveCharacter={saveCharacter} deleteCharacter={deleteCharacter} uploadAvatar={uploadAvatar} playCharacterTtsSample={playCharacterTtsSample} speechPresets={speechPresets} applyPreset={applyPreset} characterAssets={characterAssets} characterAssetsLoading={characterAssetsLoading} assetDraft={assetDraft} setAssetDraft={setAssetDraft} createCharacterAsset={createCharacterAsset} uploadCharacterAsset={uploadCharacterAsset} patchCharacterAsset={patchCharacterAsset} deleteCharacterAsset={deleteCharacterAsset} setDefaultCharacterAsset={setDefaultCharacterAsset} />}
    {route.page === 'characterAssets' && <CharacterAssetsView route={route} draft={draft} characterAssets={characterAssets} characterAssetsLoading={characterAssetsLoading} busy={busy} assetDraft={assetDraft} setAssetDraft={setAssetDraft} navigate={navigate} createCharacterAsset={createCharacterAsset} uploadCharacterAsset={uploadCharacterAsset} patchCharacterAsset={patchCharacterAsset} deleteCharacterAsset={deleteCharacterAsset} setDefaultCharacterAsset={setDefaultCharacterAsset} assetUrlFor={assetUrlFor} />}

    {route.page === 'presets' && <ResourceListBoundary><PresetsView navigate={navigate} presetQuery={presetQuery} setPresetQuery={setPresetQuery} setPresetPage={setPresetPage} loadPresets={loadPresets} visiblePresets={visiblePresets} presetPage={presetPage} pageCount={pageCount} filteredPresets={filteredPresets} /></ResourceListBoundary>}
    {(route.page === 'presetNew' || route.page === 'presetDetail') && <PresetDetailView route={route} presetDraft={presetDraft} setPresetDraft={setPresetDraft} characters={characters} savePreset={savePreset} deletePreset={deletePreset} busy={busy} navigate={navigate} />}

    {route.page === 'chatCommands' && <ResourceListBoundary><ChatCommandsView route={route} navigate={navigate} chatCommands={chatCommands} chatCommandDraft={chatCommandDraft} setChatCommandDraft={setChatCommandDraft} saveChatCommand={saveChatCommand} deleteChatCommand={deleteChatCommand} loadChatCommands={loadChatCommands} busy={busy} /></ResourceListBoundary>}
    {(route.page === 'chatCommandNew' || route.page === 'chatCommandDetail') && <ChatCommandsView route={route} navigate={navigate} chatCommands={chatCommands} chatCommandDraft={chatCommandDraft} setChatCommandDraft={setChatCommandDraft} saveChatCommand={saveChatCommand} deleteChatCommand={deleteChatCommand} loadChatCommands={loadChatCommands} busy={busy} />}
    {route.page === 'worldSettings' && <ResourceListBoundary><WorldSettingsListView worldSettings={worldSettings} navigate={navigate} loadWorldSettings={loadWorldSettings} /></ResourceListBoundary>}
    {(route.page === 'worldSettingNew' || route.page === 'worldSettingDetail') && <WorldSettingsDetailView route={route} worldSettings={worldSettings} worldSettingDraft={worldSettingDraft} setWorldSettingDraft={setWorldSettingDraft} saveWorldSetting={saveWorldSetting} deleteWorldSetting={deleteWorldSetting} uploadWorldThumbnail={uploadWorldThumbnail} busy={busy} navigate={navigate} />}

    {route.page === 'settings' && <SettingsView navigate={navigate} />}
    {route.page === 'modelSettings' && <ModelSettingsView runtimeDefaultSetting={runtimeDefaultSetting} setRuntimeDefaultSetting={setRuntimeDefaultSetting} saveRuntimeDefaultSetting={saveRuntimeDefaultSetting} refetchRuntimeDefaultSetting={refetchRuntimeDefaultSetting} busy={busy} />}
    {route.page === 'statistics' && <StatisticsView conversations={conversations} />}

    {route.page === 'conversations' && <ResourceListBoundary variant="media"><ConversationsListView conversations={conversations} busy={busy} navigate={navigate} openConversation={openConversation} deleteConversation={deleteConversation} loadConversations={loadConversations} allCharacters={characters} worldSettings={worldSettings} /></ResourceListBoundary>}
    {route.page === 'conversationEdit' && <ConversationEditView busy={busy} navigate={navigate} saveConversationRoomEdit={saveConversationRoomEdit} roomEditDraft={roomEditDraft} setRoomEditDraft={setRoomEditDraft} applyRoomEditPreset={applyRoomEditPreset} worldSettings={worldSettings} characters={characters} />}
    {route.page === 'conversationDetail' && <ConversationDetailView conversation={conversation} selectedConversationId={selectedConversationId} status={status} navigate={navigate} openConversationContextDrawer={openConversationContextDrawer} messageSelectionMode={messageSelectionMode} selectedMessageIds={selectedMessageIds} clearMessageSelection={clearMessageSelection} busy={busy} deleteSelectedMessages={deleteSelectedMessages} messages={messages} characters={characters} participants={participants} setAvatarPreview={setAvatarPreview} chatEndRef={chatEndRef} chatThreadRef={chatThreadRef} loadOlderMessages={loadOlderMessages} hasOlderMessages={hasOlderMessages} loadingOlderMessages={loadingOlderMessages} olderPagingReady={olderPagingReady} retryMessage={retryMessage} renderChatImages={renderChatImages} generateMessageTts={generateMessageTts} regenerateFromMessage={regenerateFromMessage} deleteMessageBubble={deleteMessageBubble} updateMessageBubble={updateMessageBubble} enterMessageSelectionMode={enterMessageSelectionMode} toggleMessageSelection={toggleMessageSelection} setShowJumpToLatest={setShowJumpToLatest} showJumpToLatest={showJumpToLatest} scrollToLatest={scrollToLatest} sendConversationMessage={sendConversationMessage} speakerId={speakerId} setSpeakerId={setSpeakerId} speakerOptions={speakerOptions} participantName={participantName} setChatSettingsOpen={setChatSettingsOpen} chatSettingsOpen={chatSettingsOpen} conversationRuntimeSetting={conversationRuntimeSetting} setConversationRuntimeSetting={setConversationRuntimeSetting} saveConversationRuntimeSetting={saveConversationRuntimeSetting} setRenderChatImages={setRenderChatImages} setConversation={setConversation} compressConversationNow={compressConversationNow} composerTextareaRef={composerTextareaRef} compose={compose} setCompose={setCompose} resizeComposerTextarea={resizeComposerTextarea} handleComposerKeyDown={handleComposerKeyDown} composeExpanded={composeExpanded} setComposeExpanded={setComposeExpanded} battleState={battleState} battleControlDraft={battleControlDraft} setBattleControlDraft={setBattleControlDraft} chatCommands={chatCommands} clearActiveCommand={clearActiveCommand} />}
  </AppShell>;
}
