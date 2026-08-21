import { DEFAULT_FORBIDDEN_RULES, DEFAULT_TRAIT_SCORES } from '../constants/domain';
import { cleanRuleLines } from './text';

type TraitScores = Record<string, number>;

export type CharacterDraftInput = {
  name: string;
  description?: string | null;
  persona: string;
  appearance?: string | null;
  behavior_style?: string | null;
  speech_style?: string | null;
  emotional_rules?: unknown;
  forbidden_rules?: unknown;
  default_model?: string | null;
  avatar_url?: string | null;
  trait_scores?: TraitScores | null;
  tts_provider?: string | null;
  tts_model?: string | null;
  tts_voice_style?: string | null;
  tts_sample_text?: string | null;
};

export function normalizeCharacterForApi(character: CharacterDraftInput) {
  const emotional = cleanRuleLines(character.emotional_rules);
  const forbidden = cleanRuleLines(character.forbidden_rules);
  return {
    name: character.name.trim(),
    description: character.description?.trim() || null,
    persona: character.persona.trim(),
    appearance: character.appearance?.trim() || null,
    behavior_style: character.behavior_style?.trim() || null,
    speech_style: character.speech_style?.trim() || null,
    emotional_rules: emotional,
    forbidden_rules: forbidden.length ? forbidden : DEFAULT_FORBIDDEN_RULES,
    default_model: character.default_model?.trim() || null,
    avatar_url: character.avatar_url?.trim() || null,
    trait_scores: { ...DEFAULT_TRAIT_SCORES, ...(character.trait_scores || {}) },
    tts_provider: character.tts_provider || 'supertonic',
    tts_model: character.tts_model || (character.tts_provider === 'gemini' ? 'gemini-3.1-flash-tts-preview' : 'supertonic-3'),
    tts_voice_style: character.tts_voice_style || (character.tts_provider === 'gemini' ? 'Kore' : 'F1'),
    tts_sample_text: character.tts_sample_text?.trim() || null,
  };
}
