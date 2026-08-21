import { API_BASE } from '../constants/domain';

export type GenerationLifecycleStatus = 'queued' | 'running' | 'retrying' | 'completed' | 'failed' | 'cancelled';
export type GenerationLifecycleEvent = {
  event_id: number;
  job_id: string;
  conversation_id: string;
  state_version: number;
  status: GenerationLifecycleStatus;
  created_at: string;
  generated_message_ids?: string[];
};

export class GenerationLifecycleSubscriptionClosedError extends Error {
  constructor() {
    super('Generation lifecycle subscription closed');
    this.name = 'GenerationLifecycleSubscriptionClosedError';
  }
}

export type GenerationLifecycleSubscription = {
  promise: Promise<GenerationLifecycleEvent>;
  close: () => void;
};

type SubscribeOptions = {
  conversationId: string;
  jobId: string;
  afterEventId?: number | null;
  onStatus?: (event: GenerationLifecycleEvent) => void;
  maxConsecutiveErrors?: number;
  openTimeoutMs?: number;
};

type GenerationJobEventsUrlOptions = {
  conversationId: string;
  jobId: string;
  afterEventId?: number | null;
  apiBase?: string;
  browserOrigin?: string;
};

const LIFECYCLE_STATUSES: GenerationLifecycleStatus[] = [
  'queued',
  'running',
  'retrying',
  'completed',
  'failed',
  'cancelled',
];
const TERMINAL_STATUSES = new Set<GenerationLifecycleStatus>(['completed', 'failed', 'cancelled']);

export function buildGenerationJobEventsUrl({
  conversationId,
  jobId,
  afterEventId,
  apiBase = API_BASE,
  browserOrigin = window.location.origin,
}: GenerationJobEventsUrlOptions): URL {
  const normalizedApiBase = apiBase.replace(/\/+$/, '');
  const relativePath = `${normalizedApiBase}/conversations/${encodeURIComponent(conversationId)}/generation-jobs/${encodeURIComponent(jobId)}/events`;
  const url = new URL(relativePath, browserOrigin);
  if (afterEventId && afterEventId > 0) url.searchParams.set('after_event_id', String(afterEventId));
  return url;
}

export function subscribeGenerationJobLifecycle({
  conversationId,
  jobId,
  afterEventId,
  onStatus,
  maxConsecutiveErrors = 2,
  openTimeoutMs = 5000,
}: SubscribeOptions): GenerationLifecycleSubscription {
  const url = buildGenerationJobEventsUrl({ conversationId, jobId, afterEventId });

  let eventSource: EventSource | null = null;
  let settled = false;
  let rejectPromise: (reason?: unknown) => void = () => undefined;
  let consecutiveErrors = 0;
  let openTimer = 0;

  const cleanup = () => {
    if (openTimer) window.clearTimeout(openTimer);
    openTimer = 0;
    eventSource?.close();
    eventSource = null;
  };

  const promise = new Promise<GenerationLifecycleEvent>((resolve, reject) => {
    rejectPromise = reject;
    const fail = (error: Error) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
    };
    try {
      eventSource = new EventSource(url.toString());
    } catch (error) {
      fail(error instanceof Error ? error : new Error('Lifecycle SSE unavailable'));
      return;
    }
    openTimer = window.setTimeout(() => fail(new Error('Lifecycle SSE open timeout')), openTimeoutMs);
    eventSource.onopen = () => {
      consecutiveErrors = 0;
      if (openTimer) window.clearTimeout(openTimer);
      openTimer = 0;
    };
    const handleLifecycleEvent = (rawEvent: MessageEvent<string>) => {
      try {
        const event = JSON.parse(rawEvent.data) as GenerationLifecycleEvent;
        if (!LIFECYCLE_STATUSES.includes(event.status)) return;
        onStatus?.(event);
        if (!TERMINAL_STATUSES.has(event.status) || settled) return;
        settled = true;
        cleanup();
        resolve(event);
      } catch {
        // Ignore malformed frames and let durable reconnect/replay recover.
      }
    };
    for (const status of LIFECYCLE_STATUSES) eventSource.addEventListener(status, handleLifecycleEvent as EventListener);
    eventSource.onerror = () => {
      consecutiveErrors += 1;
      if (consecutiveErrors >= maxConsecutiveErrors) fail(new Error('Lifecycle SSE repeatedly disconnected'));
    };
  });

  return {
    promise,
    close: () => {
      if (settled) return;
      settled = true;
      cleanup();
      rejectPromise(new GenerationLifecycleSubscriptionClosedError());
    },
  };
}
