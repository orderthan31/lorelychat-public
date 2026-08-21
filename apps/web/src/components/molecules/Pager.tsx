import { pageCount } from '../../utils/pagination';
import { Button } from '../atoms';
import { useI18n } from '../../i18n/I18nProvider';
import { formatPagerSummary } from '../../i18n/core';

export type PagerProps = {
  page: number;
  total: number;
  onPage: (page: number) => void;
};

export function Pager({ page, total, onPage }: PagerProps) {
  const { locale, t } = useI18n();
  const pages = pageCount(total);
  return <div className="pager"><Button type="button" variant="ghost" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>{t('이전')}</Button><span>{formatPagerSummary(page, pages, total, locale)}</span><Button type="button" variant="ghost" size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>{t('다음')}</Button></div>;
}
