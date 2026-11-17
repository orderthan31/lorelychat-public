import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Tests may run in a private source tree whose node_modules is read-only/shared.
export const testViteCacheDir = mkdtempSync(join(tmpdir(), 'lorechat-vite-cache-'));
process.once('exit', () => rmSync(testViteCacheDir, { recursive: true, force: true }));
