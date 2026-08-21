import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { characterApi } from '../api/resources';
import { queryKeys } from './useBootstrapResources';

type CharacterMutation<TVariables = unknown> = UseMutationResult<unknown, Error, TVariables, unknown>;
type SaveCharacterPayload = { id?: string | null; payload: unknown };
type CharacterPayload = { id: string; payload: unknown };
type UploadAssetPayload = { id: string; formData: FormData };
type PatchAssetPayload = { assetId: string; patch: unknown };

export type CharacterMutationsState = {
  saveCharacter: CharacterMutation<SaveCharacterPayload>;
  deleteCharacter: CharacterMutation<string>;
  playTtsSample: CharacterMutation<CharacterPayload>;
  uploadAvatar: CharacterMutation<FormData>;
  createAsset: CharacterMutation<CharacterPayload>;
  uploadAsset: CharacterMutation<UploadAssetPayload>;
  patchAsset: CharacterMutation<PatchAssetPayload>;
  deleteAsset: CharacterMutation<string>;
  setDefaultAsset: CharacterMutation<string>;
};

export function useCharacterMutations(): CharacterMutationsState {
  const queryClient = useQueryClient();
  const invalidateCharacters = () => queryClient.invalidateQueries({ queryKey: queryKeys.characters });

  return {
    saveCharacter: useMutation({
      mutationFn: characterApi.save,
      onSuccess: invalidateCharacters,
    }),
    deleteCharacter: useMutation({
      mutationFn: characterApi.remove,
      onSuccess: invalidateCharacters,
    }),
    playTtsSample: useMutation({ mutationFn: ({ id, payload }: CharacterPayload) => characterApi.ttsSample(id, payload) }),
    uploadAvatar: useMutation({ mutationFn: characterApi.uploadAvatar }),
    createAsset: useMutation({
      mutationFn: ({ id, payload }: CharacterPayload) => characterApi.createAsset(id, payload),
      onSuccess: invalidateCharacters,
    }),
    uploadAsset: useMutation({
      mutationFn: ({ id, formData }: UploadAssetPayload) => characterApi.uploadAsset(id, formData),
      onSuccess: invalidateCharacters,
    }),
    patchAsset: useMutation({
      mutationFn: ({ assetId, patch }: PatchAssetPayload) => characterApi.patchAsset(assetId, patch),
      onSuccess: invalidateCharacters,
    }),
    deleteAsset: useMutation({
      mutationFn: characterApi.deleteAsset,
      onSuccess: invalidateCharacters,
    }),
    setDefaultAsset: useMutation({
      mutationFn: characterApi.setDefaultAsset,
      onSuccess: invalidateCharacters,
    }),
  };
}
