import { DEFAULT_TRAIT_SCORES, TRAIT_DEFINITIONS } from '../../constants/domain';
import { cn } from '../../lib/utils';
import { Slider } from '../atoms';
import { useI18n } from '../../i18n/I18nProvider';
import type { UiMessageKey } from '../../i18n/core';

export type TraitScores = Record<string, number>;

export type TraitSlidersProps = {
  scores?: TraitScores | null;
  onChange?: (scores: TraitScores) => void;
  readOnly?: boolean;
};

export function TraitSliders({ scores = {}, onChange, readOnly = false }: TraitSlidersProps) {
  const { t } = useI18n();
  const merged = { ...DEFAULT_TRAIT_SCORES, ...(scores || {}) };
  return <section className={cn('grid gap-2.5 rounded-[18px] border border-border bg-white/[.04] p-3', readOnly && 'border-0 bg-transparent p-0')} aria-label={t('캐릭터 성향 점수')} data-modernized="성향 슬라이더 shadcn primitive marker">
    <div className="flex flex-wrap items-baseline justify-between gap-2"><strong className="text-foreground">{t('성향 점수')}</strong><small className="text-xs text-muted">{t('1점 낮음 · 5점 강함')}</small></div>
    {TRAIT_DEFINITIONS.map((trait) => {
      const value = Number(merged[trait.key] || 3);
      return <label className={cn('grid grid-cols-[minmax(92px,1fr)_minmax(110px,1.2fr)_42px] items-center gap-[9px]', readOnly && 'grid-cols-[minmax(96px,1fr)_minmax(120px,1.25fr)_42px]')} key={trait.key}>
        <div className="grid gap-0.5"><span className="text-[13px] font-black text-foreground">{t(trait.label as UiMessageKey)}</span><small className="text-xs text-muted">{t(trait.hint as UiMessageKey)}</small></div>
        <Slider type="range" min="1" max="5" step="1" value={value} disabled={readOnly} readOnly={readOnly} className={readOnly ? 'cursor-default opacity-80' : ''} onChange={(event) => onChange?.({ ...merged, [trait.key]: Number(event.target.value) })} />
        <strong className="text-right text-[13px] text-accent">{value}/5</strong>
      </label>;
    })}
  </section>;
}
