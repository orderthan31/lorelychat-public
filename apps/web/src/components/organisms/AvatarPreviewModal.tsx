import { Button } from '../atoms';
import { Card, CardContent, CardHeader, CardTitle } from '../atoms';
import { useI18n } from '../../i18n/I18nProvider';
import { formatProfileImageAlt } from '../../i18n/core';

export type AvatarPreviewCharacter = {
  name?: string;
  description?: string;
  persona?: string;
};

export type AvatarPreview = {
  character?: AvatarPreviewCharacter;
  src: string;
  emotion?: string;
};

export type AvatarPreviewModalProps = {
  preview?: AvatarPreview | null;
  onClose?: () => void;
};

export function AvatarPreviewModal({ preview, onClose }: AvatarPreviewModalProps) {
  const { locale, t } = useI18n();
  if (!preview) return null;
  const character = preview.character || {};
  return <div className="avatar-modal" role="dialog" aria-modal="true" onClick={onClose} data-modernized="아바타 미리보기 shadcn primitive marker">
    <Card className="avatar-preview-card" onClick={(event) => event.stopPropagation()} data-modernized="아바타 미리보기 shadcn modal card marker">
      <CardHeader className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <CardTitle>{character.name || t('캐릭터')}</CardTitle>
          <small className="text-xs text-muted">{t('현재기분:')} {preview.emotion || t('아직 없음')}</small>
        </div>
        <Button type="button" variant="ghost" size="icon" className="avatar-modal-close size-11 min-h-11 rounded-full border border-border bg-card/90 text-2xl leading-none text-foreground shadow-[0_10px_28px_rgba(0,0,0,.22)] backdrop-blur-md hover:bg-card hover:text-primary" aria-label={t('아바타 미리보기 닫기')} onClick={onClose}>×</Button>
      </CardHeader>
      <CardContent>
        <img className="avatar-preview-image" src={preview.src} alt={formatProfileImageAlt(character.name || t('캐릭터'), locale)} />
        <div className="avatar-character-info">
          <section><h3>{t('설명')}</h3><p>{character.description || t('저장된 설명 없음')}</p></section>
          <section><h3>{t('페르소나')}</h3><p>{character.persona || t('저장된 페르소나 없음')}</p></section>
        </div>
      </CardContent>
    </Card>
  </div>;
}
