import {
  DEFAULT_RUNTIME_SETTING,
  FALLBACK_COMPRESSION_INTERVAL_OPTIONS,
  FALLBACK_RESPONSE_LENGTH_PRESETS,
} from '../../constants/domain';
import { Field, FormSection } from '../molecules';
import { Button } from '../atoms';
import { Card, CardContent, CardHeader, CardTitle } from '../atoms';
import { Select } from '../atoms';
import { Switch } from '../atoms';
import { cn } from '../../lib/utils';
import { useI18n } from '../../i18n/I18nProvider';
import type { Translator } from '../../i18n/core';

type RuntimeOption = { key?: string; label?: string; provider_account_id?: string | null; provider_account_alias?: string | null; provider?: string; model?: string };
type CompressionIntervalOption = { turns?: number | string; label?: string; description?: string };
export type RuntimeSettingValue = {
  model_key?: string | null;
  fallback_model_key?: string | null;
  compression_model_key?: string | null;
  compression_fallback_model_key?: string | null;
  effective_compression_fallback_model_key?: string | null;
  compression_fallback_source?: string;
  default_tts_model_option_key?: string | null;
  safety_preset?: 'high' | 'medium' | 'low' | string;
  response_length_preset?: string;
  compression_interval_turns?: string | number;
  options?: RuntimeOption[];
  compression_options?: RuntimeOption[];
  tts_options?: RuntimeOption[];
  response_length_presets?: RuntimeOption[];
  compression_interval_options?: CompressionIntervalOption[];
  [key: string]: unknown;
};

type RuntimeSettingChange = (value: RuntimeSettingValue) => void;

export type RuntimeSettingFieldsProps = {
  value?: RuntimeSettingValue;
  onChange?: RuntimeSettingChange;
  compact?: boolean;
};

export type RuntimeSettingsPanelProps = {
  setting?: RuntimeSettingValue;
  onChange?: RuntimeSettingChange;
  onSave?: () => void;
  saving?: boolean;
  title?: string;

};

export type ChatSettingsBubbleProps = RuntimeSettingsPanelProps & {
  open: boolean;
  onClose?: () => void;
  renderImages?: boolean;
  onRenderImagesChange?: (value: boolean) => void;
  ttsEnabled?: boolean;
  onTtsEnabledChange?: (value: boolean) => void;
  onCompressNow?: () => void;
};

