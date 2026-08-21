import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/api/generationJobEvents.ts', import.meta.url), 'utf8');
const testableSource = source.replace(
  "import { API_BASE } from '../constants/domain';",
  "const API_BASE = '/api';",
);
const transpiled = ts.transpileModule(testableSource, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`;
const { buildGenerationJobEventsUrl, subscribeGenerationJobLifecycle, GenerationLifecycleSubscriptionClosedError } = await import(moduleUrl);

const relativeUrl = buildGenerationJobEventsUrl({
  conversationId: 'room / one',
  jobId: 'job?one',
  afterEventId: 42,
  apiBase: '/api',
  browserOrigin: 'https://lorechat.example',
});
assert.equal(
  relativeUrl.toString(),
  'https://lorechat.example/api/conversations/room%20%2F%20one/generation-jobs/job%3Fone/events?after_event_id=42',
);

const absoluteUrl = buildGenerationJobEventsUrl({
  conversationId: 'room-one',
  jobId: 'job-one',
  apiBase: 'https://api.example/v1/',
  browserOrigin: 'https://lorechat.example',
});
assert.equal(
  absoluteUrl.toString(),
  'https://api.example/v1/conversations/room-one/generation-jobs/job-one/events',
);

const originalWindow = globalThis.window;
const originalEventSource = globalThis.EventSource;
let eventSourceUrl = '';
class FakeEventSource {
  constructor(url) {
    eventSourceUrl = url;
  }
  addEventListener() {}
  close() {}
}

globalThis.window = {
  location: { origin: 'https://lorechat.example' },
  setTimeout,
  clearTimeout,
};
globalThis.EventSource = FakeEventSource;
try {
  const subscription = subscribeGenerationJobLifecycle({ conversationId: 'room-one', jobId: 'job-one' });
  assert.equal(
    eventSourceUrl,
    'https://lorechat.example/api/conversations/room-one/generation-jobs/job-one/events',
  );
  const closed = subscription.promise.catch((error) => error);
  subscription.close();
  assert.ok(await closed instanceof GenerationLifecycleSubscriptionClosedError);
} finally {
  globalThis.window = originalWindow;
  globalThis.EventSource = originalEventSource;
}

console.log('generation job lifecycle URL tests passed');
