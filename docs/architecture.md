# Architecture

[한국어](architecture.ko.md) | [English](architecture.md) | [日本語](architecture.ja.md)

## Runtime topology

```text
Browser
  |
  v
web (Nginx, :8080)
  |-- /, /assets/*  -> React static files
  `-- /api/*        -> api:8123/*
                         |
                         |-- /data/lorechat.db
                         |-- /uploads
                         |-- /logs
                         `-- /secrets/provider_secrets.json
```

The browser uses the same origin for the UI and API. Nginx strips the `/api` prefix before proxying to FastAPI. API port `8123` is only exposed inside the Compose network.

## Components

- `apps/web`: React, Vite, TanStack Query/Router, Zustand, and Tailwind
- `apps/api`: FastAPI, SQLModel/SQLAlchemy, SQLite, structured generation pipeline
- `deploy/nginx.conf`: SPA routing, API proxying, upload limit, and basic security headers
- Docker named volumes: separate persistence domains for DB, uploads, logs, and provider secrets

## Source ownership

- Web `app/` composes providers, layout and feature controllers. `router/` owns TanStack navigation and legacy URL mapping; there is only one history owner.
- `views/` owns page-specific state/actions; `components/` contains reusable UI. `agent/` is a bounded approval/recovery feature, not a second ordinary-CRUD commit path.
- Web `api/` owns HTTP contracts/query keys. Server lists belong to Query cache; editable drafts and in-flight generation/recovery state have separate owners. Pure shared defaults/normalizers do not depend on Views.
- API `main.py` composes lifecycle and public worker runners. `api/` owns HTTP admission, response/error mapping and lifecycle SSE; `services/` owns generation/post-commit execution and persistence orchestration.
- `engine/` contains prompt, budget, transport and compression execution components. `db/` owns ORM/session and shared SQL; domain services own catalog visibility/search/order. `schemas/` defines external contracts.
- Backend `agent/` owns staged plans, owner consent, CAS and receipts. `mcp/` adapts the same operations; `ops/` holds bounded operational I/O, locking, backup and recovery, not ordinary catalog policies.
- Keep provider I/O outside finalization transactions. Do not collapse request journals, unknown recovery, lease/CAS fences or nullable/default-suppression semantics to remove apparent duplication.

## Startup

1. When demo seeding is enabled and `/data/.demo-seeded` is absent, the container entrypoint runs `scripts.seed_demo`. That script initializes the schema before inserting missing named demo resources, then the entrypoint writes the volume marker.
2. FastAPI lifespan initializes the application and Agent schema, constructs the Agent service/optional MCP gateway, and replays bounded pending image cleanup.
3. It seeds default chat commands and recovers eligible durable jobs. Existing room/world associations and authored content are not replaced by a synthetic world.
4. Real mode starts generation and post-commit dispatchers with public service runners. Mock mode keeps those real dispatchers disabled and starts a separate supervised mock-only queue task; it does not mean there are no background tasks.
5. `/ready` becomes healthy after lifecycle setup. Compose starts the web service after API readiness; shutdown stops admission and drains owned work before releasing runtime ownership.

## Trust boundaries

- Browser input and uploaded files are untrusted.
- Provider requests cross an external data boundary when a real provider is selected.
- The provider-secret volume is sensitive local state, not source code.
- Nginx is a routing layer, not an authentication system.
- Public internet exposure requires an independent authenticated TLS boundary.
