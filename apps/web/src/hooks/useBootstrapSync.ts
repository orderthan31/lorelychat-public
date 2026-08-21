import { useEffect, type Dispatch, type SetStateAction } from 'react';
import type { BootstrapResourcesState } from './useBootstrapResources';

type Setter<T> = Dispatch<SetStateAction<T>>;
type NamedEntity = { id?: string; name?: string };
type RuntimeSetting = { scope?: string; [key: string]: unknown };

export type BootstrapSyncOptions = {
  bootstrap: BootstrapResourcesState;
  setBackendOnline: Setter<boolean>;
  setCharacters: Setter<NamedEntity[]>;
  setCharacterUsageRanking: Setter<NamedEntity[]>;
  setCharacterAId: Setter<string>;
  setCharacterBId: Setter<string>;
  setMultiCharacterIds: Setter<string[]>;
  setConversations: Setter<unknown[]>;
  setPresets: Setter<unknown[]>;
  setChatCommands: Setter<unknown[]>;
  setWorldSettings: Setter<unknown[]>;
  setTtsVoices: Setter<unknown[]>;
  setTtsModels: Setter<unknown[]>;
  setRuntimeDefaultSetting: Setter<RuntimeSetting>;
  setConversationRuntimeSetting: Setter<RuntimeSetting>;
  setStatus: Setter<string>;
};

export function useBootstrapSync({
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
}: BootstrapSyncOptions): void {
  useEffect(() => { setBackendOnline(bootstrap.health.isSuccess); }, [bootstrap.health.isSuccess, setBackendOnline]);

  useEffect(() => {
    const list = bootstrap.characters.data as NamedEntity[] | undefined;
    if (!list) return;
    setCharacters(list);
    setCharacterAId((current) => current || list[0]?.id || '');
    setCharacterBId((current) => current || list[1]?.id || '');
    setMultiCharacterIds((current) => {
      const defaults = [list[0]?.id || '', list[1]?.id || ''];
      const next = current.length >= 2 ? [...current] : ['', ''];
      return next.map((id, index) => id || defaults[index] || '');
    });
  }, [bootstrap.characters.data, setCharacters, setCharacterAId, setCharacterBId, setMultiCharacterIds]);

  useEffect(() => {
    const list = bootstrap.characterUsageRanking.data as NamedEntity[] | undefined;
    if (list) setCharacterUsageRanking(list);
  }, [bootstrap.characterUsageRanking.data, setCharacterUsageRanking]);

  useEffect(() => { if (bootstrap.conversations.data) setConversations(bootstrap.conversations.data); }, [bootstrap.conversations.data, setConversations]);
  useEffect(() => { if (bootstrap.presets.data) setPresets(bootstrap.presets.data as unknown[]); }, [bootstrap.presets.data, setPresets]);
  useEffect(() => { if (bootstrap.chatCommands.data) setChatCommands(bootstrap.chatCommands.data as unknown[]); }, [bootstrap.chatCommands.data, setChatCommands]);
  useEffect(() => { if (bootstrap.worldSettings.data) setWorldSettings(bootstrap.worldSettings.data as unknown[]); }, [bootstrap.worldSettings.data, setWorldSettings]);
  useEffect(() => { if (bootstrap.ttsVoices.data) setTtsVoices(bootstrap.ttsVoices.data as unknown[]); }, [bootstrap.ttsVoices.data, setTtsVoices]);
  useEffect(() => { if (bootstrap.ttsModels.data) setTtsModels(bootstrap.ttsModels.data as unknown[]); }, [bootstrap.ttsModels.data, setTtsModels]);

  useEffect(() => {
    const setting = bootstrap.runtimeDefault.data as RuntimeSetting | undefined | null;
    if (!setting) return;
    setRuntimeDefaultSetting(setting);
    setConversationRuntimeSetting((current) => ({ ...setting, ...(current?.scope === 'conversation' ? current : {}) }));
  }, [bootstrap.runtimeDefault.data, setRuntimeDefaultSetting, setConversationRuntimeSetting]);

  useEffect(() => {
    const firstError = [bootstrap.characters.error, bootstrap.conversations.error].find(Boolean);
    if (firstError) setStatus(firstError.message || String(firstError));
  }, [bootstrap.characters.error, bootstrap.conversations.error, setStatus]);
}
