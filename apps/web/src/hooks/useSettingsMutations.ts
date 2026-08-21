import { useMutation, useQueryClient, type QueryKey, type UseMutationResult } from '@tanstack/react-query';
import { settingsApi } from '../api/resources';
import { queryKeys } from './useBootstrapResources';

type SettingsMutation<TVariables = unknown> = UseMutationResult<unknown, Error, TVariables, unknown>;
type SavePayload = { id?: string | null; payload: unknown };

export type SettingsMutationsState = {
  updateRuntimeDefault: SettingsMutation;
  savePreset: SettingsMutation<SavePayload>;
  deletePreset: SettingsMutation<string>;
  saveChatCommand: SettingsMutation<SavePayload>;
  deleteChatCommand: SettingsMutation<string>;
  saveWorldSetting: SettingsMutation<SavePayload>;
  deleteWorldSetting: SettingsMutation<string>;
};

export function useSettingsMutations(): SettingsMutationsState {
  const queryClient = useQueryClient();
  const invalidate = (queryKey: QueryKey) => queryClient.invalidateQueries({ queryKey });

  return {
    updateRuntimeDefault: useMutation({
      mutationFn: settingsApi.updateRuntimeDefault,
      onSuccess: () => invalidate(queryKeys.runtimeDefault),
    }),
    savePreset: useMutation({
      mutationFn: settingsApi.savePreset,
      onSuccess: () => invalidate(queryKeys.presets),
    }),
    deletePreset: useMutation({
      mutationFn: settingsApi.deletePreset,
      onSuccess: () => invalidate(queryKeys.presets),
    }),

    saveChatCommand: useMutation({
      mutationFn: settingsApi.saveChatCommand,
      onSuccess: () => invalidate(queryKeys.chatCommands),
    }),
    deleteChatCommand: useMutation({
      mutationFn: settingsApi.deleteChatCommand,
      onSuccess: () => invalidate(queryKeys.chatCommands),
    }),
    saveWorldSetting: useMutation({
      mutationFn: settingsApi.saveWorldSetting,
      onSuccess: () => invalidate(queryKeys.worldSettings),
    }),
    deleteWorldSetting: useMutation({
      mutationFn: settingsApi.deleteWorldSetting,
      onSuccess: () => invalidate(queryKeys.worldSettings),
    }),
  };
}
