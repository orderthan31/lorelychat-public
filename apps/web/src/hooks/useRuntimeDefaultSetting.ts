import { useState, type Dispatch, type SetStateAction } from 'react';
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
};

export type RuntimeDefaultSettingState = {
  runtimeDefaultSetting: RuntimeSetting;
  setRuntimeDefaultSetting: Dispatch<SetStateAction<RuntimeSetting>>;
  saveRuntimeDefaultSetting: () => Promise<void>;
};

export function normalizeRuntimeDefaultPayload(setting: RuntimeSetting): RuntimeSettingUpdatePayload {
  return normalizeRuntimeSettingUpdatePayload(setting, DEFAULT_RUNTIME_SETTING);
}

export function useRuntimeDefaultSetting({ settingsMutations, setBusy, setStatus }: RuntimeDefaultSettingOptions): RuntimeDefaultSettingState {
  const { t } = useI18n();
  const [runtimeDefaultSetting, setRuntimeDefaultSetting] = useState<RuntimeSetting>(DEFAULT_RUNTIME_SETTING);

  async function saveRuntimeDefaultSetting() {
    setBusy(true);
    try {
      const saved = await settingsMutations.updateRuntimeDefault.mutateAsync(normalizeRuntimeDefaultPayload(runtimeDefaultSetting));
      setRuntimeDefaultSetting(normalizeRuntimeSetting(saved || runtimeDefaultSetting));
      setStatus(t('기본 채팅 설정 저장 완료'));
    } finally {
      setBusy(false);
    }
  }

  return {
    runtimeDefaultSetting,
    setRuntimeDefaultSetting,
    saveRuntimeDefaultSetting,
  };
}
