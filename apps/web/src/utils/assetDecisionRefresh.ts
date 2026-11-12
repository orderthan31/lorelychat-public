import type { ChatMessage } from '../components/organisms/BubbleList';

type Message = ChatMessage & { conversation_id?: string; created_at?: string; generation_job_id?: string; reply_index?: number };
type Clock = { now(): number; setTimeout(callback: () => void, ms: number): ReturnType<typeof setTimeout>; clearTimeout(timer: ReturnType<typeof setTimeout>): void };
type Target = { source: string; deadline: number; attempts: number };
const sourceKey = (row: Message) => JSON.stringify([
  row.id, row.conversation_id, row.created_at, row.generation_job_id, row.reply_index,
  row.speaker_type, row.speaker_id, row.content, row.action, row.thought, row.emotion,
  row.metadata?.raw_body, row.metadata?.render_parts, row.metadata?.response_protocol, row.metadata?.command_blocks,
]);
const eligible = (row: Message, roomId: string) => Boolean(row.id && !row.id.startsWith('local_')
  && row.conversation_id === roomId && !row.failed && !row.is_typing && row.asset_decision_pending === true);

/** One instance per room visit. No generation/status ownership, page replacement or inserts. */
export function createAssetDecisionRefresh(options: {
  roomId: string;
  isCurrent(): boolean;
  read(ids: string[]): Promise<Message[]>;
  merge(update: (current: Message[]) => Message[]): void;
  clock?: Clock;
}) {
  const clock: Clock = options.clock ?? {
    now: () => Date.now(),
    // Browser host functions require their global receiver, not this Clock.
    setTimeout: (callback, ms) => globalThis.setTimeout(callback, ms),
    clearTimeout: timer => globalThis.clearTimeout(timer),
  };
  const targets = new Map<string, Target>();
  // Retired IDs cannot restart a fresh budget on rerender, edit, deletion or failed rows.
  const seen = new Set<string>();
  let loaded: Message[] = [];
  let visible = true;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let flight: symbol | undefined;
  let flightDeadline = 0;
  let visibilityEpoch = 0;
  const current = () => !stopped && options.isCurrent();
  const clearTimer = () => { if (timer !== undefined) clock.clearTimeout(timer); timer = undefined; };
  function prune() {
    const byId = new Map(loaded.map(row => [row.id, row]));
    for (const [id, target] of targets) {
      const row = byId.get(id);
      if (!row || !eligible(row, options.roomId) || sourceKey(row) !== target.source
        || clock.now() >= target.deadline || target.attempts >= 30) targets.delete(id);
    }
  }
  function schedule() {
    clearTimer();
    if (current() && visible && targets.size) timer = clock.setTimeout(tick, 500);
  }
  function tick() {
    timer = undefined;
    if (!current() || !visible) return;
    prune();
    if (!targets.size) { flight = undefined; return; }
    if (clock.now() >= flightDeadline) flight = undefined;
    if (!flight) {
      // Share endpoint-sized batches fairly across old loaded pages and new turns.
      const requested = new Map([...targets].sort((a, b) => a[1].attempts - b[1].attempts).slice(0, 100));
      const token = Symbol('asset-read');
      const epoch = visibilityEpoch;
      flight = token;
      flightDeadline = Math.max(...[...requested.values()].map(target => target.deadline));
      requested.forEach(target => target.attempts++);
      // Promise chaining handles synchronous throws as well as transport rejection.
      void Promise.resolve().then(() => {
        if (!current() || !visible || flight !== token) return [];
        return options.read([...requested.keys()]);
      }).then(rows => {
        if (!current() || !visible || flight !== token || !Array.isArray(rows)) return;
        const patches = new Map(rows.filter(row => {
          const target = requested.get(row.id || '');
          return target && targets.get(row.id!) === target && clock.now() < target.deadline
            && !row.failed && !row.is_typing && sourceKey(row) === target.source
            && typeof row.asset_decision_pending === 'boolean' && Array.isArray(row.assets);
        }).map(row => [row.id, row]));
        options.merge(existing => {
          // React can execute the updater after a route change or later local edit.
          if (!current() || !visible || visibilityEpoch !== epoch) return existing;
          let changed = false;
          const next = existing.map(row => {
            const patch = patches.get(row.id);
            const target = requested.get(row.id || '');
            if (!patch || !target || clock.now() >= target.deadline || !eligible(row, options.roomId)
              || sourceKey(row) !== target.source) return row;
            changed = true;
            return { ...row, assets: patch.assets, asset_decision_pending: patch.asset_decision_pending };
          });
          return changed ? next : existing;
        });
        for (const [id, patch] of patches) if (!patch.asset_decision_pending) targets.delete(id!);
      }).catch(() => {
        // Quiet bounded retry: auxiliary reads never own busy/status or failed rows.
      }).finally(() => {
        if (current() && flight === token) flight = undefined;
      });
    }
    schedule();
  }
  return {
    update(rows: Message[]) {
      if (!current()) return;
      loaded = rows;
      prune();
      for (const row of rows) {
        if (!eligible(row, options.roomId) || seen.has(row.id!)) continue;
        seen.add(row.id!);
        targets.set(row.id!, { source: sourceKey(row), deadline: clock.now() + 15000, attempts: 0 });
      }
      if (timer === undefined) schedule();
    },
    setVisible(value: boolean) {
      if (!current()) return;
      visible = value;
      visibilityEpoch++;
      clearTimer();
      if (!visible) flight = undefined; // Late hidden-tab results have no ownership.
      else { prune(); schedule(); } // Resume the original budget, never reset it.
    },
    stop() { stopped = true; flight = undefined; clearTimer(); targets.clear(); seen.clear(); loaded = []; },
  };
}
