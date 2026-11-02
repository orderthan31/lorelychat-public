import { useState, useRef, useCallback, type Dispatch, type SetStateAction } from 'react';
import { DEFAULT_RUNTIME_SETTING } from '../constants/domain';
import { useI18n } from '../i18n/I18nProvider';
import type { RuntimeSetting, RuntimeSettingUpdatePayload } from '../types/domain';
import { normalizeRuntimeSetting, normalizeRuntimeSettingUpdatePayload } from '../utils/runtimeSettings';

type RuntimeDefaultMutation = {
  mutateAsync: (payload: RuntimeSettingUpdatePayload) => Promise<RuntimeSetting | null>;
};

export type RuntimeDefaultSettingOptions = {
  settingsMutations: {
    updateRuntimeDefault: RuntimeDefaultMutation;
  };
  setBusy: Dispatch<SetStateAction<boolean>>;
  setStatus: Dispatch<SetStateAction<string>>;
  ownUI?: (operation: string) => { setBusy: Dispatch<SetStateAction<boolean>>; setStatus: Dispatch<SetStateAction<string>> };
};

export type RuntimeDefaultSettingState = {
  runtimeDefaultSetting: RuntimeSetting;
  setRuntimeDefaultSetting: Dispatch<SetStateAction<RuntimeSetting>>;
  saveRuntimeDefaultSetting: () => Promise<void>;
};

export function normalizeRuntimeDefaultPayload(setting: RuntimeSetting): RuntimeSettingUpdatePayload {
  return normalizeRuntimeSettingUpdatePayload(setting, DEFAULT_RUNTIME_SETTING);
}

export function useRuntimeDefaultSetting({ settingsMutations, setBusy, setStatus, ownUI }: RuntimeDefaultSettingOptions): RuntimeDefaultSettingState {
  const { t } = useI18n();
  const [runtimeDefaultSetting, installRuntimeDefaultSetting] = useState<RuntimeSetting>(DEFAULT_RUNTIME_SETTING);
  const draftVersion = useRef(0);
  const setRuntimeDefaultSetting = useCallback<Dispatch<SetStateAction<RuntimeSetting>>>((value) => { draftVersion.current++; installRuntimeDefaultSetting(value); }, []);

  async function saveRuntimeDefaultSetting() {
    const submittedVersion = draftVersion.current;
    const ui = ownUI?.('saveRuntimeDefaultSetting') || { setBusy, setStatus };
    ui.setBusy(true);
    try {
      const saved = await settingsMutations.updateRuntimeDefault.mutateAsync(normalizeRuntimeDefaultPayload(runtimeDefaultSetting));
      if (draftVersion.current === submittedVersion) setRuntimeDefaultSetting(normalizeRuntimeSetting(saved || runtimeDefaultSetting));
      ui.setStatus(t('기본 채팅 설정 저장 완료'));
    } catch (error) {
      ui.setStatus(`저장 실패 · ${(error as Error).message}`);
    } finally {
      ui.setBusy(false);
    }
  }

  return {
    runtimeDefaultSetting,
    setRuntimeDefaultSetting,
    saveRuntimeDefaultSetting,
  };
}