function runtimeModelOptions(setting?: RuntimeSettingValue): RuntimeOption[] { return setting?.options?.length ? setting.options : []; }
function compressionModelOptions(setting?: RuntimeSettingValue): RuntimeOption[] { return setting?.compression_options?.length ? setting.compression_options : []; }
function ttsModelOptions(setting?: RuntimeSettingValue): RuntimeOption[] { return setting?.tts_options?.length ? setting.tts_options : []; }
function responseLengthPresets(setting?: RuntimeSettingValue): RuntimeOption[] { return setting?.response_length_presets?.length ? setting.response_length_presets : FALLBACK_RESPONSE_LENGTH_PRESETS; }
function compressionIntervalOptions(setting?: RuntimeSettingValue): CompressionIntervalOption[] { return setting?.compression_interval_options?.length ? setting.compression_interval_options : FALLBACK_COMPRESSION_INTERVAL_OPTIONS; }
function localizedResponseLengthLabel(option: RuntimeOption, t: Translator): string {
  if (option.key === 'short') return t('짧게');
  if (option.key === 'medium') return t('중간');
  if (option.key === 'long') return t('긴대화');
  return option.label || option.key || '';
}
function localizedCompressionInterval(option: CompressionIntervalOption, t: Translator): { label: string; description?: string } {
  const known = {
    2: [t('2턴마다 · 강한 기억 유지'), t('새 방/관계 초반처럼 첫 상황과 말맛을 자주 고정해야 할 때')],
    3: [t('3턴마다 · 자주/안전'), t('중요 장면, 리그 초반, 관계 변화가 잦은 방')],
    5: [t('5턴마다 · 기본 추천'), t('품질과 비용 균형이 가장 무난한 기본값')],
    8: [t('8턴마다 · 비용 절약'), t('긴 흐름은 유지하되 압축 호출을 줄이고 싶을 때')],
    12: [t('12턴마다 · 최소 압축'), t('테스트나 저비용 장시간 대화용')],
  } as const;
  const localized = known[Number(option.turns) as keyof typeof known];
  return localized ? { label: localized[0], description: localized[1] } : { label: option.label || `${option.turns}${t('턴마다')}`, description: option.description };
}
function RuntimeSettingFields({ value = DEFAULT_RUNTIME_SETTING, onChange, compact = false }: RuntimeSettingFieldsProps) {
  const { t } = useI18n();
  const options = runtimeModelOptions(value);
  const fallbackOptions = options.filter((option) => option.key !== value.model_key);
  const compressionOptions = compressionModelOptions(value);
  const compressionFallbackOptions = compressionOptions.filter((option) => option.key !== value.compression_model_key);
  const inheritedCompressionFallback = compressionOptions.find((option) => option.key === value.fallback_model_key);
  const chatAccounts = Array.from(new Map(options.map((option) => [option.provider_account_id || '', option])).entries()).filter(([id]) => id);
  const fallbackAccounts = Array.from(new Map(fallbackOptions.map((option) => [option.provider_account_id || '', option])).entries()).filter(([id]) => id);
  const compressionAccounts = Array.from(new Map(compressionOptions.map((option) => [option.provider_account_id || '', option])).entries()).filter(([id]) => id);
  const selectedChatAccountId = options.find((option) => option.key === value.model_key)?.provider_account_id || '';
  const selectedFallbackAccountId = fallbackOptions.find((option) => option.key === value.fallback_model_key)?.provider_account_id || '';
  const selectedCompressionAccountId = compressionOptions.find((option) => option.key === value.compression_model_key)?.provider_account_id || '';
  const chatOptionsForAccount = selectedChatAccountId ? options.filter((option) => option.provider_account_id === selectedChatAccountId) : [];
  const fallbackOptionsForAccount = selectedFallbackAccountId ? fallbackOptions.filter((option) => option.provider_account_id === selectedFallbackAccountId) : [];
  const compressionOptionsForAccount = selectedCompressionAccountId ? compressionOptions.filter((option) => option.provider_account_id === selectedCompressionAccountId) : [];
  const update = (patch: Partial<RuntimeSettingValue>) => onChange?.({ ...DEFAULT_RUNTIME_SETTING, ...(value || {}), ...patch });
  return <div className={cn('grid grid-cols-1 gap-2.5 min-[720px]:grid-cols-[minmax(0,1.25fr)_minmax(160px,.75fr)]', compact && 'gap-2')}>
    <Field label={t('채팅 Provider')}><Select value={selectedChatAccountId} disabled={!chatAccounts.length} onChange={(e) => {
      const accountOptions = options.filter((option) => option.provider_account_id === e.target.value);
      const modelKey = accountOptions[0]?.key || null;
      update({ model_key: modelKey, ...(modelKey === value.fallback_model_key ? { fallback_model_key: null } : {}) });
    }}><option value="">{chatAccounts.length ? t('Provider 계정 선택') : t('선택 가능한 모델 없음')}</option>{chatAccounts.map(([id, option]) => <option key={id} value={id}>{option.provider_account_alias || option.provider || id}</option>)}</Select></Field>
    <Field label={t('채팅 모델')}><Select value={String(value.model_key || '')} disabled={!chatOptionsForAccount.length} onChange={(e) => {
      const modelKey = e.target.value || null;
      update({ model_key: modelKey, ...(modelKey === value.fallback_model_key ? { fallback_model_key: null } : {}) });
    }}><option value="">{chatOptionsForAccount.length ? t('채팅 모델 선택') : t('선택 가능한 모델 없음')}</option>{chatOptionsForAccount.map((option) => <option key={option.key} value={option.key}>{option.label || option.key}</option>)}</Select></Field>
    <Field label={t('장애 시 보조 Provider')}><Select value={selectedFallbackAccountId} disabled={!fallbackAccounts.length} onChange={(e) => {
      const accountOptions = fallbackOptions.filter((option) => option.provider_account_id === e.target.value);
      update({ fallback_model_key: accountOptions[0]?.key || null });
    }}><option value="">{t('사용 안 함')}</option>{fallbackAccounts.map(([id, option]) => <option key={id} value={id}>{option.provider_account_alias || option.provider || id}</option>)}</Select></Field>
    <Field label={t('장애 시 보조 모델')}><Select value={String(value.fallback_model_key || '')} disabled={!selectedFallbackAccountId} onChange={(e) => update({ fallback_model_key: e.target.value || null })}><option value="">{t('사용 안 함')}</option>{fallbackOptionsForAccount.map((option) => <option key={option.key} value={option.key}>{option.label || option.key}</option>)}</Select></Field>
    <Field label={t('압축 Provider')}><Select value={selectedCompressionAccountId} disabled={!compressionAccounts.length} onChange={(e) => {
      const accountOptions = compressionOptions.filter((option) => option.provider_account_id === e.target.value);
      const modelKey = accountOptions[0]?.key || null;
      update({ compression_model_key: modelKey, ...(modelKey === value.compression_fallback_model_key ? { compression_fallback_model_key: null } : {}) });
    }}><option value="">{compressionAccounts.length ? t('Provider 계정 선택') : t('선택 가능한 모델 없음')}</option>{compressionAccounts.map(([id, option]) => <option key={id} value={id}>{option.provider_account_alias || option.provider || id}</option>)}</Select></Field>
    <Field label={t('압축 모델')}><Select value={String(value.compression_model_key || '')} disabled={!compressionOptionsForAccount.length} onChange={(e) => {
      const modelKey = e.target.value || null;
      update({ compression_model_key: modelKey, ...(modelKey === value.compression_fallback_model_key ? { compression_fallback_model_key: null } : {}) });
    }}><option value="">{compressionOptionsForAccount.length ? t('압축 모델 선택') : t('선택 가능한 모델 없음')}</option>{compressionOptionsForAccount.map((option) => <option key={option.key} value={option.key}>{option.label || option.key}</option>)}</Select></Field>
    <Field label={t('압축 폴백 모델')}><Select value={String(value.compression_fallback_model_key || '')} onChange={(e) => update({ compression_fallback_model_key: e.target.value || null })}>
      <option value="">{t('채팅 폴백 상속')}{inheritedCompressionFallback ? ` · ${inheritedCompressionFallback.label || inheritedCompressionFallback.key}` : ` · ${t('설정 없음')}`}</option>
      {compressionFallbackOptions.map((option) => <option key={option.key} value={option.key}>{option.provider_account_alias ? `${option.provider_account_alias} · ` : ''}{option.label || option.key}</option>)}
    </Select><p className="context-muted">{t('전용 모델이 없으면 채팅 폴백을 재사용합니다. 파싱·검증·빈 요약도 실패로 보고 이 모델로 다시 압축합니다.')}</p></Field>
    <Field label={t('TTS 모델')}><Select value={String(value.default_tts_model_option_key || '')} onChange={(e) => update({ default_tts_model_option_key: e.target.value || null })}><option value="">{t('TTS 모델 미설정')}</option>{ttsModelOptions(value).map((option) => <option key={option.key} value={option.key}>{option.label || option.key}</option>)}</Select></Field>
    <Field label={t('대화 길이')}><Select value={String(value.response_length_preset || DEFAULT_RUNTIME_SETTING.response_length_preset)} onChange={(e) => update({ response_length_preset: e.target.value })}>{responseLengthPresets(value).map((preset) => <option key={preset.key} value={preset.key}>{localizedResponseLengthLabel(preset, t)}</option>)}</Select></Field>
    <Field label={t('압축 빈도')}><Select value={String(value.compression_interval_turns || DEFAULT_RUNTIME_SETTING.compression_interval_turns)} onChange={(e) => update({ compression_interval_turns: Number(e.target.value) })}>{compressionIntervalOptions(value).map((option) => <option key={String(option.turns)} value={String(option.turns)}>{localizedCompressionInterval(option, t).label}</option>)}</Select></Field>
    <Field label={t('민감 콘텐츠 필터')}><Select value={String(value.safety_preset || 'medium')} onChange={(e) => update({ safety_preset: e.target.value })}><option value="high">{t('높음 · 제한 강함')}</option><option value="medium">{t('보통 · 기본 RP')}</option><option value="low">{t('낮음 · provider 허용 범위')}</option></Select><p className="context-muted">{t('개인 로컬 채팅의 모델 응답 필터에만 적용됩니다. 마켓 검수 기준과는 별개입니다.')}</p></Field>
  </div>;
}
function RuntimeSettingsPanel({ setting, onChange, onSave, saving, title }: RuntimeSettingsPanelProps) {
  const { t } = useI18n();
  const canSave = !!runtimeModelOptions(setting).length && !!compressionModelOptions(setting).length && !!setting?.model_key && !!setting?.compression_model_key;
  return <FormSection
    className="my-3"
    title={title || t('채팅 기본 설정')}
    action={<Button type="button" disabled={saving || !canSave} onClick={onSave}>{saving ? t('저장 중…') : t('기본값 저장')}</Button>}
    data-modernized="runtime 설정 flat form section"
  >
    <RuntimeSettingFields value={setting} onChange={onChange} />
    {!canSave ? <p className="context-muted">{t('Provider 계정을 연결하고 모델 목록을 불러온 뒤 채팅/압축 모델을 선택해주세요.')}</p> : null}
  </FormSection>;
}

