import { useQuery, useQueryClient, type QueryClient, type UseQueryResult } from '@tanstack/react-query';
import { characterApi, conversationApi, healthApi, settingsApi } from '../api/resources';

export const queryKeys = {
  health: ['health'],
  characters: ['characters'],
  characterUsageRanking: ['characters', 'usage-ranking'],
  conversations: ['conversations'],
  presets: ['presets', 'include_disabled'],
  chatCommands: ['chat-commands', 'include_disabled'],
  worldSettings: ['world-settings', 'include_disabled'],
  ttsVoices: ['tts', 'voices'],
  ttsModels: ['tts', 'models'],
  runtimeDefault: ['runtime-settings', 'default'],
} as const;

type BootstrapQuery<T = unknown> = UseQueryResult<T | null, Error>;

export type BootstrapResourcesState = {
  queryClient: QueryClient;
  health: BootstrapQuery;
  characters: UseQueryResult<unknown[], Error>;
  characterUsageRanking: UseQueryResult<unknown[], Error>;
  conversations: UseQueryResult<unknown[], Error>;
  presets: BootstrapQuery;
  chatCommands: BootstrapQuery;
  worldSettings: BootstrapQuery;
  ttsVoices: BootstrapQuery;
  ttsModels: BootstrapQuery;
  runtimeDefault: BootstrapQuery;
  refetchBootstrap: () => Promise<unknown[]>;
};

export function useBootstrapResources(): BootstrapResourcesState {
  const queryClient = useQueryClient();
  const health = useQuery({ queryKey: queryKeys.health, queryFn: healthApi.check, retry: false });
  const characters = useQuery({ queryKey: queryKeys.characters, queryFn: characterApi.list });
  const characterUsageRanking = useQuery({ queryKey: queryKeys.characterUsageRanking, queryFn: characterApi.usageRanking });
  const conversations = useQuery({ queryKey: queryKeys.conversations, queryFn: conversationApi.list });
  const presets = useQuery({ queryKey: queryKeys.presets, queryFn: settingsApi.presets });
  const chatCommands = useQuery({ queryKey: queryKeys.chatCommands, queryFn: settingsApi.chatCommands });
  const worldSettings = useQuery({ queryKey: queryKeys.worldSettings, queryFn: settingsApi.worldSettings });
  const ttsVoices = useQuery({ queryKey: queryKeys.ttsVoices, queryFn: settingsApi.ttsVoices });
  const ttsModels = useQuery({ queryKey: queryKeys.ttsModels, queryFn: settingsApi.ttsModels });
  const runtimeDefault = useQuery({ queryKey: queryKeys.runtimeDefault, queryFn: settingsApi.runtimeDefault });

  return {
    queryClient,
    health,
    characters,
    characterUsageRanking,
    conversations,
    presets,
    chatCommands,
    worldSettings,
    ttsVoices,
    ttsModels,
    runtimeDefault,
    refetchBootstrap: () => Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.characters }),
      queryClient.invalidateQueries({ queryKey: queryKeys.characterUsageRanking }),
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations }),
      queryClient.invalidateQueries({ queryKey: queryKeys.presets }),
      queryClient.invalidateQueries({ queryKey: queryKeys.chatCommands }),
      queryClient.invalidateQueries({ queryKey: queryKeys.worldSettings }),
      queryClient.invalidateQueries({ queryKey: queryKeys.ttsVoices }),
      queryClient.invalidateQueries({ queryKey: queryKeys.ttsModels }),
      queryClient.invalidateQueries({ queryKey: queryKeys.runtimeDefault }),
    ]),
  };
}
