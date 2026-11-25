import { useConversationSession } from '../views/ConversationDetail/hooks/useConversationSession';
import { useAssetEditor } from '../views/CharacterAssets/useAssetEditor';
import { useOperationOwnership } from '../hooks/useOperationOwnership';
import { createEmptyWorld, normalizeWorld } from '../utils/worldSettings';
import { queryKeys } from '../api/queryKeys';
import { PageActionDisabled } from '../components/molecules/PageHeaderActions';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import Swal from 'sweetalert2';
import withReactContent from 'sweetalert2-react-content';
import 'sweetalert2/dist/sweetalert2.min.css';
import { characterApi, conversationApi, settingsApi } from '../api/resources';

import { unknownSubmissionsForRoom } from '../utils/generationRecovery';
import { RoomWork } from '../utils/roomWork';

import { useAppDerivedData } from '../hooks/useAppDerivedData';
import { useBootstrapResources } from '../hooks/useBootstrapResources';
import { useBootstrapSync } from '../hooks/useBootstrapSync';
import { useCharacterMutations } from '../hooks/useCharacterMutations';

import { useLegacyNavigation } from '../hooks/useLegacyNavigation';
import { usePreviewModals } from '../hooks/usePreviewModals';
import { useRouteResourceSync } from '../hooks/useRouteResourceSync';
import { useRuntimeDefaultSetting } from '../hooks/useRuntimeDefaultSetting';
import { useConversationMutations } from '../hooks/useConversationMutations';
import { useSettingsMutations } from '../hooks/useSettingsMutations';
import { useUiStore } from '../stores/uiStore';
import { useI18n } from '../i18n/I18nProvider';
import { formatDangerConfirmation } from '../i18n/core';
import { Button } from '../components/atoms';
import { OperationFeedback, isCompletionFeedback } from '../components/molecules/OperationFeedback';
import { isChatGenerationProgress } from '../utils/chatFeedback';
import { useConversationPanelLayout } from '../hooks/useConversationPanelLayout';
import type { DrawerTab } from '../components/organisms/ConversationInfoDrawer';
import { ChatSettingsBubble } from '../components/organisms/RuntimeSettings';
import { ResourceDetailFeedback } from '../components/molecules/ResourceDetailFeedback';
import { ResourceListBoundary } from '../components/molecules';
import { CharacterCardPreviewModal } from '../components/organisms';
import { AvatarPreviewModal } from '../components/organisms';
import { AppShell } from '../components/organisms';

import { ConversationInfoDrawer } from '../components/organisms';
import { HomeView } from '../views/Home';
import { ConversationCreateView } from '../views/ConversationCreate';
import { CharactersListView } from '../views/Characters';
import { ConversationsListView } from '../views/Conversations';
import { SettingsView } from '../views/Settings';
import { AgentSettingsView } from '../agent/settings';
import { agentActions, useAgentStore } from '../agent/store';
import { useNativeProposal } from '../agent/useNativeProposal';
import { ModelSettingsView } from '../views/ModelSettings';
import { StatisticsView } from '../views/Statistics';
import { ConversationDetailView } from '../views/ConversationDetail';
import { PresetsView } from '../views/Presets';
import { PresetDetailView } from '../views/PresetDetail';
import { ChatCommandsView } from '../views/ChatCommands';
import { chatCommandSavePayload, normalizeChatCommandDraft } from '../types/chatCommands';
import { WorldSettingsDetailView, WorldSettingsListView } from '../views/WorldSettings';
import { ConversationEditView } from '../views/ConversationEdit';
import { CharacterDetailView } from '../views/CharacterDetail';
import { CharacterAssetsView } from '../views/CharacterAssets';

import { assetUrlFor } from '../utils/assets';
import { normalizeCharacterForApi } from '../utils/character';
import { normalizeGenreMode } from '../utils/genre';
import { pageCount } from '../utils/pagination';

import { normalizeRuntimeSetting } from '../utils/runtimeSettings';

import { CAST_ROLE_PRESETS, EMPTY_CHARACTER, EMPTY_PRESET, FALLBACK_TTS_MODELS, FALLBACK_TTS_VOICES, SYSTEM_ID, USER_ID } from '../constants/domain';

