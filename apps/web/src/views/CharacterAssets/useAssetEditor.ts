import { useQuery, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '../../api/queryKeys';
import type { CharacterAsset } from '../../components/organisms/CharacterAssetsPanel';
import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { characterApi } from '../../api/resources';
import { useCharacterMutations } from '../../hooks/useCharacterMutations';
import type { GuardUI } from '../../hooks/useOperationOwnership';
import type { RoomWork } from '../../utils/roomWork';
import { parseAssetTags } from '../../utils/assetTags';
import { useI18n } from '../../i18n/I18nProvider';
import { formatDangerConfirmation } from '../../i18n/core';

type AssetProposalBridge = {
  token: string; characterId?: string;
  isCurrent: (token: string) => boolean;
  chooseAvatar: (url: string) => void;
};
type AssetEditorOptions = {
  routeId: string;
  routePage: string;
  editorCharacterId?: string;
  ordinaryCharacterId?: string;
  routeWork: RoomWork;
  guardUI: GuardUI;
  proposal?: AssetProposalBridge;
  confirmLocalizedDanger: (message: string) => Promise<boolean>;
};

/** Mounted by App, not by the route View: drafts and visit fences survive navigation. */
export function useAssetEditor({ routeId, routePage, editorCharacterId, ordinaryCharacterId, routeWork, guardUI, proposal, confirmLocalizedDanger }: AssetEditorOptions) {
  const { locale, t } = useI18n();
  const characterMutations = useCharacterMutations();
  const characterReview = !!proposal;
  const routeIdRef = useRef(routeId); routeIdRef.current = routeId;
  const queryClient = useQueryClient();
  const assetsQuery = useQuery({
    queryKey: queryKeys.characterAssets(routeId),
    queryFn: () => characterApi.assets(routeId) as Promise<CharacterAsset[]>,
    enabled: false, // Route hydration explicitly loads after the identity fence.
  });
  // Detach the old query run too, not just its UI callbacks. Otherwise A→B→A
  // could reuse the first A request and let that cancelled visit hydrate new A.
  // Only GET observation is cancelled; mutations/server work are never aborted.
  useLayoutEffect(() => () => {
    void queryClient.cancelQueries({ queryKey: queryKeys.characterAssets(routeId), exact: true });
  }, [queryClient, routeId, routePage]);
  const characterAssets = assetsQuery.data || [];
  const setCharacterAssets = useCallback((assets: CharacterAsset[]) => {
    queryClient.setQueryData(queryKeys.characterAssets(routeId), assets);
  }, [queryClient, routeId]);
  const [characterAssetsLoading, setCharacterAssetsLoading] = useState(false);
  const [assetDraft, setAssetDraft] = useState({ label: '', description: '', image_url: '', tags: '', mood_tags: '', scene_tags: '', outfit_tags: '', pose_tags: '', expression_tags: '', priority: 50, enabled: true, is_default: false });
  function ownUI(operation: string) {
    return guardUI(operation, { setCharacterAssets, setCharacterAssetsLoading, setAssetDraft });
  }
  async function loadCharacterAssets(characterId = routeId) {
    const { setStatus, setCharacterAssets, setCharacterAssetsLoading } = ownUI('loadCharacterAssets');
    if (!characterId || routeIdRef.current !== characterId) return [];
    setCharacterAssets([]);
    setCharacterAssetsLoading(true);
    try {
      const list = await queryClient.fetchQuery({
        queryKey: queryKeys.characterAssets(characterId),
        queryFn: () => characterApi.assets(characterId) as Promise<CharacterAsset[]>,
        staleTime: 0,
      });
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
      tags: parseAssetTags(assetDraft.tags),
      mood_tags: parseAssetTags(assetDraft.mood_tags),
      scene_tags: parseAssetTags(assetDraft.scene_tags),
      outfit_tags: parseAssetTags(assetDraft.outfit_tags),
      pose_tags: parseAssetTags(assetDraft.pose_tags),
      expression_tags: parseAssetTags(assetDraft.expression_tags),
      priority: Number(assetDraft.priority || 50),
      enabled: assetDraft.enabled !== false,
      is_default: !!assetDraft.is_default,
    };
  }
  async function createCharacterAsset() {
    if (editorCharacterId !== routeId) return;
    const reviewToken = characterReview ? proposal?.token : null;
    const chooseDefault = !!assetDraft.is_default;
    const operationVisit = routeWork.capture();
    const { setBusy, setStatus, setAssetDraft } = ownUI('createCharacterAsset');
    if (!routeId || !assetDraft.label.trim() || !assetDraft.image_url.trim()) return;
    setBusy(true);
    try {
      const payload = normalizeAssetDraft();
      if (reviewToken) payload.is_default = false;
      const created = await characterMutations.createAsset.mutateAsync({ id: routeId, payload }) as { image_url?: string };
      if (operationVisit.current && reviewToken && chooseDefault && proposal?.isCurrent(reviewToken) && typeof created.image_url === 'string') {
        proposal?.chooseAvatar(created.image_url);
      }
      setAssetDraft({ label: '', description: '', image_url: '', tags: '', mood_tags: '', scene_tags: '', outfit_tags: '', pose_tags: '', expression_tags: '', priority: 50, enabled: true, is_default: false });
      if (!operationVisit.current) return;
      await loadCharacterAssets(routeId);
      setStatus(t('에셋 추가 완료'));
    } catch (error) { setStatus(`요청 실패 · ${error.message}`); } finally { setBusy(false); }
  }
  async function uploadCharacterAsset(file) {
    if (editorCharacterId !== routeId) return;
    const reviewToken = characterReview ? proposal?.token : null;
    const chooseDefault = !!assetDraft.is_default;
    const operationVisit = routeWork.capture();
    const { setBusy, setStatus } = ownUI('uploadCharacterAsset');
    if (!file || !routeId) return;
    setBusy(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('label', assetDraft.label || '이미지 에셋');
      formData.append('description', assetDraft.description || '');
      formData.append('tags', JSON.stringify(parseAssetTags(assetDraft.tags)));
      formData.append('mood_tags', JSON.stringify(parseAssetTags(assetDraft.mood_tags)));
      formData.append('scene_tags', JSON.stringify(parseAssetTags(assetDraft.scene_tags)));
      formData.append('outfit_tags', JSON.stringify(parseAssetTags(assetDraft.outfit_tags)));
      formData.append('pose_tags', JSON.stringify(parseAssetTags(assetDraft.pose_tags)));
      formData.append('expression_tags', JSON.stringify(parseAssetTags(assetDraft.expression_tags)));
      formData.append('priority', String(assetDraft.priority || 50));
      formData.append('enabled', String(assetDraft.enabled !== false));
      formData.append('is_default', String(!reviewToken && chooseDefault));
      const created = await characterMutations.uploadAsset.mutateAsync({ id: routeId, formData }) as { image_url?: string };
      if (operationVisit.current && reviewToken && chooseDefault && proposal?.isCurrent(reviewToken) && typeof created.image_url === 'string') {
        proposal?.chooseAvatar(created.image_url);
      }
      if (!operationVisit.current) return;
      await loadCharacterAssets(routeId);
      setStatus(t('에셋 업로드 완료'));
    } catch (error) { setStatus(`요청 실패 · ${error.message}`); } finally { setBusy(false); }
  }
  async function patchCharacterAsset(assetId, patch) {
    if (ordinaryCharacterId !== routeId || !characterAssets.some(asset => asset.id === assetId)) return;
    const operationVisit = routeWork.capture();
    const { setBusy, setStatus } = ownUI('patchCharacterAsset');
    setBusy(true);
    try {
      await characterMutations.patchAsset.mutateAsync({ assetId, patch });
      if (!operationVisit.current) return;
      await loadCharacterAssets(routeId);
    } catch (error) { setStatus(`요청 실패 · ${error.message}`); } finally { setBusy(false); }
  }
  async function deleteCharacterAsset(assetId) {
    if (ordinaryCharacterId !== routeId || !characterAssets.some(asset => asset.id === assetId)) return;
    const operationVisit = routeWork.capture();
    const actionVisit = routeWork.capture();
    const { setBusy, setStatus } = ownUI('deleteCharacterAsset');
    if (!(await confirmLocalizedDanger(formatDangerConfirmation('asset', locale)))) return;
    if (!actionVisit.current) return;
    setBusy(true);
    try {
      await characterMutations.deleteAsset.mutateAsync(assetId);
      if (!operationVisit.current) return;
      await loadCharacterAssets(routeId);
      setStatus(t('에셋 삭제 완료'));
    } catch (error) { setStatus(`요청 실패 · ${error.message}`); } finally { setBusy(false); }
  }
  async function setDefaultCharacterAsset(assetId) {
    if (characterReview) {
      const asset = characterAssets.find(item => item.id === assetId);
      if (asset && proposal?.characterId === routeId) proposal?.chooseAvatar(asset.image_url);
      return;
    }
    if (ordinaryCharacterId !== routeId || !characterAssets.some(asset => asset.id === assetId)) return;
    const operationVisit = routeWork.capture();
    const { setBusy, setStatus } = ownUI('setDefaultCharacterAsset');
    setBusy(true);
    try {
      await characterMutations.setDefaultAsset.mutateAsync(assetId);
      if (!operationVisit.current) return;
      await loadCharacterAssets(routeId);
      setStatus(t('기본 프사 변경 완료'));
    } catch (error) { setStatus(`요청 실패 · ${error.message}`); } finally { setBusy(false); }
  }

  return { characterAssets, setCharacterAssets, characterAssetsLoading, assetDraft, setAssetDraft, loadCharacterAssets, createCharacterAsset, uploadCharacterAsset, patchCharacterAsset, deleteCharacterAsset, setDefaultCharacterAsset };
}
