import { useState, type Dispatch, type SetStateAction } from 'react';
import { Field, FormSection } from '../molecules';
import { TextareaWithExpand } from '../molecules';
import { TraitSliders, type TraitScores } from '../molecules';
import { CharacterAssetsPanel, type CharacterAsset, type CharacterAssetDraft } from './CharacterAssetsPanel';
import { avatarUrlFor } from '../../utils/assets';
import { arrayToLines, linesToArray } from '../../utils/text';
import { FALLBACK_TTS_MODELS, FALLBACK_TTS_VOICES } from '../../constants/domain';
import { Button } from '../atoms';
import { Input } from '../atoms';
import { Select } from '../atoms';
import { useI18n } from '../../i18n/I18nProvider';

export type TtsOption = {
  provider?: string;
  key: string;
  label?: string;
};

export type SpeechPreset = {
  id: string;
  title?: string;
  content?: string;
};

export type CharacterDraft = {
  id?: string;
  name?: string;
  description?: string;
  avatar_url?: string;
  persona?: string;
  appearance?: string;
  behavior_style?: string;
  speech_style?: string;
  emotional_rules?: string[];
  forbidden_rules?: string[];
  trait_scores?: TraitScores;
  tts_provider?: string;
  tts_model?: string;
  tts_voice_style?: string;
  tts_sample_text?: string;
};

type CharacterTab = 'basic' | 'prompt' | 'traits' | 'tts' | 'images';

export type CharacterFormProps = {
  draft: CharacterDraft;
  setDraft: Dispatch<SetStateAction<CharacterDraft>>;
  onSave: () => void;
  onDelete?: () => void;
  saving?: boolean;
  isNew?: boolean;
  speechPresets?: SpeechPreset[];
  applyPreset?: (field: keyof CharacterDraft, content?: string) => void;
  onAvatarUpload?: (file: File | undefined, update: <K extends keyof CharacterDraft>(key: K, value: CharacterDraft[K]) => void) => Promise<void> | void;
  ttsVoices?: TtsOption[];
  ttsModels?: TtsOption[];
  onTtsSample?: (draft: CharacterDraft) => void;
  characterId?: string | null;
  assets?: CharacterAsset[];
  assetsLoading?: boolean;
  assetDraft: CharacterAssetDraft;
  setAssetDraft: Dispatch<SetStateAction<CharacterAssetDraft>>;
  onCreateAsset?: () => void;
  onUploadAsset?: (file?: File) => Promise<void> | void;
  onPatchAsset?: (id: string, patch: Partial<CharacterAsset>) => void;
  onDeleteAsset?: (id: string) => void;
  onDefaultAsset?: (id: string) => void;
};

