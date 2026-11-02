# Frontend behavior checks

Run `npm ci`, then `npx playwright install chromium` once on a fresh test host.
`npm test` runs the lifecycle, virtualization, contract and isolated browser
regressions. Run `npm run typecheck` and `npm run build` separately.

Focused browser runners include:
- `npm run test:freebody`: semantic rendering, editing and clipboard formatting.
- `node tests/message-selection.test.mjs`: selection toolbar, chronological and
  offscreen copy, touch/mouse gestures, draft restoration and translated layouts.
- `node tests/ui-consistency.test.mjs`: shared Button cascade/variants, native
  controls, focus/disabled states, measured theme contrast and single-copy success,
  insecure-HTTP fallback, failures and stale feedback.
- `node tests/message-actions.test.mjs`: the production message-action/selection
  hooks under deferred confirmations and transports, room navigation/re-entry,
  readiness, stale completions and busy ownership.
- `node tests/conversation-presentation.test.mjs`: the production typed chat view,
  composer and battle controls, including multiline/expanded draft restoration,
  commands, keyboard actions and callback forwarding.
- `node tests/hardening-client.test.mjs`: actual AST-extracted production owners,
  room-load failure, route-visit guards, same-key reconciliation, durable receipt
  lookup outside recent/active pages, explicit stop-tracking and CAS retry.
- `node tests/hardening-browser.test.mjs`: synthetic App navigation/reload,
  persisted failed-source confirmed DELETE, receipt errors/recovery, typed HTTP,
  unavailable fallback selections, malformed content and retained long-history copy.
  Artifacts default to a fresh `lorechat-hardening-web-*` directory in the host's
  temporary root; set `LORECHAT_WEB_TEST_ARTIFACT_DIR` to choose a fresh output
  directory explicitly. The runner prints its artifact directory. Do not reuse
  historical receipt directories when measuring a new revision.
- `node tests/command-output.test.mjs`: real command form output/context selectors;
  name-only saves preserve legacy/inherited/explicit-empty authored contexts,
  while context checkbox edits send the new `context_sources` field.

Browser suites start temporary loopback Vite listeners on OS-assigned ports and
close them in `finally`. They render real components with production styles,
without loading application environment files or proxying to a live API.
Unexpected origins and requests are blocked. Where needed, serializer assertions
use explicitly synthetic intercepted API responses or deferred injected transports.
Clipboard success tests use real browser clipboard operations; deterministic
permission/failure cases are identified separately. They never call a model or
read/write a conversation database. Fixtures are not application routes or
production build entries.

Long-history tests retain all semantic rows for chronological selection/copy.
Mounted DOM is bounded and large rich content defaults to complete plain text,
but retained history and window offsets remain O(history). Desktop headless
measurements are not a mobile-memory ceiling or an equivalent-GC leak test.
