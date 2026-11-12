import { API_BASE } from '../constants/domain';

export { imageUrlFor } from './assets';
export type { ImageVariant } from './assets';
import { isDerivedImageUrl, type ImageVariant } from './assets';
const api = API_BASE.replace(/\/$/, '');

/** Only shipped WebP, vectors and our own derived endpoints bypass preparation. */
export function isDirectImageUrl(value: string): boolean {
  return isDerivedImageUrl(value)
    || /^\/brand\/lorely-(?:symbol|horizontal|app-icon)(?:-(?:light|dark))?\.webp$/.test(value)
    || /^data:image\/svg\+xml[;,]/i.test(value);
}

type Entry = { promise: Promise<string>; expires: number; pending: boolean };
const memo = new Map<string, Entry>();
const MAX_ENTRIES = 128;
const MAX_PENDING = 32;
const MAX_KEY_CHARS = 16 * 1024 * 1024;
const SUCCESS_TTL = 5 * 60_000;
const FAILURE_TTL = 10_000;
export const IMAGE_PREPARE_TIMEOUT_MS = 15_000;

/** In-memory only. Shared requests survive one subscriber unmounting, but expire. */
export function prepareImageUrl(source: string, variant: ImageVariant): Promise<string> {
  const key = `${variant}:${source}`;
  const now = Date.now();
  for (const [oldKey, entry] of memo) if (!entry.pending && entry.expires <= now) memo.delete(oldKey);
  const cached = memo.get(key);
  if (cached) return cached.promise;
  let chars = key.length;
  let pending = 0;
  for (const [oldKey, entry] of memo) { chars += oldKey.length; if (entry.pending) pending++; }
  for (const [oldKey, entry] of memo) {
    if (memo.size < MAX_ENTRIES && chars <= MAX_KEY_CHARS) break;
    if (!entry.pending) { memo.delete(oldKey); chars -= oldKey.length; }
  }
  if (memo.size >= MAX_ENTRIES || pending >= MAX_PENDING || chars > MAX_KEY_CHARS) return Promise.reject(new Error('Image preparation unavailable'));
  const controller = new AbortController();
  const entry: Entry = { promise: Promise.resolve(''), expires: Infinity, pending: true };
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => { controller.abort(); reject(new Error('Image preparation timed out')); }, IMAGE_PREPARE_TIMEOUT_MS);
  });
  entry.promise = Promise.race([
    Promise.resolve().then(async () => {
      const response = await fetch(`${api}/images/prepare`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source, variant }), signal: controller.signal,
      });
      if (!response.ok) throw new Error('Image preparation failed');
      const result: unknown = await response.json();
      const path = (result as { image_url?: unknown })?.image_url;
      // Never trust a response to redirect the browser to the authored raster.
      if (typeof path !== 'string' || !/^\/images\/cache\/[a-zA-Z0-9_-]+\/[a-zA-Z0-9_.-]+\.webp$/.test(path)) throw new Error('Invalid prepared image');
      return `${api}${path}`;
    }), timeout,
  ]).then(value => { entry.expires = Date.now() + SUCCESS_TTL; return value; }, () => {
    entry.expires = Date.now() + FAILURE_TTL;
    throw new Error('Image preparation failed');
  }).finally(() => { clearTimeout(timer); entry.pending = false; });
  memo.set(key, entry);
  return entry.promise;
}
