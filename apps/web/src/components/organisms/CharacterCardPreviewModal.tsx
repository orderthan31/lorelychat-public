import { EMPTY_CHARACTER } from '../../constants/domain';
import { avatarUrlFor } from '../../utils/assets';
import { useUiStore } from '../../stores/uiStore';
import { Button } from '../atoms';
import { Card, CardContent, CardHeader, CardTitle } from '../atoms';
import { TraitSliders } from '../molecules';
import { useI18n } from '../../i18n/I18nProvider';
import { formatProfileImageAlt } from '../../i18n/core';

export type CharacterCardPreview = Partial<typeof EMPTY_CHARACTER> & {
  [key: string]: unknown;
};

export type CharacterCardPreviewModalProps = {
  character?: CharacterCardPreview | null;
  onClose?: () => void;
};

export function CharacterCardPreviewModal({ character, onClose }: CharacterCardPreviewModalProps) {
  const theme = useUiStore((state) => state.theme);
  const { locale, t } = useI18n();
  if (!character) return null;
  return <div className="avatar-modal" role="dialog" aria-modal="true" onClick={onClose} data-modernized="캐릭터 카드 미리보기 design-system primitive marker">
    <Card className="card-preview-modal" onClick={(event) => event.stopPropagation()} data-modernized="캐릭터 카드 미리보기 design-system modal card marker">
      <CardHeader className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <CardTitle>{t('카드 미리보기')}</CardTitle>
          <small className="text-xs text-muted">{character.name || t('새 캐릭터')}</small>
        </div>
        <Button type="button" variant="ghost" size="icon" className="drawer-close" aria-label={t('카드 미리보기 닫기')} onClick={onClose}>×</Button>
      </CardHeader>
      <CardContent>
        <div className="card-preview-hero">
          <img className="card-preview-avatar" src={avatarUrlFor(character, theme)} alt={formatProfileImageAlt(String(character.name || t('캐릭터')), locale)} />
          <div><strong>{character.name || t('새 캐릭터')}</strong><span>{character.description || t('설명 없음')}</span></div>
        </div>
        <div className="card-preview-grid">
          <section><h3>{t('목록 설명')}</h3><p>{character.description || t('저장된 설명 없음')}</p></section>
          <section><h3>{t('페르소나')}</h3><p>{character.persona || t('저장된 페르소나 없음')}</p></section>
          <section><h3>{t('외형 참조')}</h3><p>{character.appearance || t('외형 참조 없음')}</p></section>
          <section><h3>{t('행동 스타일')}</h3><p>{character.behavior_style || t('행동스타일 없음')}</p></section>
          <section><h3>{t('말투 예시')}</h3><p>{character.speech_style || t('말투예시 없음')}</p></section>
          <section className="card-preview-traits"><TraitSliders scores={character.trait_scores} readOnly /></section>
          <section><h3>{t('TTS 요약')}</h3><p>{character.tts_provider || 'supertonic'} · {character.tts_model || 'supertonic-3'} · {character.tts_voice_style || 'F1'}<br />{character.tts_sample_text || EMPTY_CHARACTER.tts_sample_text}</p></section>
        </div>
      </CardContent>
    </Card>
  </div>;
}
