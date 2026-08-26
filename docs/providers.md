# Provider Configuration

[한국어](providers.ko.md) | [English](providers.md) | [日本語](providers.ja.md)

## Mock mode

`LLM_MOCK=true` is the install-validation mode. It requires no provider account and does not make paid generation requests.

## UI-backed provider accounts

For normal use:

1. Start in mock mode.
2. Open **Settings → Model providers**.
3. Create a provider account with the correct protocol and base URL.
4. Save the API key.
5. Synchronize or manually add compatible chat model options.
6. Set the runtime default model.
7. Disable mock mode and recreate the API container.

Provider keys are stored as plaintext JSON values in `/secrets/provider_secrets.json`; database records reference them by opaque IDs. The file is mode `0600` where the filesystem supports POSIX permissions. Lorechat masks keys in API responses but does not encrypt the secret file itself.

## Local OpenAI-compatible endpoint

Inside Docker, `127.0.0.1` refers to the API container. To reach a model server on the Docker host, use:

```dotenv
LLM_BASE_URL=http://host.docker.internal:11434/v1
```

The Compose file maps `host.docker.internal` to the host gateway on Linux. Confirm that the model server is listening on an address reachable from Docker and is protected from untrusted networks.

## Privacy

A real provider receives the compiled prompt and conversation context required for generation. Do not configure a third-party provider for rooms containing data you are not authorized to send.
