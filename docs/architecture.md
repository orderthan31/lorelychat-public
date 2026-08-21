# Architecture

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

## Startup

1. The API entrypoint creates synthetic demo data once per data volume.
2. FastAPI initializes and migrates the SQLite schema.
3. Default system prompts and chat commands are created.
4. In mock mode, background model dispatchers stay disabled.
5. The web service starts only after `/ready` reports healthy.

## Trust boundaries

- Browser input and uploaded files are untrusted.
- Provider requests cross an external data boundary when a real provider is selected.
- The provider-secret volume is sensitive local state, not source code.
- Nginx is a routing layer, not an authentication system.
- Public internet exposure requires an independent authenticated TLS boundary.
