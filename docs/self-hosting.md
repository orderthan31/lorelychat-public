# Self-hosting

[한국어](self-hosting.ko.md) | [English](self-hosting.md) | [日本語](self-hosting.ja.md)

## First deployment

```bash
cp .env.example .env
docker compose config
docker compose up --build -d
docker compose ps
curl -fsS http://127.0.0.1:8080/api/ready
```

The first start creates synthetic demo characters and worlds. Runtime data is stored in Docker named volumes.

## Updating

```bash
git pull --ff-only
docker compose build --pull
docker compose up -d
docker compose ps
```

Back up persistent data before an update. Test the new images in a staging environment when the data matters.

## Backup

Inspect the actual Compose volume names first:

```bash
docker volume ls --filter label=com.docker.compose.project=lorechat
```

The important volumes are data, uploads, logs, and secrets. Use your host backup tooling or a reviewed temporary container to archive them. Store backups encrypted and outside the repository.

## Remote access

Do not expose the default deployment directly to the internet. Preferred options:

1. private VPN or private mesh network, or
2. authenticated TLS reverse proxy with request-size and timeout controls.

Only after that boundary is ready should you change:

```dotenv
LORECHAT_BIND_ADDRESS=0.0.0.0
```

## Resetting demo data

`docker compose down` preserves data. Removing named volumes permanently deletes the database, uploads, logs, and locally stored provider keys. Do not run a volume-removal command unless that destruction is intentional and backed up.