export function CharacterForm({ draft, setDraft, onSave, onDelete, saving = false, isNew = false, speechPresets = [], applyPreset, onAvatarUpload, ttsVoices = FALLBACK_TTS_VOICES, ttsModels = FALLBACK_TTS_MODELS, onTtsSample, characterId, assets = [], assetsLoading = false, assetDraft, setAssetDraft, onCreateAsset, onUploadAsset, onPatchAsset, onDeleteAsset, onDefaultAsset }: CharacterFormProps) {
  const { t } = useI18n();
  const [activeTab, setActiveTab] = useState<CharacterTab>('basic');
  const selectedTtsProvider = draft.tts_provider || 'supertonic';
  const availableTtsModels = ttsModels.filter((model) => (model.provider || 'supertonic') === selectedTtsProvider);
  const availableTtsVoices = ttsVoices.filter((voice) => (voice.provider || 'supertonic') === selectedTtsProvider);
  const selectedTtsModel = availableTtsModels.some((model) => model.key === draft.tts_model) ? draft.tts_model : (availableTtsModels[0]?.key || (selectedTtsProvider === 'gemini' ? 'gemini-3.1-flash-tts-preview' : 'supertonic-3'));
  const selectedTtsVoice = availableTtsVoices.some((voice) => voice.key === draft.tts_voice_style) ? draft.tts_voice_style : (availableTtsVoices[0]?.key || (selectedTtsProvider === 'gemini' ? 'Kore' : 'F1'));
  const update = <K extends keyof CharacterDraft>(key: K, value: CharacterDraft[K]) => setDraft((current) => ({ ...current, [key]: value }));
  const updateTtsProvider = (provider: string) => {
    const model = (ttsModels.find((item) => item.provider === provider) || {}).key || (provider === 'gemini' ? 'gemini-3.1-flash-tts-preview' : 'supertonic-3');
    const voice = (ttsVoices.find((item) => item.provider === provider) || {}).key || (provider === 'gemini' ? 'Kore' : 'F1');
    setDraft((current) => ({ ...current, tts_provider: provider, tts_model: model, tts_voice_style: voice }));
  };
  const tabs: Array<[CharacterTab, string]> = [
    ['basic', t('기본')], ['prompt', t('프롬프트')], ['traits', t('성향')], ['tts', t('TTS')], ['images', t('이미지')],
  ];
  return <form className="stack" data-modernized="캐릭터 폼 shadcn primitive marker" onSubmit={(event) => { event.preventDefault(); onSave(); }}>
    <div className="category-tabs character-tabs" aria-label={t('캐릭터 상세 탭')}>{tabs.map(([key, label]) => <Button type="button" key={key} variant={activeTab === key ? 'secondary' : 'ghost'} size="sm" onClick={() => setActiveTab(key)}>{label}</Button>)}</div>
    {activeTab === 'basic' && <section className="tab-panel stack"><Field label={t('이름')}><Input value={draft.name || ''} onChange={(e) => update('name', e.target.value)} placeholder={t('예: 아리아')} /></Field><Field label={t('설명')}><Input value={draft.description || ''} onChange={(e) => update('description', e.target.value)} placeholder={t('목록에 보일 짧은 설명')} /></Field><Field label={t('아바타 URL')}><Input value={draft.avatar_url || ''} onChange={(e) => update('avatar_url', e.target.value)} placeholder={t('https://... 또는 업로드 후 자동 입력')} /></Field><div className="avatar-upload-row"><img className="avatar-upload-preview" src={avatarUrlFor(draft)} alt={t('현재 캐릭터 프사 미리보기')} /><Field label={t('아바타 파일 업로드')}><Input type="file" accept="image/png,image/jpeg,image/webp,image/gif" disabled={saving} onChange={async (e) => { await onAvatarUpload?.(e.target.files?.[0] || undefined, update); e.target.value = ''; }} /></Field></div></section>}
    {activeTab === 'prompt' && <section className="tab-panel stack"><TextareaWithExpand label={t('페르소나')} rows={7} value={draft.persona || ''} onChange={(value) => update('persona', value)} placeholder={t('배경, 성격, 가치관, 내적 우선순위')} /><TextareaWithExpand label={t('외형 참조')} rows={5} value={draft.appearance || ''} onChange={(value) => update('appearance', value)} placeholder={t('나이, 체형, 헤어, 스타일링, 첫인상')} /><TextareaWithExpand label={t('행동스타일')} rows={5} value={draft.behavior_style || ''} onChange={(value) => update('behavior_style', value)} placeholder={t('행동, 거리감, 제스처, 주도성, 반응 방식')} /><TextareaWithExpand label={t('말투예시')} rows={5} value={draft.speech_style || ''} onChange={(value) => update('speech_style', value)} placeholder={t('말버릇, 자주 쓰는 표현, 감정별 말투와 호칭 변화')} /><div className="preset-inline"><Select defaultValue="" onChange={(e) => { if (e.target.value) { applyPreset?.('speech_style', e.target.value); e.target.value = ''; } }}><option value="">{t('말투예시 프리셋 불러오기')}</option>{speechPresets.map((preset) => <option key={preset.id} value={preset.content}>{preset.title}</option>)}</Select></div></section>}
    {activeTab === 'traits' && <section className="tab-panel stack"><TextareaWithExpand label={t('감정 규칙')} rows={4} value={arrayToLines(draft.emotional_rules)} onChange={(value) => update('emotional_rules', linesToArray(value))} placeholder={t('한 줄에 하나씩')} /><TraitSliders scores={draft.trait_scores} onChange={(scores) => update('trait_scores', scores)} /><TextareaWithExpand label={t('금지 규칙')} rows={4} value={arrayToLines(draft.forbidden_rules)} onChange={(value) => update('forbidden_rules', linesToArray(value))} placeholder={t('한 줄에 하나씩')} /></section>}
    {activeTab === 'tts' && <FormSection className="tab-panel" title={t('TTS 보이스')}><div className="grid grid-cols-1 gap-2 min-[720px]:grid-cols-[minmax(0,1.25fr)_minmax(160px,.75fr)]"><Field label={t('TTS 엔진')}><Select value={selectedTtsProvider} onChange={(e) => updateTtsProvider(e.target.value)}><option value="supertonic">Supertonic · {t('로컬')}</option><option value="gemini">Gemini · API</option></Select></Field><Field label={t('음성 모델')}><Select value={selectedTtsModel} onChange={(e) => update('tts_model', e.target.value)}>{availableTtsModels.map((model) => <option key={model.key} value={model.key}>{model.label || model.key}</option>)}</Select></Field><Field label={t('보이스')}><Select value={selectedTtsVoice} onChange={(e) => update('tts_voice_style', e.target.value)}>{availableTtsVoices.map((voice) => <option key={voice.key} value={voice.key}>{voice.label || voice.key}</option>)}</Select></Field></div><TextareaWithExpand label={t('보이스 샘플 대사')} rows={3} value={draft.tts_sample_text || ''} onChange={(value) => update('tts_sample_text', value)} placeholder={t('목소리를 확인할 짧은 대사')} /><div className="tts-sample-action-row"><Button type="button" variant="secondary" size="sm" disabled={saving || isNew} onClick={() => onTtsSample?.(draft)}>{t(isNew ? '저장 후 듣기' : '샘플 듣기')}</Button></div></FormSection>}
    {activeTab === 'images' && <FormSection className="tab-panel image-assets-tab" title={t('이미지/에셋')}>{isNew || !characterId ? <p className="context-muted">{t('캐릭터를 먼저 저장하세요.')}</p> : <CharacterAssetsPanel character={draft} assets={assets} loading={assetsLoading} saving={saving} draft={assetDraft} setDraft={setAssetDraft} onCreate={onCreateAsset} onUpload={onUploadAsset} onPatch={onPatchAsset} onDelete={onDeleteAsset} onDefault={onDefaultAsset} />}</FormSection>}
    <div className="form-action-row"><Button type="submit" disabled={saving}>{t(saving ? '저장 중…' : '저장')}</Button>{!isNew && <Button type="button" variant="destructive" onClick={onDelete}>{t('삭제')}</Button>}</div>
  </form>;
}
