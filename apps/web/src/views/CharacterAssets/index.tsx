import type { Dispatch, SetStateAction } from 'react';
import { CharacterAssetsPanel, type CharacterAsset, type CharacterAssetDraft } from '../../components/organisms';
import type { CharacterDraft } from '../../components/organisms';

type CharacterAssetsRoute = {
  id?: string | null;
};

export type CharacterAssetsViewProps = {
  route: CharacterAssetsRoute;
  draft: CharacterDraft;
  characterAssets?: CharacterAsset[];
  characterAssetsLoading?: boolean;
  busy?: boolean;
  assetDraft: CharacterAssetDraft;
  setAssetDraft: Dispatch<SetStateAction<CharacterAssetDraft>>;
  navigate: (path: string) => void;
  createCharacterAsset?: () => void;
  uploadCharacterAsset?: (file?: File) => Promise<void> | void;
  patchCharacterAsset?: (id: string, patch: Partial<CharacterAsset>) => void;
  deleteCharacterAsset?: (id: string) => void;
  setDefaultCharacterAsset?: (id: string) => void;
  assetUrlFor?: (url?: string) => string;
};

export function CharacterAssetsView({
  route,
  draft,
  characterAssets,
  characterAssetsLoading,
  busy = false,
  assetDraft,
  setAssetDraft,
  navigate,
  createCharacterAsset,
  uploadCharacterAsset,
  patchCharacterAsset,
  deleteCharacterAsset,
  setDefaultCharacterAsset,
}: CharacterAssetsViewProps) {
  return <section className="panel page grid max-w-[min(1180px,calc(100vw-24px))] gap-3" data-modernized="에셋 갤러리 design-system primitive marker">

    <CharacterAssetsPanel character={draft} assets={characterAssets} loading={characterAssetsLoading} saving={busy} draft={assetDraft} setDraft={setAssetDraft} onCreate={createCharacterAsset} onUpload={uploadCharacterAsset} onPatch={patchCharacterAsset} onDelete={deleteCharacterAsset} onDefault={setDefaultCharacterAsset} />
  </section>;
}
