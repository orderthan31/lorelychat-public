import { DEFAULT_GENRE_MODE, GENRE_MODE_OPTIONS } from '../constants/domain';

export function normalizeGenreMode(value: unknown): string {
  const key = String(value || DEFAULT_GENRE_MODE).trim().toLowerCase().replace(/-/g, '_');
  return GENRE_MODE_OPTIONS.some((option: { key: string }) => option.key === key) ? key : DEFAULT_GENRE_MODE;
}

export function genreModeLabel(value: unknown): string {
  const key = normalizeGenreMode(value);
  const option = GENRE_MODE_OPTIONS.find((item: { key: string; label: string }) => item.key === key);
  return option ? option.label : key;
}
