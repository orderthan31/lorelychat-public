# Frontend behavior checks

Run `npm ci`, then `npx playwright install chromium` once on a fresh test host.
`npm test` includes the free-body browser regressions; `npm run test:freebody`
runs just that suite. Run `npm run typecheck` and `npm run build` separately.

The browser suite starts a temporary loopback Vite listener on an OS-assigned
port, renders the real BubbleList with production styles, and shuts it down in
`finally`. It does not load app environment files or proxy to an API. External
requests and all unmatched API requests are blocked. PATCH assertions use the
real resource serializer against explicitly synthetic intercepted responses.
It never calls a model or reads/writes a conversation database. The fixture is
not an application route or production build entry.
