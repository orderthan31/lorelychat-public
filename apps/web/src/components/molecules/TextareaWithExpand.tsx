import { IconButton, TextArea } from '@astryxdesign/core';
import { Maximize2 } from 'lucide-react';
import { useState } from 'react';
import { Button } from '../atoms';
import { ExpandedTextDialog } from './ExpandedTextDialog';
import { useI18n } from '../../i18n/I18nProvider';

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
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const text = String(value || '');
  const change = (next: string) => onChange?.(next);
  return <div className="expandable-field grid min-w-0 items-stretch gap-1 text-sm text-foreground" data-modernized="textarea 확장 design-system primitive marker">
    <div className="expandable-field-head">
      <span className="expandable-field-label font-semibold" aria-hidden="true">{label}</span>
      <IconButton type="button" variant="ghost" className="expandable-field-trigger" icon={<Maximize2 size={16} />}
        label={`${label} · ${t('크게 쓰기')}`} tooltip={t('크게 쓰기')} aria-haspopup="dialog" aria-expanded={expanded} onClick={() => setExpanded(true)} />
    </div>
    <TextArea label={ariaLabel || label} isLabelHidden rows={rows} value={text} onChange={change} placeholder={placeholder}
      width="100%" controlClassName="expandable-inline-control" />
    <ExpandedTextDialog open={expanded} onOpenChange={setExpanded} title={editorTitle || label}
      closeAction={<Button type="button" variant="ghost" onClick={() => setExpanded(false)}>{t('닫기')}</Button>}>
      <TextArea isLabelHidden label={`${label} · ${t('전체 화면 편집')}`} hasAutoFocus={expanded} value={text} onChange={change} placeholder={placeholder}
        width="100%" containerClassName="focus-editor-input" controlClassName="focus-editor-control" />
    </ExpandedTextDialog>
  </div>;
}
