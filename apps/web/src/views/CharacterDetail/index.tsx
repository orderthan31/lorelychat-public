import type { Dispatch, SetStateAction } from 'react';
import { CharacterForm, type CharacterDraft, type SpeechPreset, type TtsOption } from '../../components/organisms';
import type { CharacterAsset, CharacterAssetDraft } from '../../components/organisms';
import { Button } from '../../components/atoms';

type CharacterRoute = {
  page?: string;
  id?: string | null;
};

export type CharacterDetailViewProps = {
  route: CharacterRoute;
  draft: CharacterDraft;
  setDraft: Dispatch<SetStateAction<CharacterDraft>>;
  ttsVoices?: TtsOption[];
  ttsModels?: TtsOption[];
  busy?: boolean;
  navigate: (path: string) => void;
  setCardPreview: (draft: CharacterDraft) => void;
  saveCharacter: () => void;
  deleteCharacter?: () => void;
  uploadAvatar?: (file: File | undefined, update: <K extends keyof CharacterDraft>(key: K, value: CharacterDraft[K]) => void) => Promise<void> | void;
  playCharacterTtsSample?: (draft: CharacterDraft) => void;
  speechPresets?: SpeechPreset[];
  applyPreset?: (field: keyof CharacterDraft, content?: string) => void;
  characterAssets?: CharacterAsset[];
  characterAssetsLoading?: boolean;
  assetDraft: CharacterAssetDraft;
  setAssetDraft: Dispatch<SetStateAction<CharacterAssetDraft>>;
  createCharacterAsset?: () => void;
  uploadCharacterAsset?: (file?: File) => Promise<void> | void;
  patchCharacterAsset?: (id: string, patch: Partial<CharacterAsset>) => void;
  deleteCharacterAsset?: (id: string) => void;
  setDefaultCharacterAsset?: (id: string) => void;
};

export function CharacterDetailView({
  route,
  draft,
  setDraft,
  ttsVoices,
  ttsModels,
  busy = false,
  navigate,
  setCardPreview,
  saveCharacter,
  deleteCharacter,
  uploadAvatar,
  playCharacterTtsSample,
  speechPresets,
  applyPreset,
  characterAssets,
  characterAssetsLoading,
  assetDraft,
  setAssetDraft,
  createCharacterAsset,
  uploadCharacterAsset,
  patchCharacterAsset,
  deleteCharacterAsset,
  setDefaultCharacterAsset,
}: CharacterDetailViewProps) {
  return <section className="panel page grid gap-3" data-modernized="캐릭터 상세 shadcn primitive marker">
    <CharacterForm draft={draft} setDraft={setDraft} onSave={saveCharacter} onDelete={deleteCharacter} saving={busy} isNew={route.page === 'characterNew'} speechPresets={speechPresets} applyPreset={applyPreset} onAvatarUpload={uploadAvatar} ttsVoices={ttsVoices} ttsModels={ttsModels} onTtsSample={playCharacterTtsSample} characterId={route.id} assets={characterAssets} assetsLoading={characterAssetsLoading} assetDraft={assetDraft} setAssetDraft={setAssetDraft} onCreateAsset={createCharacterAsset} onUploadAsset={uploadCharacterAsset} onPatchAsset={patchCharacterAsset} onDeleteAsset={deleteCharacterAsset} onDefaultAsset={setDefaultCharacterAsset} />
  </section>;
}
