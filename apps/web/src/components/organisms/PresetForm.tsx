import type { Dispatch, SetStateAction } from 'react';
import { Field } from '../molecules';
import { TextareaWithExpand } from '../molecules';
import { Button } from '../atoms';
import { Input } from '../atoms';
import { Select } from '../atoms';
import { Switch } from '../atoms';
import { useI18n } from '../../i18n/I18nProvider';

export type PresetDraft = {
  preset_type?: string;
  title?: string;
  content?: string;
  description?: string;
  enabled?: boolean;
  [key: string]: unknown;
};

export type PresetFormProps = {
  draft: PresetDraft;
  setDraft: Dispatch<SetStateAction<PresetDraft>>;
  characters?: unknown[];
  onSave: () => void;
  onDelete?: () => void;
  saving?: boolean;
  isNew?: boolean;
};

export function PresetForm({ draft, setDraft, onSave, onDelete, saving, isNew }: PresetFormProps) {
  const { t } = useI18n();
  const update = (key: string, value: unknown) => setDraft((current) => ({ ...current, [key]: value }));
  return <form className="stack" data-modernized="프리셋 폼 design-system primitive marker" onSubmit={(event) => { event.preventDefault(); onSave(); }}>
    <div className="grid two compact"><Field label={t('프리셋 타입')}><Select value={draft.preset_type || 'speech_style'} onChange={(e) => update('preset_type', e.target.value)}><option value="speech_style">{t('캐릭터 말투')}</option></Select></Field><Field label={t('제목')}><Input value={draft.title || ''} onChange={(e) => update('title', e.target.value)} /></Field></div>
    <TextareaWithExpand label={t('내용')} rows={8} value={draft.content || ''} onChange={(value) => update('content', value)} placeholder={t('캐릭터 말투 프리셋 내용')} />
    <Field label={t('설명')}><Input value={draft.description || ''} onChange={(e) => update('description', e.target.value)} /></Field>
    <Switch checked={draft.enabled !== false} onChange={(e) => update('enabled', e.target.checked)} label={t('활성화')} />
    <div className="grid grid-cols-1 gap-2.5 w-full pt-1 [position:sticky] bottom-2 bg-[linear-gradient(180deg,transparent,rgba(255,250,251,.94)_20%)] dark:bg-[linear-gradient(180deg,transparent,rgba(12,17,32,.94)_20%)] [&>button]:w-full [&>button]:min-w-0 [&>button]:justify-self-stretch sm:grid-cols-2"><Button type="submit" disabled={saving}>{t(saving ? '저장 중…' : '저장')}</Button>{!isNew && <Button type="button" variant="destructive" onClick={onDelete}>{t('삭제')}</Button>}</div>
  </form>;
}
