import { useState, type Dispatch, type SetStateAction } from 'react';
import { DEFAULT_RUNTIME_SETTING } from '../constants/domain';
import { useI18n } from '../i18n/I18nProvider';

export type RuntimeSetting = {
  model_key?: string;
  fallback_model_key?: string | null;
  compression_model_key?: string;
  compression_fallback_model_key?: string | null;
  default_tts_model_option_key?: string | null;
  safety_preset?: string;
  response_length_preset?: string;
  compression_interval_turns?: number | string;
  [key: string]: unknown;
};

type RuntimeDefaultMutation = {
  mutateAsync: (payload: RuntimeSetting) => Promise<RuntimeSetting>;
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

function normalizeRuntimeDefaultPayload(setting: RuntimeSetting): RuntimeSetting {
  return {
    model_key: setting.model_key || undefined,
    fallback_model_key: setting.fallback_model_key || null,
    compression_model_key: setting.compression_model_key || undefined,
    compression_fallback_model_key: setting.compression_fallback_model_key || null,
    default_tts_model_option_key: setting.default_tts_model_option_key || null,
    safety_preset: setting.safety_preset || 'medium',
    response_length_preset: setting.response_length_preset || DEFAULT_RUNTIME_SETTING.response_length_preset,
    compression_interval_turns: Number(setting.compression_interval_turns || DEFAULT_RUNTIME_SETTING.compression_interval_turns),
  };
}

export function useRuntimeDefaultSetting({ settingsMutations, setBusy, setStatus }: RuntimeDefaultSettingOptions): RuntimeDefaultSettingState {
  const { t } = useI18n();
  const [runtimeDefaultSetting, setRuntimeDefaultSetting] = useState<RuntimeSetting>(DEFAULT_RUNTIME_SETTING);

  async function saveRuntimeDefaultSetting() {
    setBusy(true);
    try {
      const saved = await settingsMutations.updateRuntimeDefault.mutateAsync(normalizeRuntimeDefaultPayload(runtimeDefaultSetting));
      setRuntimeDefaultSetting(saved);
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
