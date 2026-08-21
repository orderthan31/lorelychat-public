# Security Policy

## Supported version

Security fixes target the latest commit on the default branch after the repository is published.

## Reporting a vulnerability

Do not include credentials, private conversations, character cards, databases, or exploit details in a public issue.

Use GitHub Private Vulnerability Reporting once it is enabled for the repository. If that channel is unavailable, contact the repository owner privately through the GitHub profile and wait for a private reporting channel.

Include:

- affected version or commit
- deployment topology
- minimal reproduction steps
- expected and actual behavior
- redacted logs only

## Deployment warning

Lorechat is designed for trusted single-user self-hosting and does not currently provide a complete public-internet authentication boundary.

- The Compose default binds to `127.0.0.1`.
- Do not change the bind address to `0.0.0.0` unless an authenticated TLS reverse proxy or private VPN controls access.
- The API container is not published directly; all browser traffic goes through the web container.
- Treat provider keys, the SQLite database, uploads, prompts, and logs as sensitive.
- Back up Docker volumes before upgrades.

## Secret handling

- Never commit `.env`, provider-secret JSON, databases, uploads, logs, or backups.
- Rotate any credential that has ever entered Git history or a public log.
- Provider credentials saved in the UI use a local file-backed secret store with mode `0600`; the dedicated volume is not encrypted by Lorechat.
- Use host-level disk encryption and access controls when the threat model requires encryption at rest.

## Container defaults

The supplied Compose setup uses non-root processes, read-only root filesystems, dropped Linux capabilities, `no-new-privileges`, private named volumes, and health checks. Review these controls again if you add bind mounts, devices, a browser renderer, or an outbound proxy.