const EMPTY_LIST: any[] = [];
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
  const agentOpen = useAgentStore(s => s.open && !!s.settings?.enabled);
  const confirmLocalizedDanger = (message: string) => confirmDanger(message, { title: t('삭제 확인'), confirmText: t('삭제'), cancelText: t('취소') });
  const bootstrap = useBootstrapResources();
  const characterMutations = useCharacterMutations();
  const conversationMutations = useConversationMutations();
  const settingsMutations = useSettingsMutations();
  const { route, navigate } = useLegacyNavigation();
  const appRoute = route as typeof route & { id?: string };
  const routeId = appRoute.id || '';
  const selectedConversationId = route.page === 'conversationDetail' ? routeId : '';
  const { avatarPreview, setAvatarPreview, cardPreview, setCardPreview } = usePreviewModals();
  const [backendOnline, setBackendOnline] = useState(false);
  const drawerOpen = useUiStore((state) => state.drawerOpen);
  const setDrawerOpen = useUiStore((state) => state.setDrawerOpen);
  const contextDrawerOpen = useUiStore((state) => state.contextDrawerOpen);
  const setContextDrawerOpen = useUiStore((state) => state.setContextDrawerOpen);
  const characters = (bootstrap.characters.data || EMPTY_LIST) as any[];
  const characterUsageRanking = (bootstrap.characterUsageRanking.data || EMPTY_LIST) as any[];
  const presets = (bootstrap.presets.data || EMPTY_LIST) as any[];
  const chatCommands = (bootstrap.chatCommands.data || EMPTY_LIST) as any[];
  const worldSettings = (bootstrap.worldSettings.data || EMPTY_LIST) as any[];
  useEffect(() => {
    // Labels/IDs only; selecting context never implicitly sends loaded page data.
    useAgentStore.setState({ resources: [
      ...characters.map(item => ({ kind: 'character' as const, id: item.id, label: item.name || item.id })),
      ...worldSettings.map(item => ({ kind: 'world' as const, id: item.id, label: item.title || item.id })),
    ] });
  }, [characters, worldSettings]);
  const [worldSettingDraft, setWorldSettingDraft] = useState<any>(createEmptyWorld());
  const [selectedWorldSettingId, setSelectedWorldSettingId] = useState('');
  const ttsVoices = ((bootstrap.ttsVoices.data as any[])?.length ? bootstrap.ttsVoices.data : FALLBACK_TTS_VOICES) as any[];
  const ttsModels = ((bootstrap.ttsModels.data as any[])?.length ? bootstrap.ttsModels.data : FALLBACK_TTS_MODELS) as any[];
  const [presetDraft, setPresetDraft] = useState<typeof EMPTY_PRESET & { id?: string }>(EMPTY_PRESET);
  const [chatCommandDraft, setChatCommandDraft] = useState<any>(normalizeChatCommandDraft());
  const [presetQuery, setPresetQuery] = useState('');
  const [presetPage, setPresetPage] = useState(1);
  const conversations = (bootstrap.conversations.data || EMPTY_LIST) as any[];
  const [draft, setDraft] = useState<any>(EMPTY_CHARACTER);
  const [conversationTitle, setConversationTitle] = useState('');
  const [conversationThumbnailUrl, setConversationThumbnailUrl] = useState('');
  const [userDescription, setUserDescription] = useState('');
  const [roomEditDraft, setRoomEditDraft] = useState<any>({ title: '', world_setting_id: '', user_description: '', participants: [] });
  const [characterAId, setCharacterAId] = useState('');
  const [characterBId, setCharacterBId] = useState('');
  const [multiCharacterIds, setMultiCharacterIds] = useState<string[]>(['']);
  const [multiCharacterRoles, setMultiCharacterRoles] = useState<string[]>(['primary']);
  const [scene, setScene] = useState<any>({ location: '작업방', mood: '개발 테스트', seed: '오늘의 개발 방향을 짧게 정리합니다.', opening_scene: '', opening_line: '', tone_preset: '', relationship_archetype: '', compression_focus: '', tts_enabled: false });
  const renderChatImages = useUiStore((state) => state.renderChatImages);
  const setRenderChatImages = useUiStore((state) => state.setRenderChatImages);
  const [status, setStatus] = useState('');
  const dismissStatus = useCallback((expected: string) => setStatus(current => current === expected ? '' : current), []);
  const [busy, setBusy] = useState(false);
  const [resourceReloadKey, setResourceReloadKey] = useState(0);
  const routeWork = useRef(new RoomWork()).current;
  const guardUI = useOperationOwnership(routeWork, setBusy, setStatus);
  useLayoutEffect(() => {
    const visit = routeWork.enter(`${route.page}:${'id' in route ? route.id : ''}`);
    setBusy(false);
    setStatus(current => isCompletionFeedback(current, t) ? current : '');
    return () => visit.cancel();
  }, [route.page, 'id' in route ? route.id : '']);
  // Guard every completion, including setters passed an updater and late finally.
  // Independent operations use independent epochs; only the busy owner clears busy.
  function ownUI(operation: string) {
    const setters = { navigate, setChatCommandDraft, setConversation, setConversationThumbnailUrl, setConversationTitle, setDraft, setHasOlderMessages, setMessages, setOlderPagingReady, setParticipants, setRoomEditDraft, setSelectedWorldSettingId, setUserDescription, setWorldSettingDraft };
    return guardUI(operation, setters);
  }
  useEffect(() => {
    if (route.page === 'worldSettingNew') setWorldSettingDraft(createEmptyWorld());
  }, [route.page, 'id' in route ? route.id : '']);
  const nativeProposal = useNativeProposal(route, navigate, routeWork);
  const characterReview = nativeProposal.active && nativeProposal.kind === 'character';
  const worldReview = nativeProposal.active && nativeProposal.kind === 'world';
  const editorCharacterDraft = characterReview ? nativeProposal.form || {} : draft;
  const editorWorldDraft = worldReview ? nativeProposal.form || {} : worldSettingDraft;
  const { characterAssets, setCharacterAssets, characterAssetsLoading, assetDraft, setAssetDraft,
    loadCharacterAssets, createCharacterAsset, uploadCharacterAsset, patchCharacterAsset, deleteCharacterAsset, setDefaultCharacterAsset } = useAssetEditor({
    routeId, routePage: route.page, editorCharacterId: editorCharacterDraft.id, ordinaryCharacterId: draft.id, routeWork, guardUI, confirmLocalizedDanger,
    proposal: characterReview && nativeProposal.review ? {
      token: nativeProposal.review.token, characterId: nativeProposal.form?.id,
      isCurrent: token => useAgentStore.getState().proposalRequest?.token === token,
      chooseAvatar: url => nativeProposal.setForm(current => ({ ...current, avatar_url: url })),
    } : undefined,
  });
  const characterById = useMemo(() => new Map(characters.map((c) => [c.id, c])), [characters]);
  const participantName = (id) => id === SYSTEM_ID ? t('시스템') : id === 'storyteller' ? t('스토리텔링') : id === USER_ID ? t('나') : characterById.get(id)?.name || `${t('삭제된 캐릭터')} · ${id.slice(-4)}`;
  const {
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
    deleteMessageBubble, regenerateFromMessage } = useConversationSession({
    route, selectedConversationId, routeWork, guardUI, busy, status, setBusy, setStatus,
    contextDrawerOpen, setContextDrawerOpen, participantName, confirmLocalizedDanger, confirmDanger,
  });
  const { runtimeDefaultSetting, setRuntimeDefaultSetting, saveRuntimeDefaultSetting } = useRuntimeDefaultSetting({ settingsMutations: settingsMutations as any, setBusy, setStatus, ownUI });
  const chatPanel = useConversationPanelLayout(selectedConversationId);
  const [chatPanelTab, setChatPanelTab] = useState<DrawerTab>('manage');
  useEffect(() => { if (agentOpen) chatPanel.setOpen(false); }, [agentOpen, selectedConversationId]);
  const chatPanelReady = route.page === 'conversationDetail' && !!selectedConversationId && conversation?.id === selectedConversationId;
  useEffect(() => { setChatPanelTab('manage'); }, [selectedConversationId]);
  useEffect(() => { setContextDrawerOpen(chatPanelReady && chatPanel.open); }, [chatPanelReady, chatPanel.open, setContextDrawerOpen]);
  useEffect(() => {
    if (!chatPanelReady || !chatPanel.open) return;
    const visit = roomWork.capture();
    let active = true;
    void Promise.all([loadConversationContext(selectedConversationId), loadBattleState(selectedConversationId)])
      .catch(error => { if (active && visit.current) setStatus(String(error?.message || error)); });
    return () => { active = false; };
  }, [chatPanelReady, chatPanel.open, selectedConversationId]);
  const defaultCastRoleForSlot = (index) => CAST_ROLE_PRESETS.filter((preset) => preset.key)[index]?.key || 'support';

  async function refetchRuntimeDefaultSetting() {
    const result = await bootstrap.runtimeDefault.refetch();
    if (result.data) setRuntimeDefaultSetting(normalizeRuntimeSetting(result.data as any));
    return result.data;
  }

  async function loadCharacters() { await bootstrap.queryClient.invalidateQueries({ queryKey: queryKeys.characters }); return bootstrap.queryClient.getQueryData<any[]>(queryKeys.characters) || []; }
  async function loadConversations() { await bootstrap.queryClient.invalidateQueries({ queryKey: queryKeys.conversations }); return bootstrap.queryClient.getQueryData<any[]>(queryKeys.conversations) || []; }

  async function uploadConversationThumbnail(file) {
    const { setBusy, setStatus, setConversationThumbnailUrl } = ownUI('uploadConversationThumbnail');
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
    const visit = routeWork.capture();
    const reviewToken = worldReview ? nativeProposal.review?.token : null;
    const { setBusy, setStatus, setWorldSettingDraft } = ownUI('uploadWorldThumbnail');
    if (!file) return;
    setBusy(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const result = await settingsApi.uploadWorldThumbnail(formData) as any;
      if (!visit.current) return;
      if (reviewToken) {
        if (useAgentStore.getState().proposalRequest?.token !== reviewToken) return;
        nativeProposal.setForm(current => ({ ...current, thumbnail_url: result.thumbnail_url || '' }));
      } else setWorldSettingDraft((current) => ({ ...normalizeWorld(current), thumbnail_url: result.thumbnail_url || '' }));
      setStatus(t('세계관 썸네일 업로드 완료'));
    } catch (error) {
      setStatus(`${t('세계관 썸네일 업로드 실패 ·')} ${error.message}`);
    } finally {
      setBusy(false);
    }
  }
  async function loadPresets() { const result = await bootstrap.presets.refetch(); if (result.error) throw result.error; return (result.data || []) as any[]; }
  async function loadChatCommands() { const result = await bootstrap.chatCommands.refetch(); if (result.error) throw result.error; return (result.data || []) as any[]; }
  async function loadWorldSettings() { const result = await bootstrap.worldSettings.refetch(); if (result.error) throw result.error; return (result.data || []) as any[]; }
  async function loadTtsVoices() { const result = await bootstrap.ttsVoices.refetch(); if (result.error) throw result.error; return (result.data || []) as any[]; }
  async function loadTtsModels() { const result = await bootstrap.ttsModels.refetch(); if (result.error) throw result.error; return (result.data || []) as any[]; }

  function openConversationContextDrawer() {
    useAgentStore.getState().setOpen(false);
    if (!chatPanel.open) setChatPanelTab('manage');
    chatPanel.setOpen(!chatPanel.open);
  }
  function openConversationPanelSettings() {
    useAgentStore.getState().setOpen(false);
    setChatSettingsOpen(false);
    setChatPanelTab('settings');
    chatPanel.setOpen(true);
  }

  async function openConversation(conversationId) { navigate(`/conversations/${conversationId}`); }

  async function loadConversationEditDraft(conversationId = routeId) {
    const { setBusy, setStatus, setRoomEditDraft } = ownUI('loadConversationEditDraft');
    setRoomEditDraft({ title: '', world_setting_id: '', participants: [] });
    setStatus(t('불러오는 중…'));
    if (!conversationId) return;
    setBusy(true);
    try {
      const [room, context, roomParticipants] = await Promise.all([conversationApi.get(conversationId), conversationApi.context(conversationId), conversationApi.participants(conversationId)]) as [any, any, any[]];
      if (!room || room.id !== conversationId || !Array.isArray(roomParticipants)) throw new Error('Invalid room edit snapshot');
      setRoomEditDraft(sceneDraftFromContext(room, context, roomParticipants));
      setStatus(current => current === t('불러오는 중…') ? '' : current);
    } catch (error) { setStatus(`요청 실패 · ${error.message}`); } finally { setBusy(false); }
  }
  async function saveConversationRoomEdit() {
    const { setBusy, setStatus, setConversation, navigate } = ownUI('saveConversationRoomEdit');
    if (roomEditDraft.id !== routeId) { setStatus('Draft identity does not match save target. Reload this form before saving.'); return; }
    if (!routeId || !roomEditDraft.title.trim()) { setStatus(t('대화방 제목은 필수입니다.')); return; }

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
        world_setting_id: roomEditDraft.world_setting_id || null,
        genre_mode: normalizeGenreMode(worldSettings.find((world) => world.id === roomEditDraft.world_setting_id)?.genre_mode || roomEditDraft.genre_mode),
        participants: normalizedParticipants,
        scene: { user_description: roomEditDraft.user_description || '' },
      } }) as any;
      setConversation(updated);
      setStatus(t('대화방 수정 완료'));
      navigate('/conversations');
    } catch (error) { setStatus(`요청 실패 · ${error.message}`); } finally { setBusy(false); }
  }

  async function saveNativeProposal() {
    const visit = routeWork.capture();
    const token = nativeProposal.review?.token;
    const { setBusy, setStatus, setDraft, setWorldSettingDraft, navigate } = ownUI('saveNativeProposal');
    const owns = () => visit.current && useAgentStore.getState().proposalRequest?.token === token;
    setBusy(true);
    try {
      const committed = await nativeProposal.save();
      const id = committed.result!.id as string;
      await bootstrap.queryClient.invalidateQueries({ queryKey: committed.kind === 'character' ? queryKeys.characters : queryKeys.worldSettingsRoot });
      if (committed.kind === 'character') {
        const saved = await characterApi.get(id) as any;
        if (saved.id !== id) throw new Error('Saved character identity could not be verified.');
        if (!owns()) return;
        setDraft(saved); setStatus(t('캐릭터 저장 완료'));
      } else {
        const worlds = bootstrap.queryClient.getQueryData<any[]>(queryKeys.worldSettings) || [];
        const saved = worlds.find(world => world.id === id);
        if (!saved) throw new Error('Saved world identity could not be verified.');
        if (!owns()) return;
        setWorldSettingDraft(saved); setStatus(t('세계관 저장 완료'));
      }
      if (!owns()) return;
      agentActions.closeProposal(token!);
      navigate(`/${committed.kind === 'character' ? 'characters' : 'world-settings'}/${encodeURIComponent(id)}`);
    } catch (error) { if (owns()) setStatus(`요청 실패 · ${error.message}`); }
    finally { setBusy(false); }
  }
  async function saveCharacter() {
    const draft = editorCharacterDraft;
    const { setBusy, setStatus, navigate } = ownUI('saveCharacter');
    if (route.page !== 'characterNew' && draft.id !== routeId) { setStatus('Draft identity does not match save target. Reload this form before saving.'); return; }
    if (!draft.name?.trim() || !draft.persona?.trim()) { setStatus(t('이름과 페르소나는 필수입니다.')); return; }
    if (characterReview) { await saveNativeProposal(); return; }
    setBusy(true);
    try {
      const isNew = route.page === 'characterNew';
      const saved = await characterMutations.saveCharacter.mutateAsync({ id: isNew ? null : routeId, payload: normalizeCharacterForApi(draft) }) as any;
      setStatus(t('캐릭터 저장 완료')); navigate(`/characters/${saved.id}`);
    } catch (error) { setStatus(`요청 실패 · ${error.message}`); } finally { setBusy(false); }
  }
  async function deleteCharacter() {
    const actionVisit = routeWork.capture();
    const { setStatus, navigate } = ownUI('deleteCharacter');
    try {
    if (!routeId) return;
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('character', locale, draft.name || routeId)))) return;
    if (!actionVisit.current) return;
    await characterMutations.deleteCharacter.mutateAsync(routeId);
    navigate('/characters');
  } catch (error) { setStatus(`요청 실패 · ${error.message}`); }
  }
  async function playCharacterTtsSample(characterDraft = draft) {
    const operationVisit = routeWork.capture();
    const { setBusy, setStatus } = ownUI('playCharacterTtsSample');
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
      if (!operationVisit.current) return;
      const audio = new Audio(assetUrlFor(result.audio_url));
      await audio.play();
      setStatus(t('TTS 샘플 재생'));
    } catch (error) {
      setStatus(`${t('TTS 샘플 실패 ·')} ${error.message}`);
    } finally { setBusy(false); }
  }

  async function uploadAvatar(file, updateDraft) {
    const reviewToken = characterReview ? nativeProposal.review?.token : null;
    const operationVisit = routeWork.capture();
    const { setBusy, setStatus } = ownUI('uploadAvatar');
    if (!file) return;
    setBusy(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const uploaded = await characterMutations.uploadAvatar.mutateAsync(formData) as any;
      if (!operationVisit.current) return;
      if (reviewToken && useAgentStore.getState().proposalRequest?.token !== reviewToken) return;
      updateDraft('avatar_url', uploaded.avatar_url);
      setStatus(t('아바타 업로드 완료. 저장을 눌러 캐릭터에 반영하세요.'));
    } catch (error) {
      setStatus(`${t('아바타 업로드 실패 ·')} ${error.message}`);
    } finally {
      setBusy(false);
    }
  }
  async function savePreset() {
    const { setBusy, setStatus, navigate } = ownUI('savePreset');
    if (route.page !== 'presetNew' && presetDraft.id !== routeId) { setStatus('Draft identity does not match save target. Reload this form before saving.'); return; }
    if (!presetDraft.title.trim() || !presetDraft.content.trim()) { setStatus(t('프리셋 제목과 내용은 필수입니다.')); return; }
    setBusy(true);
    try {
      const isNew = route.page === 'presetNew';
      const body = { preset_type: presetDraft.preset_type || 'room_seed', title: presetDraft.title.trim(), content: presetDraft.content.trim(), description: presetDraft.description?.trim() || null, enabled: presetDraft.enabled !== false };
      const saved = await settingsMutations.savePreset.mutateAsync({ id: isNew ? null : routeId, payload: body }) as any;
      setStatus(t('프리셋 저장 완료')); navigate(`/presets/${saved.id}`);
    } catch (error) { setStatus(`요청 실패 · ${error.message}`); } finally { setBusy(false); }
  }
  async function deletePreset(id = routeId) {
    const actionVisit = routeWork.capture();
    const { setStatus, navigate } = ownUI('deletePreset');
    try { if (!(await confirmLocalizedDanger(formatDangerConfirmation('preset', locale)))) return;
    if (!actionVisit.current) return; await settingsMutations.deletePreset.mutateAsync(id); navigate('/presets'); } catch (error) { setStatus(`요청 실패 · ${error.message}`); }
  }
  async function saveChatCommand() {
    const { setBusy, setStatus, setChatCommandDraft, navigate } = ownUI('saveChatCommand');
    if (route.page !== 'chatCommandNew' && chatCommandDraft.id !== routeId) { setStatus('Draft identity does not match save target. Reload this form before saving.'); return; }
    const generationPrompt = chatCommandDraft.generation_prompt || '';
    const postprocessPrompt = chatCommandDraft.postprocess_prompt || chatCommandDraft.prompt || '';
    if (!chatCommandDraft.name?.trim() || !(generationPrompt.trim() || postprocessPrompt.trim())) { setStatus(t('커맨드명과 생성/후처리 프롬프트 중 하나는 필수입니다.')); return; }
    setBusy(true);
    try {
      const body = chatCommandSavePayload(chatCommandDraft);
      const saved = await settingsMutations.saveChatCommand.mutateAsync({ id: chatCommandDraft.id || null, payload: body }) as any;
      setChatCommandDraft(normalizeChatCommandDraft(saved)); setStatus(t('커맨드 저장 완료')); navigate(`/chat-commands/${saved.id}`);
    } catch (error) { setStatus(`요청 실패 · ${error.message}`); } finally { setBusy(false); }
  }
  async function deleteChatCommand(command) {
    const actionVisit = routeWork.capture();
    const { setStatus, setChatCommandDraft, navigate } = ownUI('deleteChatCommand');
    try {
    if (!command?.id) return;
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('command', locale, command.display_name || command.name)))) return;
    if (!actionVisit.current) return;
    await settingsMutations.deleteChatCommand.mutateAsync(command.id);
    setChatCommandDraft(normalizeChatCommandDraft()); setStatus(t('커맨드 삭제 완료')); navigate('/chat-commands');
  } catch (error) { setStatus(`요청 실패 · ${error.message}`); }
  }
  async function saveWorldSetting() {
    const worldSettingDraft = editorWorldDraft;
    const { setBusy, setStatus, setWorldSettingDraft, navigate } = ownUI('saveWorldSetting');
    if (route.page !== 'worldSettingNew' && worldSettingDraft.id !== routeId) { setStatus('Draft identity does not match save target. Reload this form before saving.'); return; }
    if (!worldSettingDraft.title?.trim() || !worldSettingDraft.world_seed?.trim()) { setStatus(t('세계관 이름과 세계관/규칙은 필수입니다.')); return; }
    if (worldReview) { await saveNativeProposal(); return; }
    setBusy(true);
    try {
      const payload = { ...worldSettingDraft, title: worldSettingDraft.title.trim(), thumbnail_url: worldSettingDraft.thumbnail_url || null, description: worldSettingDraft.description || null, genre_mode: normalizeGenreMode(worldSettingDraft.genre_mode), tags: Array.isArray(worldSettingDraft.tags) ? worldSettingDraft.tags : [], enabled: worldSettingDraft.enabled !== false };
      const saved = await settingsMutations.saveWorldSetting.mutateAsync({ id: worldSettingDraft.id || null, payload }) as any;
      setWorldSettingDraft(saved); setStatus(t('세계관 저장 완료')); navigate(`/world-settings/${saved.id}`);
    } catch (error) { setStatus(`요청 실패 · ${error.message}`); } finally { setBusy(false); }
  }
  async function deleteWorldSetting(world) {
    const actionVisit = routeWork.capture();
    const { setStatus, setWorldSettingDraft } = ownUI('deleteWorldSetting');
    try {
    if (!world?.id) return;
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('world', locale, world.title || t('세계관'))))) return;
    if (!actionVisit.current) return;
    await settingsMutations.deleteWorldSetting.mutateAsync(world.id);
    setWorldSettingDraft(createEmptyWorld()); setStatus(t('세계관 삭제 완료'));
  } catch (error) { setStatus(`요청 실패 · ${error.message}`); }
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
      id: room?.id,
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
    const actionVisit = routeWork.capture();
    const { setBusy, setStatus, navigate } = ownUI('deleteConversation');
    event?.stopPropagation();
    if (!conversationId || busy) return;
    const room = conversations.find((item) => item.id === conversationId);
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('conversation', locale, room?.title || conversationId)))) return;
    if (!actionVisit.current) return;
    setBusy(true);
    try {
      await conversationMutations.deleteConversation.mutateAsync(conversationId);
      if (selectedConversationId === conversationId) navigate('/conversations');
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

  async function startConversationRoom() {
    const { setBusy, setStatus, setConversation, setConversationThumbnailUrl, setMessages, setConversationTitle, setSelectedWorldSettingId, setHasOlderMessages, setOlderPagingReady, setParticipants, setUserDescription, navigate } = ownUI('startConversationRoom');
    setBusy(true);
    try {
      const selectedCharacters = multiCharacterIds
        .map((id, index) => ({ id, index }))
        .filter((slot) => !!slot.id)
        .map((slot) => ({ character: requireCharacter(slot.id), role: multiCharacterRoles[slot.index] || null }));
      const uniqueIds = new Set(selectedCharacters.map(({ character }) => character.id));
      if (selectedCharacters.length < 1) throw new Error(t('참여 캐릭터를 1명 이상 선택해야 합니다.'));
      if (uniqueIds.size !== selectedCharacters.length) throw new Error(t('서로 다른 캐릭터를 선택해야 합니다.'));

      const createdRoomMode = selectedCharacters.length === 1 ? 'user_character' : 'character_character';
      const participants = selectedCharacters.length === 1
        ? [{ type: 'user', id: USER_ID, order_index: 0 }, { type: 'character', id: selectedCharacters[0].character.id, role: selectedCharacters[0].role, order_index: 1 }]
        : selectedCharacters.map(({ character, role }, index) => ({ type: 'character', id: character.id, role, order_index: index }));
      const conversation = await conversationMutations.createConversation.mutateAsync({ mode: createdRoomMode, world_setting_id: selectedWorldSettingId || null, title: conversationTitle.trim() || null, thumbnail_url: null, participants, tts_enabled: !!scene.tts_enabled, scene: { user_description: userDescription.trim() || null } }) as any;
      setUserDescription(current => current === userDescription ? '' : current);
      setConversationTitle('');
      setConversationThumbnailUrl('');
      setSelectedWorldSettingId('');
      setConversation(conversation); setMessages([]); setHasOlderMessages(false); setOlderPagingReady(false); setParticipants(participants);
      navigate(`/conversations/${conversation.id}`);
      setStatus(t('대화방 생성 완료'));
    } catch (error) { setStatus(error.message); }
    finally { setBusy(false); }
  }

  useBootstrapSync({
    bootstrap,
    setBackendOnline,
    setCharacterAId,
    setCharacterBId,
    setMultiCharacterIds,
    setRuntimeDefaultSetting,
    setConversationRuntimeSetting,
    setStatus,
  });
  useRouteResourceSync({
    route,
    reloadKey: resourceReloadKey,
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

  const resourceDraftMismatch = ((route.page === 'characterDetail' || route.page === 'characterAssets') && editorCharacterDraft.id !== routeId) || (route.page === 'presetDetail' && presetDraft.id !== routeId) || (route.page === 'chatCommandDetail' && chatCommandDraft.id !== routeId) || (route.page === 'conversationEdit' && roomEditDraft.id !== routeId);
  const roomFailureStatus = !!roomLoadError && !isCompletionFeedback(status, t) && (status.includes(roomLoadError) || status.includes(t('캐릭터 초대 완료')));
  const chatGenerationStatus = isChatGenerationProgress(status, t) ? status : '';
  const chatFeedback = <>
    <OperationFeedback status={chatGenerationStatus ? '' : status} onDismiss={dismissStatus} showPersistent={!roomFailureStatus} />
    {roomLoadError && <ResourceDetailFeedback variant="chat" error={roomFailureStatus ? status : roomLoadError} onRetry={() => void retryConversationRefresh()}>{unknownSubmissionsForRoom(selectedConversationId).length > 0 && !generationOwners.pending.has(selectedConversationId) && <Button onClick={() => void stopTrackingUnknownSubmission()}>Stop tracking unresolved submission</Button>}</ResourceDetailFeedback>}
  </>;
  const conversationSidePanel = <ConversationInfoDrawer
    open={chatPanelReady && chatPanel.open && !agentOpen} docked={chatPanel.docked} activeTab={chatPanelTab} onTabChange={setChatPanelTab} onClose={() => chatPanel.setOpen(false)}
    feedbackContent={chatFeedback} context={conversationContext} loading={contextLoading} conversation={conversation} participants={participants} characters={characters} participantName={participantName}
    onRenameConversation={renameConversation} onInviteCharacter={inviteCharacterToConversation} onRemoveCharacter={removeCharacterFromConversation} onUpdateCharacterRole={updateCharacterRoleInConversation}
    battleState={battleState} onUpdateSceneSummary={updateSceneSummary} userMemoryDraft={userMemoryDraft} onUserMemoryDraftChange={setUserMemoryDraft}
    onCreateUserMemory={createUserMemory} onUpdateMemory={updateConversationMemory} onDeleteMemory={deleteConversationMemory} busy={busy}
    settingsContent={<ChatSettingsBubble embedded open={chatPanel.open} setting={conversationRuntimeSetting} inheritedThinkingLevel={runtimeDefaultSetting.effective_thinking_level}
      onChange={setConversationRuntimeSetting} onSave={saveConversationRuntimeSetting} saving={busy} onClose={() => chatPanel.setOpen(false)}
      renderImages={renderChatImages} onRenderImagesChange={setRenderChatImages} ttsEnabled={!!conversation?.tts_enabled}
      onTtsEnabledChange={enabled => setConversation(current => ({ ...(current || {}), tts_enabled: enabled }))} onCompressNow={compressConversationNow} />}
  />;
  const appOverlays = <>
    <AvatarPreviewModal preview={avatarPreview as any} onClose={() => setAvatarPreview(null)} />
    <CharacterCardPreviewModal character={cardPreview as any} onClose={() => setCardPreview(null)} />
  </>;

  const selectedMultiCharacterIds = multiCharacterIds.filter(Boolean);
  const canStartConversation = selectedMultiCharacterIds.length >= 1 && new Set(selectedMultiCharacterIds).size === selectedMultiCharacterIds.length;
  const headerActions = (() => {
    if (route.page === 'modelSettings' || route.page === 'agentSettings') return <Button type="button" variant="ghost" onClick={() => navigate('/settings')}>{t('설정')}</Button>;
    if (route.page === 'home') return <Button type="button" onClick={() => navigate('/conversations/new')}>{t('새 대화방')}</Button>;
    if (route.page === 'conversations') return <Button type="button" onClick={() => navigate('/conversations/new')}>{t('새 대화방')}</Button>;
    if (route.page === 'conversationNew') return <Button type="button" disabled={busy || !canStartConversation} onClick={startConversationRoom}>{t(busy ? '생성 중…' : '대화 시작')}</Button>;
    if (route.page === 'conversationEdit') return null;
    if (route.page === 'characters') return <Button type="button" onClick={() => navigate('/characters/new')}>{t('새 캐릭터')}</Button>;
    if (route.page === 'characterDetail' || route.page === 'characterNew') return <><Button type="button" variant="ghost" onClick={() => navigate('/characters')}>{t('목록으로')}</Button><Button type="button" variant="ghost" onClick={() => setCardPreview(editorCharacterDraft)}>{t('카드 미리보기')}</Button></>;
    if (route.page === 'characterAssets') return <Button type="button" variant="ghost" onClick={() => navigate(`/characters/${route.id}`)}>{t('캐릭터 상세로')}</Button>;
    if (route.page === 'worldSettings') return <Button type="button" onClick={() => navigate('/world-settings/new')}>{t('새 세계관')}</Button>;
    if (route.page === 'worldSettingNew' || route.page === 'worldSettingDetail') return <Button type="button" variant="ghost" onClick={() => navigate('/world-settings')}>{t('목록으로')}</Button>;
    if (route.page === 'presets') return <Button type="button" onClick={() => navigate('/presets/new')}>{t('프리셋 추가')}</Button>;
    if (route.page === 'presetNew' || route.page === 'presetDetail') return <Button type="button" variant="ghost" onClick={() => navigate('/presets')}>{t('목록으로')}</Button>;
    if (route.page === 'chatCommands') return <Button type="button" onClick={() => navigate('/chat-commands/new')}>{t('새 커맨드')}</Button>;
    if (route.page === 'chatCommandNew' || route.page === 'chatCommandDetail') return <Button type="button" variant="ghost" onClick={() => navigate('/chat-commands')}>{t('목록')}</Button>;
    return null;
  })();

  return <AppShell route={route} chatHeaderReady={route.page !== 'conversationDetail' || conversation?.id === selectedConversationId} backendOnline={backendOnline} drawerOpen={drawerOpen} setDrawerOpen={setDrawerOpen} navigate={navigate} overlays={appOverlays} headerActions={headerActions} secondaryHeaderActions={['modelSettings', 'agentSettings', 'characterDetail', 'characterNew', 'characterAssets', 'worldSettingNew', 'worldSettingDetail', 'presetNew', 'presetDetail', 'chatCommandNew', 'chatCommandDetail'].includes(route.page)}>
    {nativeProposal.active && !nativeProposal.ready && <section role={nativeProposal.error ? 'alert' : 'status'}><p>{nativeProposal.error || t('불러오는 중…')}</p><Button variant="ghost" onClick={nativeProposal.cancel}>{t('취소')}</Button></section>}
    {resourceDraftMismatch && !nativeProposal.active && <ResourceDetailFeedback error={status && status !== t('불러오는 중…') && !isCompletionFeedback(status, t) ? status : ''} onRetry={() => setResourceReloadKey(key => key + 1)}><Button onClick={() => navigate('/' + (route.page.startsWith('character') ? 'characters' : route.page.startsWith('preset') ? 'presets' : route.page.startsWith('chatCommand') ? 'chat-commands' : 'conversations'))}>{t('목록으로')}</Button></ResourceDetailFeedback>}
    {route.page !== 'conversationDetail' && <OperationFeedback status={status} onDismiss={dismissStatus} showPersistent={!resourceDraftMismatch} />}
    {route.page === 'home' && <ResourceListBoundary variant="home"><HomeView conversations={conversations} characters={characterUsageRanking.length ? characterUsageRanking : characters} allCharacters={characters} worldSettings={worldSettings} navigate={navigate} openConversation={openConversation} /></ResourceListBoundary>}
    {route.page === 'conversationNew' && <ConversationCreateView busy={busy} navigate={navigate} status={status} startConversationRoom={startConversationRoom} conversationTitle={conversationTitle} setConversationTitle={setConversationTitle} selectedWorldSettingId={selectedWorldSettingId} setSelectedWorldSettingId={setSelectedWorldSettingId} worldSettings={worldSettings} scene={scene} setScene={setScene} userDescription={userDescription} setUserDescription={setUserDescription} applyPreset={applyPreset} multiCharacterIds={multiCharacterIds} multiCharacterRoles={multiCharacterRoles} updateMultiCharacterSlot={updateMultiCharacterSlot} updateMultiCharacterRole={updateMultiCharacterRole} removeMultiCharacterSlot={removeMultiCharacterSlot} addMultiCharacterSlot={addMultiCharacterSlot} selectOptions={selectOptions} />}

    {route.page === 'characters' && <ResourceListBoundary><CharactersListView characters={characters} navigate={navigate} /></ResourceListBoundary>}
    {(route.page === 'characterDetail' || route.page === 'characterNew') && !resourceDraftMismatch && (!nativeProposal.active || nativeProposal.ready) && <PageActionDisabled.Provider value={characterReview && (busy || nativeProposal.blocked)}><fieldset className="contents" disabled={characterReview && (busy || nativeProposal.blocked)}><CharacterDetailView reviewMode={characterReview} changedFields={characterReview ? nativeProposal.changedFields : []} onCancelReview={nativeProposal.cancel} route={route} draft={editorCharacterDraft} setDraft={characterReview ? nativeProposal.setForm : setDraft} ttsVoices={ttsVoices} ttsModels={ttsModels} busy={busy} navigate={navigate} setCardPreview={setCardPreview} saveCharacter={saveCharacter} deleteCharacter={deleteCharacter} uploadAvatar={uploadAvatar} playCharacterTtsSample={playCharacterTtsSample} speechPresets={speechPresets} applyPreset={characterReview ? (field, content) => nativeProposal.setForm(current => ({ ...current, [field]: [current[field], content].filter(Boolean).join('\n') })) : applyPreset} characterAssets={characterAssets} characterAssetsLoading={characterAssetsLoading} assetDraft={assetDraft} setAssetDraft={setAssetDraft} createCharacterAsset={createCharacterAsset} uploadCharacterAsset={uploadCharacterAsset} patchCharacterAsset={patchCharacterAsset} deleteCharacterAsset={deleteCharacterAsset} setDefaultCharacterAsset={setDefaultCharacterAsset} /></fieldset></PageActionDisabled.Provider>}
    {route.page === 'characterAssets' && !resourceDraftMismatch && <CharacterAssetsView route={route} draft={draft} characterAssets={characterAssets} characterAssetsLoading={characterAssetsLoading} busy={busy} assetDraft={assetDraft} setAssetDraft={setAssetDraft} navigate={navigate} createCharacterAsset={createCharacterAsset} uploadCharacterAsset={uploadCharacterAsset} patchCharacterAsset={patchCharacterAsset} deleteCharacterAsset={deleteCharacterAsset} setDefaultCharacterAsset={setDefaultCharacterAsset} assetUrlFor={assetUrlFor} />}

    {route.page === 'presets' && <ResourceListBoundary><PresetsView navigate={navigate} presetQuery={presetQuery} setPresetQuery={setPresetQuery} setPresetPage={setPresetPage} loadPresets={loadPresets} visiblePresets={visiblePresets} presetPage={presetPage} pageCount={pageCount} filteredPresets={filteredPresets} /></ResourceListBoundary>}
    {(route.page === 'presetNew' || route.page === 'presetDetail') && !resourceDraftMismatch && <PresetDetailView route={route} presetDraft={presetDraft} setPresetDraft={setPresetDraft} characters={characters} savePreset={savePreset} deletePreset={deletePreset} busy={busy} navigate={navigate} />}

    {route.page === 'chatCommands' && <ResourceListBoundary><ChatCommandsView route={route} navigate={navigate} chatCommands={chatCommands} chatCommandDraft={chatCommandDraft} setChatCommandDraft={setChatCommandDraft} saveChatCommand={saveChatCommand} deleteChatCommand={deleteChatCommand} loadChatCommands={loadChatCommands} busy={busy} /></ResourceListBoundary>}
    {(route.page === 'chatCommandNew' || route.page === 'chatCommandDetail') && !resourceDraftMismatch && <ChatCommandsView route={route} navigate={navigate} chatCommands={chatCommands} chatCommandDraft={chatCommandDraft} setChatCommandDraft={setChatCommandDraft} saveChatCommand={saveChatCommand} deleteChatCommand={deleteChatCommand} loadChatCommands={loadChatCommands} busy={busy} />}
    {route.page === 'worldSettings' && <ResourceListBoundary><WorldSettingsListView worldSettings={worldSettings} navigate={navigate} loadWorldSettings={loadWorldSettings} /></ResourceListBoundary>}
    {(route.page === 'worldSettingNew' || route.page === 'worldSettingDetail') && (!nativeProposal.active || nativeProposal.ready) && <PageActionDisabled.Provider value={worldReview && (busy || nativeProposal.blocked)}><fieldset className="contents" disabled={worldReview && (busy || nativeProposal.blocked)}><WorldSettingsDetailView reviewMode={worldReview} changedFields={worldReview ? nativeProposal.changedFields : []} onCancelReview={nativeProposal.cancel} route={route} worldSettings={worldSettings} loading={bootstrap.worldSettings.isPending} loadError={bootstrap.worldSettings.error?.message} onRetry={() => { void loadWorldSettings().catch(error => setStatus(error.message)); }} worldSettingDraft={editorWorldDraft as any} setWorldSettingDraft={worldReview ? nativeProposal.setForm : setWorldSettingDraft} saveWorldSetting={saveWorldSetting} deleteWorldSetting={deleteWorldSetting} uploadWorldThumbnail={uploadWorldThumbnail} busy={busy} navigate={navigate} /></fieldset></PageActionDisabled.Provider>}

    {route.page === 'settings' && <SettingsView navigate={navigate} />}
    {route.page === 'agentSettings' && <AgentSettingsView />}
    {route.page === 'modelSettings' && <ModelSettingsView section={route.section} runtimeDefaultSetting={runtimeDefaultSetting} setRuntimeDefaultSetting={setRuntimeDefaultSetting} saveRuntimeDefaultSetting={saveRuntimeDefaultSetting} refetchRuntimeDefaultSetting={refetchRuntimeDefaultSetting} busy={busy} />}
    {route.page === 'statistics' && <StatisticsView conversations={conversations} />}

    {route.page === 'conversations' && <ResourceListBoundary variant="media"><ConversationsListView conversations={conversations} busy={busy} navigate={navigate} openConversation={openConversation} deleteConversation={deleteConversation} loadConversations={loadConversations} allCharacters={characters} worldSettings={worldSettings} /></ResourceListBoundary>}
    {route.page === 'conversationEdit' && !resourceDraftMismatch && <ConversationEditView busy={busy} navigate={navigate} saveConversationRoomEdit={saveConversationRoomEdit} roomEditDraft={roomEditDraft} setRoomEditDraft={setRoomEditDraft} applyRoomEditPreset={applyRoomEditPreset} worldSettings={worldSettings} characters={characters} />}
    {route.page === 'conversationDetail' && (!conversation || conversation.id !== selectedConversationId) && <ResourceDetailFeedback variant="chat" error={roomLoadError} onRetry={() => void retryConversationRefresh()}>{unknownSubmissionsForRoom(selectedConversationId).length > 0 && !generationOwners.pending.has(selectedConversationId) && <Button onClick={() => void stopTrackingUnknownSubmission()}>Stop tracking unresolved submission</Button>}</ResourceDetailFeedback>}
    {route.page === 'conversationDetail' && conversation?.id === selectedConversationId && <ConversationDetailView sidePanel={conversationSidePanel} workspaceRef={chatPanel.workspaceRef} panelDocked={chatPanel.docked} panelOpen={chatPanel.open && !agentOpen} panelWidth={chatPanel.panelWidth} onOpenPanelSettings={openConversationPanelSettings} feedback={!chatPanel.open ? chatFeedback : null} conversation={conversation} selectedConversationId={selectedConversationId} status={chatGenerationStatus} navigate={navigate} openConversationContextDrawer={openConversationContextDrawer} messageSelectionMode={messageSelectionMode} selectedMessageIds={selectedMessageIds} clearMessageSelection={clearMessageSelection} busy={busy} deleteSelectedMessages={deleteSelectedMessages} messages={messages} characters={characters} participants={participants} setAvatarPreview={setAvatarPreview} chatEndRef={chatEndRef} chatThreadRef={chatThreadRef} loadOlderMessages={loadOlderMessages} hasOlderMessages={hasOlderMessages} loadingOlderMessages={loadingOlderMessages} olderPagingReady={olderPagingReady} retryMessage={retryMessage} renderChatImages={renderChatImages} generateMessageTts={generateMessageTts} regenerateFromMessage={regenerateFromMessage} deleteMessageBubble={deleteMessageBubble} updateMessageBubble={updateMessageBubble} enterMessageSelectionMode={enterMessageSelectionMode} toggleMessageSelection={toggleMessageSelection} setShowJumpToLatest={setShowJumpToLatest} showJumpToLatest={showJumpToLatest} scrollToLatest={scrollToLatest} sendConversationMessage={sendConversationMessage} speakerId={speakerId} setSpeakerId={setSpeakerId} speakerOptions={speakerOptions} participantName={participantName} setChatSettingsOpen={setChatSettingsOpen} chatSettingsOpen={chatSettingsOpen} conversationRuntimeSetting={conversationRuntimeSetting} inheritedThinkingLevel={runtimeDefaultSetting.effective_thinking_level} setConversationRuntimeSetting={setConversationRuntimeSetting} saveConversationRuntimeSetting={saveConversationRuntimeSetting} setRenderChatImages={setRenderChatImages} setConversation={setConversation} compressConversationNow={compressConversationNow} composerTextareaRef={composerTextareaRef} compose={compose} setCompose={setCompose} resizeComposerTextarea={resizeComposerTextarea} handleComposerKeyDown={handleComposerKeyDown} composeExpanded={composeExpanded} setComposeExpanded={setComposeExpanded} battleState={battleState} battleControlDraft={battleControlDraft} setBattleControlDraft={setBattleControlDraft} chatCommands={chatCommands} clearActiveCommand={clearActiveCommand} />}
  </AppShell>;
}