function ChatSettingsBubble({ open, setting, onChange, onSave, saving, onClose, renderImages, onRenderImagesChange, ttsEnabled, onTtsEnabledChange, onCompressNow }: ChatSettingsBubbleProps) {
  const { t } = useI18n();
  if (!open) return null;
  const imagesEnabled = renderImages !== false;
  return <Card className="chat-settings-popover" role="dialog" aria-label={t('채팅 설정')} data-modernized="runtime 설정 shadcn primitive marker">
    <CardHeader className="flex items-start justify-between gap-3">
      <CardTitle>{t('채팅 설정')}</CardTitle>
      <Button type="button" variant="ghost" size="icon" aria-label={t('채팅 설정 닫기')} onClick={onClose}>×</Button>
    </CardHeader>
    <CardContent>
      <RuntimeSettingFields value={setting} onChange={onChange} compact />
      <Switch checked={imagesEnabled} onChange={(e) => onRenderImagesChange?.(e.target.checked)} label={t('대화 이미지 표시')} />
      <Switch checked={!!ttsEnabled} onChange={(e) => onTtsEnabledChange?.(e.target.checked)} label={t('캐릭터 TTS 자동 생성')} />
      <div className="grid w-full grid-cols-[repeat(auto-fit,minmax(0,1fr))] gap-2.5 [&>button]:w-full [&>button]:min-w-0 [&>button]:justify-self-stretch"><Button type="button" disabled={saving} onClick={onSave}>{saving ? t('저장 중…') : t('이 방 설정 저장')}</Button><Button type="button" variant="ghost" disabled={saving} onClick={onCompressNow}>{t('지금 압축')}</Button></div>
    </CardContent>
  </Card>;
}

export { ChatSettingsBubble, RuntimeSettingFields, RuntimeSettingsPanel };
