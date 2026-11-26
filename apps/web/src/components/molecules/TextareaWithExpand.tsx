import { useState } from 'react';
import { Button } from '../atoms';
import { ExpandedTextDialog } from './ExpandedTextDialog';
import { Textarea } from '../atoms';
import { useI18n } from '../../i18n/I18nProvider';
import { formatTextMetrics } from '../../i18n/core';

export type TextareaWithExpandProps = {
  label: string;
  value?: unknown;
  onChange?: (value: string) => void;
  rows?: number;
  placeholder?: string;
  ariaLabel?: string;
  editorTitle?: string;
};

export function TextareaWithExpand({ label, value, onChange, rows = 3, placeholder = '', ariaLabel, editorTitle }: TextareaWithExpandProps) {
  const { locale, t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const text = String(value || '');
  const change = (next: string) => onChange?.(next);
  const metrics = formatTextMetrics(text.length, Math.max(1, text.split('\n').length), locale);
  return <div className="expandable-field grid items-stretch gap-2 text-sm font-extrabold text-foreground" data-modernized="textarea 확장 design-system primitive marker">
    <span>{label}</span>
    <Textarea rows={rows} value={text} onChange={(e) => change(e.target.value)} placeholder={placeholder} aria-label={ariaLabel || label} />
    <div className="textarea-meta">
      <span>{metrics}</span>
      <Button type="button" variant="ghost" size="sm" onClick={() => setExpanded(true)} aria-label={`${label} · ${t('크게 쓰기')}`}>↗ {t('크게')}</Button>
    </div>
    <ExpandedTextDialog open={expanded} onOpenChange={setExpanded} title={editorTitle || label} actions={<>
      <span>{metrics}</span><Button type="button" onClick={() => setExpanded(false)}>{t('완료')}</Button>
    </>}>
      <Textarea className="text-editor-textarea" data-autofocus="true" value={text} onChange={(e) => change(e.target.value)} placeholder={placeholder} aria-label={`${label} · ${t('전체 화면 편집')}`} />
    </ExpandedTextDialog>
  </div>;
}
