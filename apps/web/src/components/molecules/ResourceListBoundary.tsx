import { Component, Suspense, type ErrorInfo, type ReactNode } from 'react';
import { QueryErrorResetBoundary } from '@tanstack/react-query';
import { Button, Skeleton } from '../atoms';
import { useI18n } from '../../i18n/I18nProvider';
import { cn } from '../../lib/utils';

export type ResourceSkeletonVariant = 'row' | 'media' | 'home';

function RowCardSkeleton() {
  return <article className="grid min-h-[98px] grid-cols-[72px_minmax(0,1fr)] items-center gap-3 rounded-xl border border-border bg-card p-3">
    <Skeleton className="aspect-square w-[72px] rounded-lg" />
    <span className="grid min-w-0 gap-2">
      <Skeleton className="h-4 w-2/5" />
      <Skeleton className="h-3.5 w-full" />
      <Skeleton className="h-3.5 w-3/4" />
    </span>
  </article>;
}

function MediaCardSkeleton() {
  return <article className="overflow-hidden rounded-xl border border-border bg-card">
    <Skeleton className="aspect-[12/5] w-full rounded-none sm:aspect-[16/9]" />
    <span className="grid gap-2 p-3">
      <Skeleton className="h-4 w-1/2" />
      <Skeleton className="h-3.5 w-4/5" />
      <span className="flex justify-between gap-3 pt-1"><Skeleton className="h-3 w-1/3" /><Skeleton className="h-3 w-12" /></span>
    </span>
  </article>;
}

function HomeRailSkeleton({ media = false }: { media?: boolean }) {
  return <section className="recommend-rail">
    <div className="recommend-rail-head"><Skeleton className="h-5 w-32" /><Skeleton className="h-8 w-16 rounded-lg" /></div>
    <div className="recommend-scroll pointer-events-none overflow-hidden" aria-hidden="true">
      <div className="flex w-max gap-2.5 pr-3">
        {Array.from({ length: 4 }, (_, index) => <article key={index} className="w-44 overflow-hidden rounded-xl border border-border bg-card sm:w-52">
          <Skeleton className={cn('w-full rounded-none', media ? 'aspect-[16/10]' : 'aspect-square')} />
          <span className="grid gap-2 p-3"><Skeleton className="h-4 w-2/3" /><Skeleton className="h-3 w-4/5" /></span>
        </article>)}
      </div>
    </div>
  </section>;
}

export function ResourceListSkeleton({ variant = 'row', count = 6 }: { variant?: ResourceSkeletonVariant; count?: number }) {
  const { t } = useI18n();
  if (variant === 'home') return <section className="page home-dashboard" role="status" aria-live="polite" aria-label={t('불러오는 중…')} aria-busy="true" data-resource-list-skeleton="home">
    <HomeRailSkeleton media />
    <HomeRailSkeleton />
    <HomeRailSkeleton media />
    <div className="home-quick-grid" aria-hidden="true">{Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="h-14 rounded-xl" />)}</div>
  </section>;

  const CardSkeleton = variant === 'media' ? MediaCardSkeleton : RowCardSkeleton;
  return <section className="panel page grid gap-3" role="status" aria-live="polite" aria-label={t('불러오는 중…')} aria-busy="true" data-resource-list-skeleton={variant}>
    <Skeleton className="h-11 w-full rounded-lg" />
    <div className="h-0.5 overflow-hidden rounded-full bg-surface-muted"><span className="skeleton-shimmer block h-full w-2/5 bg-primary/35" /></div>
    <div className="grid items-stretch gap-2 sm:grid-cols-2 xl:grid-cols-3">{Array.from({ length: count }, (_, index) => <CardSkeleton key={index} />)}</div>
  </section>;
}

export function ResourceListPendingBar({ pending }: { pending: boolean }) {
  return <div className="h-0.5 overflow-hidden rounded-full bg-surface-muted" aria-hidden="true">
    <span className={cn('skeleton-shimmer relative block h-full w-2/5 overflow-hidden bg-primary/45 transition-opacity', pending ? 'opacity-100' : 'opacity-0')} />
  </div>;
}

type ErrorBoundaryProps = {
  children: ReactNode;
  onReset: () => void;
  renderFallback: (retry: () => void) => ReactNode;
};

type ErrorBoundaryState = { error: Error | null };

class ResourceErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    // Rendering the localized recovery surface is intentional; transport details stay out of the UI.
  }

  retry = () => {
    this.props.onReset();
    this.setState({ error: null });
  };

  render() {
    return this.state.error ? this.props.renderFallback(this.retry) : this.props.children;
  }
}

export function ResourceListBoundary({ children, variant = 'row', count = 6 }: { children: ReactNode; variant?: ResourceSkeletonVariant; count?: number }) {
  const { t } = useI18n();
  return <QueryErrorResetBoundary>{({ reset }) => <ResourceErrorBoundary
    onReset={reset}
    renderFallback={(retry) => <div className="panel page grid min-h-48 place-items-center gap-3 rounded-xl border border-dashed border-border p-8 text-center" role="alert">
      <strong className="text-foreground">{t('요청 실패')}</strong>
      <Button type="button" variant="secondary" onClick={retry}>{t('재시도')}</Button>
    </div>}
  >
    <Suspense fallback={<ResourceListSkeleton variant={variant} count={count} />}>{children}</Suspense>
  </ResourceErrorBoundary>}</QueryErrorResetBoundary>;
}
