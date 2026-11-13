import type { CompressionStrategy, RuntimeSetting, RuntimeSettingUpdatePayload } from '../types/domain';

type RuntimeSettingWithStrategy = {
  compression_strategy?: unknown;
  [key: string]: unknown;
};

export function normalizeCompressionStrategy(value: unknown): CompressionStrategy {
  return value === 'fast' ? 'fast' : 'quality';
}

export function normalizeRuntimeSetting<T extends RuntimeSettingWithStrategy>(setting: T): T & { compression_strategy: CompressionStrategy } {
  return {
    ...setting,
    compression_strategy: normalizeCompressionStrategy(setting.compression_strategy),
  };
}

type RuntimeSettingPayloadDefaults = Pick<RuntimeSettingUpdatePayload, 'response_length_preset'>;

export function hasActiveDecisionModel(setting?: {
  decision_model_option_key?: string | null;
  decision_options?: { key?: string }[];
}): boolean {
  return !!setting?.decision_model_option_key && !!setting.decision_options?.some(
    (option) => option.key === setting.decision_model_option_key,
  );
}

export function normalizeRuntimeSettingUpdatePayload(
  setting: RuntimeSetting,
  defaults: RuntimeSettingPayloadDefaults,
): RuntimeSettingUpdatePayload {
  return {
    model_key: setting.model_key || null,
    fallback_model_key: setting.fallback_model_key || null,
    compression_model_key: setting.compression_model_key || null,
    compression_fallback_model_key: setting.compression_fallback_model_key || null,
    default_tts_model_option_key: setting.default_tts_model_option_key || null,
    decision_enabled: !!setting.decision_enabled,
    decision_model_option_key: setting.decision_model_option_key || null,
    safety_preset: setting.safety_preset || 'medium',
    // Omission preserves the server value; explicit null restores room inheritance.
    ...(setting.thinking_level !== undefined ? { thinking_level: setting.thinking_level } : {}),
    response_length_preset: setting.response_length_preset || defaults.response_length_preset,
    compression_strategy: normalizeCompressionStrategy(setting.compression_strategy),
  };
}
