import { DEFAULT_GENRE_MODE } from '../constants/domain';

export type WorldSettingItem = {
  id?: string;
  title: string;
  thumbnail_url?: string | null;
  description?: string | null;
  genre_mode?: string | null;
  location?: string | null;
  mood?: string | null;
  world_seed?: string | null;
  opening_scene?: string | null;
  opening_line?: string | null;
  tone_preset?: string | null;
  relationship_archetype?: string | null;
  compression_focus?: string | null;
  tags?: string[];
  enabled?: boolean;
};

export const emptyWorld: WorldSettingItem = {
  title: '',
  thumbnail_url: '',
  description: '',
  genre_mode: DEFAULT_GENRE_MODE,
  location: '',
  mood: '',
  world_seed: '',
  opening_scene: '',
  opening_line: '',
  tone_preset: '',
  relationship_archetype: '',
  compression_focus: '',
  tags: [],
  enabled: true,
};

export function createEmptyWorld(): WorldSettingItem {
  return { ...emptyWorld, tags: [] };
}

export function normalizeWorld(world?: WorldSettingItem): WorldSettingItem {
  return { ...createEmptyWorld(), ...(world || {}), genre_mode: world?.genre_mode || DEFAULT_GENRE_MODE };
}
