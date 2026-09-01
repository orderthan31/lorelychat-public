import { Component, Fragment, type ErrorInfo, type ReactNode } from 'react';

const CLIENT_ERROR_STORAGE_KEY = 'lorelychat.client-errors.v1';
const MAX_STORED_CLIENT_ERRORS = 10;
const DUPLICATE_ERROR_WINDOW_MS = 1000;

type ClientErrorKind = 'react-boundary' | 'window-error' | 'unhandled-rejection';

type ClientErrorDiagnostic = {
  occurred_at: string;
  kind: ClientErrorKind;
  name: string;
  message: string;
  stack?: string;
  component_stack?: string;
  pathname: string;
};

type AppErrorBoundaryProps = {
  children: ReactNode;
};

type AppErrorBoundaryState = {
  error: Error | null;
  resetKey: number;
};

function normalizeError(value: unknown): Error {
  if (value instanceof Error) return value;
  if (typeof value === 'string') return new Error(value);
  return new Error('Unknown client error');
}

function currentPathname(): string {
  return typeof window === 'undefined' ? '' : window.location.pathname;
}

function truncateDiagnosticText(value: string, maxLength: number): string {
  return value.length > maxLength ? `${value.slice(0, maxLength)}…` : value;
}

function storeClientError(kind: ClientErrorKind, value: unknown, componentStack?: string): void {
  if (typeof window === 'undefined') return;
  const error = normalizeError(value);
  const diagnostic: ClientErrorDiagnostic = {
    occurred_at: new Date().toISOString(),
    kind,
    name: error.name || 'Error',
    message: truncateDiagnosticText(error.message || 'Unknown client error', 1000),
    ...(error.stack ? { stack: truncateDiagnosticText(error.stack, 16000) } : {}),
    ...(componentStack ? { component_stack: truncateDiagnosticText(componentStack, 16000) } : {}),
    pathname: currentPathname(),
  };

  try {
    const stored = window.localStorage.getItem(CLIENT_ERROR_STORAGE_KEY);
    const parsed = stored ? JSON.parse(stored) : [];
    const previous = Array.isArray(parsed) ? parsed : [];
    const latest = previous.at(-1) as ClientErrorDiagnostic | undefined;
    const latestTimestamp = latest ? Date.parse(latest.occurred_at) : Number.NaN;
    const isDuplicate = latest
      && latest.kind === diagnostic.kind
      && latest.name === diagnostic.name
      && latest.message === diagnostic.message
      && latest.pathname === diagnostic.pathname
      && Number.isFinite(latestTimestamp)
      && Date.now() - latestTimestamp <= DUPLICATE_ERROR_WINDOW_MS;
    if (isDuplicate) return;
    window.localStorage.setItem(
      CLIENT_ERROR_STORAGE_KEY,
      JSON.stringify([...previous, diagnostic].slice(-MAX_STORED_CLIENT_ERRORS)),
    );
  } catch {
    // Error reporting must never cause another application failure.
  }
}

let globalErrorListenerUsers = 0;

function handleWindowError(event: ErrorEvent): void {
  storeClientError('window-error', event.error || event.message);
}

function handleUnhandledRejection(event: PromiseRejectionEvent): void {
  storeClientError('unhandled-rejection', event.reason);
}

function subscribeGlobalErrorListeners(): () => void {
  if (globalErrorListenerUsers === 0) {
    window.addEventListener('error', handleWindowError);
    window.addEventListener('unhandledrejection', handleUnhandledRejection);
  }
  globalErrorListenerUsers += 1;

  return () => {
    globalErrorListenerUsers = Math.max(0, globalErrorListenerUsers - 1);
    if (globalErrorListenerUsers === 0) {
      window.removeEventListener('error', handleWindowError);
      window.removeEventListener('unhandledrejection', handleUnhandledRejection);
    }
  };
}

export class AppErrorBoundary extends Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { error: null, resetKey: 0 };

  private unsubscribeGlobalErrorListeners: (() => void) | null = null;

  static getDerivedStateFromError(value: unknown): Partial<AppErrorBoundaryState> {
    return { error: normalizeError(value) };
  }

  componentDidMount(): void {
    this.unsubscribeGlobalErrorListeners = subscribeGlobalErrorListeners();
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    storeClientError('react-boundary', error, info.componentStack || undefined);
  }

  componentWillUnmount(): void {
    this.unsubscribeGlobalErrorListeners?.();
    this.unsubscribeGlobalErrorListeners = null;
  }

  private restartApp = (): void => {
    this.setState((state) => ({ error: null, resetKey: state.resetKey + 1 }));
  };

  private reloadPage = (): void => {
    window.location.reload();
  };

  render() {
    if (this.state.error) {
      return <main className="grid min-h-dvh place-items-center bg-background px-5 py-10 text-foreground">
        <section className="grid w-full max-w-lg gap-4 rounded-3xl border border-border bg-card p-6 shadow-xl" role="alert" aria-live="assertive">
          <div className="grid gap-2">
            <p className="m-0 text-sm font-black text-destructive">클라이언트 오류</p>
            <h1 className="m-0 text-xl font-black">앱을 표시하는 중 문제가 발생했습니다.</h1>
            <p className="m-0 text-sm leading-6 text-muted-foreground">최근 오류 정보는 대화 내용이나 요청 payload 없이 이 브라우저에만 저장했습니다.</p>
          </div>
          <details className="rounded-2xl border border-border bg-background p-3 text-xs text-muted-foreground">
            <summary className="cursor-pointer font-bold text-foreground">오류 정보</summary>
            <p className="mb-0 break-words whitespace-pre-wrap">{this.state.error.name}: {this.state.error.message}</p>
          </details>
          <div className="flex flex-wrap gap-2">
            <button className="min-h-11 rounded-xl bg-primary px-4 py-2 text-sm font-bold text-primary-foreground" type="button" onClick={this.restartApp}>앱 다시 열기</button>
            <button className="min-h-11 rounded-xl border border-border bg-background px-4 py-2 text-sm font-bold text-foreground" type="button" onClick={this.reloadPage}>페이지 다시 불러오기</button>
          </div>
        </section>
      </main>;
    }

    return <Fragment key={this.state.resetKey}>{this.props.children}</Fragment>;
  }
}
