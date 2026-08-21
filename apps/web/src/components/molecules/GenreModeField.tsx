import { GENRE_MODE_OPTIONS } from '../../constants/domain';
import { Field } from './Field';
import { normalizeGenreMode } from '../../utils/genre';
import { Select } from '../atoms';
import { useI18n } from '../../i18n/I18nProvider';
import type { UiMessageKey } from '../../i18n/core';

export type GenreModeFieldProps = {
  value?: unknown;
  onChange?: (value: string) => void;
};

export function GenreModeField({ value, onChange }: GenreModeFieldProps) {
  const { t } = useI18n();
  const selected = GENRE_MODE_OPTIONS.find((option) => option.key === normalizeGenreMode(value)) || GENRE_MODE_OPTIONS[0];
  return <Field label={t('장르 모드')}><Select data-modernized="genre-mode-select" value={selected.key} onChange={(e) => onChange?.(e.target.value)}>{GENRE_MODE_OPTIONS.map((option) => <option key={option.key} value={option.key}>{t(option.label as UiMessageKey)}</option>)}</Select></Field>;
}
