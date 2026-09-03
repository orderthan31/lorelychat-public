export type RoutePage =
  | 'home'
  | 'characters'
  | 'characterNew'
  | 'characterDetail'
  | 'characterAssets'
  | 'conversations'
  | 'conversationDetail'
  | 'conversationEdit'
  | 'worldSettings'
  | 'worldSettingNew'
  | 'worldSettingDetail'
  | 'presets'
  | 'presetNew'
  | 'presetDetail'
  | 'chatCommands'
  | 'modelSettings'
  | 'statistics'
  | 'settings';

export interface AppRoute {
  page: RoutePage;
  id?: string;
}

export interface CharacterSummary {
  id: string;
  name: string;
  description?: string | null;
  persona?: string | null;
  avatar_url?: string | null;
  trait_scores?: Record<string, number>;
}

export interface ConversationSummary {
  id: string;
  title?: string | null;
  mode: 'user_character' | 'character_character' | string;
  genre_mode?: string | null;
  world_setting_id?: string | null;
  auto_mode?: boolean;
  tts_enabled?: boolean;
}

export type CompressionStrategy = 'fast' | 'quality';

export interface RuntimeSetting {
  scope?: 'default' | 'conversation' | string;
  model_key?: string | null;
  fallback_model_key?: string | null;
  compression_model_key?: string | null;
  compression_fallback_model_key?: string | null;
  default_tts_model_option_key?: string | null;
  safety_preset?: string;
  response_length_preset?: string;
  compression_interval_turns?: number | string;
  compression_strategy?: CompressionStrategy;
  [key: string]: unknown;
}

export type RuntimeSettingUpdatePayload = {
  model_key: string | null;
  fallback_model_key: string | null;
  compression_model_key: string | null;
  compression_fallback_model_key: string | null;
  default_tts_model_option_key: string | null;
  safety_preset: string;
  response_length_preset: string;
  compression_strategy: CompressionStrategy;
};
