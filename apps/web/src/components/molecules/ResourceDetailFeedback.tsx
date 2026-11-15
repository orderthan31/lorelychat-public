import type { ReactNode } from 'react';
import { useI18n } from '../../i18n/I18nProvider';
import { Button } from '../atoms';
import { ChatLoading } from './ChatLoading';
import './ux-feedback.scss';

export function ResourceDetailFeedback({ variant = 'form', error = '', onRetry, children }: {
  variant?: 'form' | 'chat';
  error?: string;
  onRetry: () => void;
  children?: ReactNode;
}) {
  const { t } = useI18n();
  if (error) return <section className="ux-detail-error" role="alert">
    <p>{error}</p><Button type="button" onClick={onRetry}>{t('재시도')}</Button>{children}
  </section>;
  if (variant === 'chat') return <ChatLoading />;
  return <section className={`ux-detail-skeleton ux-detail-skeleton--${variant}`} data-detail-skeleton={variant} role="status" aria-label={t('불러오는 중…')} aria-busy="true">
    <span className="ux-detail-loading-label">{t('불러오는 중…')}</span>
    <div aria-hidden="true" className="ux-detail-placeholder-layout">
      <div className="ux-detail-placeholder ux-detail-placeholder--heading" />
      <div className="ux-detail-placeholder ux-detail-placeholder--body" />
      <div className="ux-detail-placeholder ux-detail-placeholder--body" />
      <div className="ux-detail-placeholder ux-detail-placeholder--body" />
      <div className="ux-detail-placeholder ux-detail-placeholder--action" />
    </div>
  </section>;
}
