// A room visit, not just a room ID, owns every asynchronous UI write.
export class RoomWorkCancelledError extends Error {
  constructor() { super('Room work cancelled'); }
}

export class RoomVisit {
  readonly controller = new AbortController();
  constructor(readonly roomId: string) {}
  get current() { return !this.controller.signal.aborted; }
  assert() { if (!this.current) throw new RoomWorkCancelledError(); }
  cancel() { this.controller.abort(); }
  async wait<T>(work: Promise<T>, timeoutMs = 30000): Promise<T> {
    this.assert();
    return new Promise<T>((resolve, reject) => {
      const signal = this.controller.signal;
      const cleanup = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); };
      const abort = () => { cleanup(); reject(new RoomWorkCancelledError()); };
      const timer = setTimeout(() => { cleanup(); reject(new Error('Room request timed out')); }, timeoutMs);
      signal.addEventListener('abort', abort, { once: true });
      work.then((value) => { cleanup(); if (this.current) resolve(value); else reject(new RoomWorkCancelledError()); }, (error) => { cleanup(); reject(error); });
    });
  }
  sleep(ms: number): Promise<void> {
    this.assert();
    return new Promise((resolve, reject) => {
      const signal = this.controller.signal;
      const abort = () => { clearTimeout(timer); reject(new RoomWorkCancelledError()); };
      const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
      signal.addEventListener('abort', abort, { once: true });
    });
  }
}

export class RoomWork {
  private visit = new RoomVisit('');
  enter(roomId: string) {
    if (this.visit.roomId !== roomId || !this.visit.current) {
      this.visit.cancel();
      this.visit = new RoomVisit(roomId);
    }
    return this.visit;
  }
  capture() { return this.visit; }
}

// Records survive route changes, while runners belong to one visit only.
// A late create response must retain the server job without writing another room.
export class GenerationOwners<T extends { conversationId: string }> {
  readonly pending = new Map<string, T>();
  private runners = new Map<T, { visit: RoomVisit; promise: Promise<void> }>();
  finish(job: T) { if (this.pending.get(job.conversationId) === job) this.pending.delete(job.conversationId); }
  run(job: T, visit: RoomVisit, work: () => Promise<void>): Promise<void> {
    const existing = this.runners.get(job);
    if (existing?.visit === visit) return existing.promise;
    const runner = { visit, promise: Promise.resolve() };
    this.runners.set(job, runner);
    runner.promise = Promise.resolve().then(() => { visit.assert(); return work(); }).finally(() => {
      if (this.runners.get(job) === runner) this.runners.delete(job);
    });
    return runner.promise;
  }
}
