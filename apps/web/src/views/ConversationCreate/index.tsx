import type { ReactNode } from 'react';
import { Field, FormSection } from '../../components/molecules';
import { CAST_ROLE_PRESETS, USER_SETTING_PRESETS } from '../../constants/domain';
import { Button } from '../../components/atoms';
import { Input } from '../../components/atoms';
import { Select } from '../../components/atoms';
import { Switch } from '../../components/atoms';
import { Textarea } from '../../components/atoms';
import { Plus, Trash2 } from 'lucide-react';
import { useI18n } from '../../i18n/I18nProvider';
import { formatCharacterSlot, formatSelectedCharacters, type UiMessageKey } from '../../i18n/core';

export type ConversationWorldSetting = {
  id: string;
  title?: string;
  enabled?: boolean;
};

export type ConversationCreateSceneDraft = {
  tts_enabled?: boolean;
  [key: string]: unknown;
};

export type ConversationCreateViewProps = {
  busy?: boolean;
  navigate: (path: string) => void;
  startConversationRoom?: () => void;
  status?: string;
  conversationTitle?: string;
  setConversationTitle: (value: string) => void;
  selectedWorldSettingId?: string;
  setSelectedWorldSettingId: (value: string) => void;
  worldSettings?: ConversationWorldSetting[];
  scene: ConversationCreateSceneDraft;
  setScene: (value: ConversationCreateSceneDraft | ((current: ConversationCreateSceneDraft) => ConversationCreateSceneDraft)) => void;
  userDescription?: string;
  setUserDescription: (value: string) => void;
  applyPreset?: (field: string, content?: string) => void;
  multiCharacterIds?: string[];
  multiCharacterRoles?: string[];
  updateMultiCharacterSlot: (index: number, value: string) => void;
  updateMultiCharacterRole: (index: number, value: string) => void;
  removeMultiCharacterSlot: (index: number) => void;
  addMultiCharacterSlot: () => void;
  selectOptions?: ReactNode;
};

export function ConversationCreateView({
  busy = false,
  startConversationRoom,
  status = '',
  conversationTitle = '',
  setConversationTitle,
  selectedWorldSettingId = '',
  setSelectedWorldSettingId,
  worldSettings = [],
  scene,
  setScene,
  userDescription = '',
  setUserDescription,
  applyPreset,
  multiCharacterIds = [],
  multiCharacterRoles = [],
  updateMultiCharacterSlot,
  updateMultiCharacterRole,
  removeMultiCharacterSlot,
  addMultiCharacterSlot,
  selectOptions,
}: ConversationCreateViewProps) {
  const { locale, t } = useI18n();
  const updateScene = (patch: Partial<ConversationCreateSceneDraft>) => setScene((current) => ({ ...current, ...patch }));
  const selectedMultiCharacterIds = multiCharacterIds.filter(Boolean);
  const uniqueMultiCharacterCount = new Set(selectedMultiCharacterIds).size;
  const characterSelectionReady = selectedMultiCharacterIds.length >= 1 && uniqueMultiCharacterCount === selectedMultiCharacterIds.length;
  const worldSelectionReady = !!selectedWorldSettingId;
  const canStart = characterSelectionReady && worldSelectionReady;
  const characterSelectionHint = selectedMultiCharacterIds.length < 1
    ? t('참여 캐릭터 1명 이상 선택 필요')
    : uniqueMultiCharacterCount !== selectedMultiCharacterIds.length
      ? t('같은 캐릭터가 중복 선택되어 있어요. 서로 다른 캐릭터로 골라줘.')
      : formatSelectedCharacters(selectedMultiCharacterIds.length, locale);

  return <section className="panel page grid gap-4" data-modernized="대화방 생성 world-select-only marker flat form sections">
    <FormSection title={t('참여 캐릭터')} action={<Button type="button" size="sm" variant="secondary" onClick={addMultiCharacterSlot}><Plus className="size-4" aria-hidden="true" />{t('추가')}</Button>}>
      {multiCharacterIds.map((id, index) => <div className="relative grid min-w-0 gap-2.5 border-t border-border pt-3 first:border-t-0 first:pt-0 min-[720px]:grid-cols-2" key={index}>
        <Field className={multiCharacterIds.length > 1 ? 'min-w-0 pr-12 min-[720px]:pr-0' : 'min-w-0'} label={formatCharacterSlot(index + 1, locale)}><Select value={id} onChange={(e) => updateMultiCharacterSlot(index, e.target.value)}>{selectOptions}</Select></Field>
        <Field className="min-w-0" label={t('캐릭터 역할')}><Select value={multiCharacterRoles[index] || ''} onChange={(e) => updateMultiCharacterRole(index, e.target.value)}>{CAST_ROLE_PRESETS.map((preset) => <option key={preset.key || 'none'} value={preset.key}>{t(preset.label as UiMessageKey)} · {t(preset.hint as UiMessageKey)}</option>)}</Select></Field>
        {multiCharacterIds.length > 1 && <Button type="button" variant="ghost" size="icon" className="absolute right-0 top-0 text-danger" aria-label={`${formatCharacterSlot(index + 1, locale)} ${t('삭제')}`} title={`${formatCharacterSlot(index + 1, locale)} ${t('삭제')}`} onClick={() => removeMultiCharacterSlot(index)}><Trash2 className="size-4" aria-hidden="true" /></Button>}
      </div>)}
      <div className="grid gap-1.5 text-xs text-muted-foreground">
        <span className="flex items-center gap-2"><span className={`size-2 rounded-full ${characterSelectionReady ? 'bg-success' : 'bg-warning'}`} aria-hidden="true" />{characterSelectionHint}</span>
        <span className="flex items-center gap-2"><span className={`size-2 rounded-full ${worldSelectionReady ? 'bg-success' : 'bg-warning'}`} aria-hidden="true" />{t(worldSelectionReady ? '세계관 선택 완료' : '세계관 선택 필요')}</span>
        {status ? <span>{status}</span> : null}
      </div>
    </FormSection>

    <FormSection title={t('대화방 정보')}>
      <Field label={t('대화방 제목')}><Input value={conversationTitle} onChange={(e) => setConversationTitle(e.target.value)} placeholder={t('비워두면 캐릭터 이름으로 자동 생성')} /></Field>
      <Field label={t('세계관')}><Select value={selectedWorldSettingId} onChange={(e) => setSelectedWorldSettingId(e.target.value)}><option value="">{t('세계관 선택')}</option>{worldSettings.filter((world) => world.enabled !== false).map((world) => <option key={world.id} value={world.id}>{world.title || world.id}</option>)}</Select></Field>
      <Switch checked={!!scene.tts_enabled} onChange={(e) => updateScene({ tts_enabled: e.target.checked })} label={t('캐릭터 TTS 자동 생성')} />
      <Field label={t('유저페르소나')}><Textarea rows={4} value={userDescription} onChange={(e) => setUserDescription(e.target.value)} placeholder={t('예: 사용자는 개발자이고 캐릭터와 가까운 관계다.')} /></Field>
      <div className="preset-inline"><Select defaultValue="" onChange={(e) => { if (e.target.value) { applyPreset?.('user_setting', e.target.value); e.target.value = ''; } }}><option value="">{t('유저페르소나 프리셋 불러오기')}</option>{USER_SETTING_PRESETS.map((preset) => <option key={preset.title} value={preset.content}>{t(preset.title as UiMessageKey)}</option>)}</Select></div>
    </FormSection>
  </section>;
}
