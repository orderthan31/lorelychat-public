# Configuration

Copy `.env.example` to `.env`. Docker Compose reads the file automatically.

## Core values

| Variable | Default | Purpose |
|---|---:|---|
| `LORECHAT_BIND_ADDRESS` | `127.0.0.1` | Host address exposed by the web container |
| `LORECHAT_PORT` | `8080` | Host port for the web/API endpoint |
| `LLM_MOCK` | `true` | Use deterministic replies without external model calls |
| `LORECHAT_SEED_DEMO` | `true` | Seed synthetic characters/worlds once per data volume |
| `CORS_ORIGINS` | `http://localhost:8080,http://127.0.0.1:8080` | Allowed origins for direct API development |

The Compose file fixes container paths for `DATABASE_URL`, `UPLOAD_ROOT`, `LOG_DIR`, and `PROVIDER_SECRET_ROOT`. Do not override them unless you also redesign the volume layout.

## Provider environment values

The UI-backed provider/model settings are preferred. Legacy environment variables remain available for compatible deployments:

- `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`
- `CHAT_LLM_PROVIDER`, `CHAT_LLM_BASE_URL`, `CHAT_LLM_MODEL`, `CHAT_LLM_API_KEY`
- `GEMINI_API_KEY`

Empty values in `.env.example` are intentional. Never put real values in `.env.example` or commit `.env`.

## External memory

External semantic memory is disabled by default:

```dotenv
MEMORY_PROVIDER=local
MEM0_ENABLED=false
```

The supplied Compose file does not pass `MEM0_READ_ENABLED` or `MEM0_WRITE_ENABLED`, so setting `MEM0_ENABLED=true` alone does not enable external reads or writes. A custom deployment must explicitly provide the integration dependency and read/write flags. If enabled, selected memory content can leave the host; review the provider privacy policy and retention settings first.
