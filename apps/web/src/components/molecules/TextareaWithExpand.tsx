import { useEffect, useState } from 'react';
import { Button } from '../atoms';
import { Card } from '../atoms';
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
  const [viewportHeight, setViewportHeight] = useState<number | null>(null);
  const text = String(value || '');
  useEffect(() => {
    if (!expanded) return undefined;
    const viewport = window.visualViewport;
    const update = () => setViewportHeight(Math.floor(viewport?.height || window.innerHeight || 640));
    update();
    viewport?.addEventListener('resize', update);
    viewport?.addEventListener('scroll', update);
    return () => { viewport?.removeEventListener('resize', update); viewport?.removeEventListener('scroll', update); };
  }, [expanded]);
  const change = (next: string) => onChange?.(next);
  const metrics = formatTextMetrics(text.length, Math.max(1, text.split('\n').length), locale);
  return <div className="expandable-field grid items-stretch gap-2 text-sm font-extrabold text-foreground" data-modernized="textarea 확장 shadcn primitive marker"><span>{label}</span><div className="expandable-textarea-wrap"><Textarea rows={rows} value={text} onChange={(e) => change(e.target.value)} placeholder={placeholder} aria-label={ariaLabel || label} /><Button type="button" variant="ghost" size="sm" className="textarea-expand-button" onClick={() => setExpanded(true)} aria-label={`${label} · ${t('크게 쓰기')}`}>↗ {t('크게')}</Button></div><div className="textarea-meta"><span>{metrics}</span></div>{expanded && <div className="text-editor-modal" role="dialog" aria-modal="true"><button type="button" className="text-editor-backdrop" aria-label={t('닫기')} onClick={() => setExpanded(false)} /><Card className="text-editor-card" style={viewportHeight ? { maxHeight: `${Math.max(280, viewportHeight - 20)}px` } : undefined}><header className="text-editor-head"><strong>{editorTitle || label}</strong><Button type="button" variant="ghost" size="sm" onClick={() => setExpanded(false)}>{t('완료')}</Button></header><Textarea className="text-editor-textarea" autoFocus value={text} onChange={(e) => change(e.target.value)} placeholder={placeholder} aria-label={`${label} · ${t('전체 화면 편집')}`} /><footer className="text-editor-foot"><span>{metrics}</span><Button type="button" onClick={() => setExpanded(false)}>{t('적용')}</Button></footer></Card></div>}</div>;
}
