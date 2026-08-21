import type { ReactNode } from 'react';
import { SYSTEM_ID, USER_ID } from '../constants/domain';
import { matchesQuery, pageCount, paginate } from '../utils/pagination';
import { useI18n } from '../i18n/I18nProvider';

type Entity = {
  id: string;
  name?: string;
  type?: string;
  preset_type?: string;
  usage?: string;
  [key: string]: unknown;
};

export type AppDerivedDataOptions = {
  characters: Entity[];
  presets: Entity[];
  participants: Entity[];
  presetQuery: string;
  presetPage: number;
};

export type AppDerivedDataState = {
  selectOptions: ReactNode;
  roomCharacters: Entity[];
  speechPresets: Entity[];
  filteredPresets: Entity[];
  visiblePresets: Entity[];
  speakerOptions: string[];
};

function normalizeDerivedPage(page: number, total: number): number {
  return Math.min(page, pageCount(total));
}

export function useAppDerivedData({
  characters,
  presets,
  participants,
  presetQuery,
  presetPage,
}: AppDerivedDataOptions): AppDerivedDataState {
  const { t } = useI18n();
  const selectOptions = <><option value="">{t('캐릭터 선택')}</option>{characters.map((c) => <option value={c.id} key={c.id}>{c.name}</option>)}</>;
  const roomCharacters = participants.filter((p) => p.type === 'character');
  const speechPresets = presets.filter((preset) => preset.preset_type === 'speech_style');
  const filteredPresets = presets.filter((preset) => matchesQuery(preset, presetQuery, ['preset_type', 'title', 'content', 'description']));
  const visiblePresets = paginate(filteredPresets, normalizeDerivedPage(presetPage, filteredPresets.length));
  const speakerOptions = [USER_ID, SYSTEM_ID, ...roomCharacters.map((p) => p.id)];

  return {
    selectOptions,
    roomCharacters,
    speechPresets,
    filteredPresets,
    visiblePresets,
    speakerOptions,
  };
}
