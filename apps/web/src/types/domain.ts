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

export interface RuntimeSetting {
  scope?: 'default' | 'conversation' | string;
  model_key?: string;
  compression_model_key?: string;
  response_length_preset?: string;
  compression_interval_turns?: number;
}
