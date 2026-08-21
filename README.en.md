# Lorechat

[한국어](README.md) | [English](README.en.md) | [日本語](README.ja.md)

Lorechat is a **self-hosted, mobile-first character chat runtime** for creating characters, defining worlds, and running conversations. This monorepo ships the React web client and FastAPI backend as one Docker Compose deployment.

> **License notice**
> Lorechat is **source-available software**, not OSI-approved open source. Personal use and other permitted noncommercial purposes are governed by the [PolyForm Noncommercial License 1.0.0](LICENSE). Commercial use is not permitted without separate written permission from the licensor.

## Features

- Character cards and world-setting management
- One-to-one and multi-character chat rooms
- Multi-bubble responses with action, dialogue, thought, and emotion fields
- Persistent conversation context, scene state, relationships, and character memory
- Runtime provider/model configuration and deterministic mock mode
- SQLite persistence, file uploads, and optional TTS
- React + Nginx web container and FastAPI API behind one origin
- Synthetic demo characters and worlds on the first Docker startup

## Quick start

### Requirements

- Docker Engine
- Docker Compose v2 (`docker compose`)

### Run

```bash
git clone <repository-url> lorechat
cd lorechat
cp .env.example .env
docker compose config
docker compose up --build -d
docker compose ps
```

Open <http://127.0.0.1:8080> in a browser.

The default is `LLM_MOCK=true`, so you can validate mock chat and compression flows without an API key. Separately enabled external features, such as TTS, may still make their own external requests.

### Health checks

```bash
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/api/ready
```

### Stop and restart

Stop without deleting data:

```bash
docker compose down
```

Start again:

```bash
docker compose up -d
```

## SQLite and persistent data

No separate database container is required. The API uses a SQLite file at `/data/lorechat.db`, stored in a Docker named volume.

| Compose volume key | Contents |
|---|---|
| `lorechat_data` | SQLite database and demo-seed state |
| `lorechat_uploads` | Uploaded files |
| `lorechat_logs` | Application logs |
| `lorechat_secrets` | Provider credentials saved through the UI |

Docker prefixes the actual volume name with the Compose project name. With the default `COMPOSE_PROJECT_NAME=lorechat`, the database volume is normally created as `lorechat_lorechat_data`.

A regular `docker compose down`, container recreation, or image update does not delete these volumes.

> **Warning:** `docker compose down -v` permanently deletes the SQLite database, uploads, logs, and locally stored provider keys. Run it only when an intentional reset is safe or a backup exists.

The first startup seeds two synthetic characters and starter worlds. To disable demo seeding for a new data volume, set:

```dotenv
LORECHAT_SEED_DEMO=false
```

## Connect a real model

1. Validate the deployment with `LLM_MOCK=true`.
2. Open **Settings → Model Settings** (`설정 → 모델 설정` in the current UI).
3. Add a provider account and API key.
4. Synchronize its model list or add a compatible model manually.
5. Select the runtime default model.
6. Disable mock mode in `.env`:

```dotenv
LLM_MOCK=false
```

7. Recreate the API container:

```bash
docker compose up -d --force-recreate api
```

Provider keys saved through the UI are stored as plaintext JSON values in `/secrets/provider_secrets.json`; the database references them by opaque IDs. The file is created with mode `0600` on POSIX filesystems, but Lorechat does not encrypt it. It is not committed to Git or baked into the Docker image. Use host disk encryption and access controls when required by your threat model.

Inside Docker, `127.0.0.1` refers to the API container itself. Use `host.docker.internal`, as shown in `.env.example`, to reach an OpenAI-compatible server running on the Docker host.

See [Provider configuration](docs/providers.md) and [Configuration](docs/configuration.md) for details.

## Core environment variables

Copy `.env.example` to `.env`. Never commit an `.env` file containing real credentials.

| Variable | Default | Purpose |
|---|---|---|
| `LORECHAT_BIND_ADDRESS` | `127.0.0.1` | Host address exposed by the web container |
| `LORECHAT_PORT` | `8080` | Host port for the web/API endpoint |
| `LLM_MOCK` | `true` | Use deterministic mock replies for chat and compression generation |
| `LORECHAT_SEED_DEMO` | `true` | Seed synthetic demo records in a new data volume |
| `CORS_ORIGINS` | `http://localhost:8080,http://127.0.0.1:8080` | Allowed origins for direct API development |
| `MEMORY_PROVIDER` | `local` | Select the memory provider |
| `MEM0_ENABLED` | `false` | Base feature flag for external memory; this value alone does not enable reads or writes |

Compose fixes the container-side `DATABASE_URL`, `UPLOAD_ROOT`, `LOG_DIR`, and `PROVIDER_SECRET_ROOT` to match the supplied volume layout. The supplied Compose file does not pass `MEM0_READ_ENABLED` or `MEM0_WRITE_ENABLED`, so external memory reads and writes remain disabled. A custom deployment must explicitly configure the provider dependency and read/write flags.

## Network and security

The default deployment binds only to `127.0.0.1`. Lorechat currently targets a trusted, single-user self-hosted environment and does not provide a complete public-internet authentication boundary.

Before enabling remote access, place it behind one of the following:

- a private VPN, or
- an authenticated TLS reverse proxy.

Do not set `LORECHAT_BIND_ADDRESS=0.0.0.0` without that protection. Treat provider keys, the SQLite database, conversations, prompts, uploads, and logs as sensitive data.

See [SECURITY.md](SECURITY.md) for the full deployment warning and vulnerability-reporting policy.

## Repository layout

```text
apps/web/        React + Vite web client
apps/api/        FastAPI + SQLModel API
apps/api/seeds/  Synthetic public demo data
deploy/          Nginx reverse-proxy configuration
docs/            Architecture, configuration, and self-hosting guides
docker-compose.yml
```

The browser connects to one Nginx endpoint. Requests under `/api/*` are proxied to the internal FastAPI service. API port `8123` is exposed only inside the Compose network.

## Local development and verification

### Web

```bash
cd apps/web
npm ci
npm run typecheck
npm run test
npm run build
npm audit --omit=dev
```

### API

Python 3.12 and [uv](https://docs.astral.sh/uv/) are recommended.

```bash
cd apps/api
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip sync requirements-dev.txt
python -m pytest -q
```

### Docker

```bash
docker compose config --quiet
docker compose build --pull
docker compose up -d --wait
```

## Documentation

- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Provider configuration](docs/providers.md)
- [Self-hosting](docs/self-hosting.md)
- [Security policy](SECURITY.md)
- [Contributing policy](CONTRIBUTING.md)

## Privacy and external services

- Runtime data stays in local Docker volumes by default.
- With `LLM_MOCK=true`, chat and compression generation make no external LLM call. Separately enabled features such as TTS may still make their own requests.
- A configured external provider receives the prompt and conversation context needed for its requests.
- External memory reads and writes are disabled in the supplied Compose deployment. Enabling them in a custom deployment may send selected memory content outside the host.
- Included demo records are synthetic and contain no production conversations, personas, or operational data.

## License

Copyright 2026 orderthan31.

Lorechat is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE). This repository does not grant commercial-use rights. Separate written permission from the licensor is required for commercial use, paid services, resale, commercial hosting, or business use.

Because of this restriction, Lorechat is a **source-available project**, not open source under the OSI definition.

## Contributing

Security reports and bug reports are welcome. Code contributions are not accepted until a contributor/relicensing agreement is published. See [CONTRIBUTING.md](CONTRIBUTING.md).
