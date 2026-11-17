import { useEffect, type Dispatch, type SetStateAction } from 'react';
import type { BootstrapResourcesState } from './useBootstrapResources';
import { normalizeRuntimeSetting } from '../utils/runtimeSettings';

type Setter<T> = Dispatch<SetStateAction<T>>;
type NamedEntity = { id?: string; name?: string };
type RuntimeSetting = { scope?: string; [key: string]: unknown };

export type BootstrapSyncOptions = {
  bootstrap: BootstrapResourcesState;
  setBackendOnline: Setter<boolean>;
  setCharacterAId: Setter<string>;
  setCharacterBId: Setter<string>;
  setMultiCharacterIds: Setter<string[]>;
  setRuntimeDefaultSetting: Setter<RuntimeSetting>;
  setConversationRuntimeSetting: Setter<RuntimeSetting>;
  setStatus: Setter<string>;
};

export function useBootstrapSync({
  bootstrap,
  setBackendOnline,
  setCharacterAId,
  setCharacterBId,
  setMultiCharacterIds,
  setRuntimeDefaultSetting,
  setConversationRuntimeSetting,
  setStatus,
}: BootstrapSyncOptions): void {
  useEffect(() => { setBackendOnline(bootstrap.health.isSuccess); }, [bootstrap.health.isSuccess, setBackendOnline]);

  useEffect(() => {
    const list = bootstrap.characters.data as NamedEntity[] | undefined;
    if (!list) return;
    setCharacterAId((current) => current || list[0]?.id || '');
    setCharacterBId((current) => current || list[1]?.id || '');
    setMultiCharacterIds((current) => {
      const defaults = [list[0]?.id || '', list[1]?.id || ''];
      const next = current.length >= 2 ? [...current] : ['', ''];
      return next.map((id, index) => id || defaults[index] || '');
    });
  }, [bootstrap.characters.data, setCharacterAId, setCharacterBId, setMultiCharacterIds]);

  useEffect(() => {
    const setting = bootstrap.runtimeDefault.data as RuntimeSetting | undefined | null;
    if (!setting) return;
    const normalizedSetting = normalizeRuntimeSetting(setting);
    setRuntimeDefaultSetting(normalizedSetting);
    setConversationRuntimeSetting((current) => normalizeRuntimeSetting({ ...normalizedSetting, thinking_level: null, ...(current?.scope === 'conversation' ? current : {}) }));
  }, [bootstrap.runtimeDefault.data, setRuntimeDefaultSetting, setConversationRuntimeSetting]);

  useEffect(() => {
    const firstError = [bootstrap.characters.error, bootstrap.conversations.error].find(Boolean);
    if (firstError) setStatus(firstError.message || String(firstError));
  }, [bootstrap.characters.error, bootstrap.conversations.error, setStatus]);
}
