import {useEffect, useRef, type ReactNode} from 'react';
import {Button} from '../atoms';
import {useI18n} from '../../i18n/I18nProvider';

/** Preview is optional; the primary resource click always retains its original navigation. */
export function ResourceWorkspace({children, selection, selectionKey, onClear}: {
  children: ReactNode; selection?: ReactNode; selectionKey?: string; onClear?: () => void;
}) {
  const {t} = useI18n();
  const returnFocus = useRef<HTMLElement | null>(null);
  const wasSelected = useRef(false);
  useEffect(() => {
    if (selection) {
      const active = document.activeElement;
      if (active instanceof HTMLElement && active.matches('[data-resource-preview]')) returnFocus.current = active;
    } else if (wasSelected.current && returnFocus.current?.isConnected) {
      returnFocus.current.focus();
    }
    wasSelected.current = !!selection;
  }, [selectionKey, !!selection]);
  return <div className="resource-workspace" data-resource-workspace data-has-selection={!!selection}>
    <div className="resource-results">{children}</div>
    {selection && <aside className="resource-inspector" aria-label={t('선택 상세')}>
      <header><strong>{t('미리보기')}</strong><Button type="button" variant="ghost" size="icon" aria-label={t('닫기')} onClick={onClear}>×</Button></header>
      {selection}
    </aside>}
  </div>;
}
