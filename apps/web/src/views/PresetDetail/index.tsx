import type { Dispatch, SetStateAction } from 'react';
import { PresetForm, type PresetDraft } from '../../components/organisms';
import { FormSection } from '../../components/molecules';
import { useI18n } from '../../i18n/I18nProvider';
import { formatNamedAction } from '../../i18n/core';

type DetailRoute = {
  page?: string;
  id?: string | null;
};

type CharacterOption = { id?: string; name?: string };

export type PresetDetailViewProps = {
  route: DetailRoute;
  presetDraft: PresetDraft;
  setPresetDraft: Dispatch<SetStateAction<PresetDraft>>;
  characters?: CharacterOption[];
  savePreset: () => void;
  deletePreset: (id?: string | null) => void;
  busy?: boolean;
  navigate: (path: string) => void;
};

export function PresetDetailView({ route, presetDraft, setPresetDraft, characters = [], savePreset, deletePreset, busy = false, navigate }: PresetDetailViewProps) {
  const { locale, t } = useI18n();
  const isNew = route.page === 'presetNew';
  return <section className="panel page grid gap-3" data-modernized="상세 편집 design-system primitive marker">
    <FormSection title={isNew ? t('새 프리셋 등록') : formatNamedAction(presetDraft.title || t('프리셋'), 'edit', locale)}>
      <PresetForm draft={presetDraft} setDraft={setPresetDraft} characters={characters} onSave={savePreset} onDelete={() => deletePreset(route.id)} saving={busy} isNew={isNew} />
    </FormSection>
  </section>;
}
