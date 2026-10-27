# Renewal development isolation

`renewal` is the shared frontend/backend branch in this monorepo. Run it from a separate Git worktree so the ordinary development checkout can keep serving its existing users. This environment setup does not change the generated-response protocol.

## Isolation contract

- Keep independent Python virtual environments and frontend `node_modules` directories.
- Give the renewal web server its own port. `strictPort` stays enabled: a collision must fail, not silently move to another port.
- Keep the API on a separate loopback port; route both `/api` and `/uploads` through the renewal web server to that API.
- Use an independent SQLite database, provider-secret store, upload directory, log directory and runtime lock.
- Never launch the original development service or reuse its runtime lock from the renewal checkout.
- Do not switch the original checkout's branch or copy its uncommitted changes implicitly.

## Web configuration

The Vite configuration reads process environment variables:

- `LORECHAT_DEV_HOST`: listening address; default `0.0.0.0`.
- `LORECHAT_DEV_PORT`: web port; default `5173`, integer from 1 to 65535.
- `LORECHAT_DEV_API_TARGET`: API proxy target; default `http://127.0.0.1:8123`.

For an isolated development pair, use a free web port such as `5174` and a separate API target such as `http://127.0.0.1:8124`. Prefer binding the web listener to the private VPN address when that is the intended access path. API clients continue using same-origin `/api`; never put the developer's loopback API address into browser-facing configuration.

The npm `dev` and `preview` commands defer the listening address to Vite configuration so a hard-coded CLI `--host` cannot override isolation settings.

## Data import

A private renewal runtime may be created from a SQLite online backup of the current runtime. Do not copy a live WAL database with a plain filesystem copy. Preserve the immutable snapshot and validate the imported copy before startup.

For a recent-history import:

1. Fix a UTC cutoff at snapshot time minus the approved retention interval.
2. Trim messages only in the destination database; prune dependent message-asset links and orphan generation records there.
3. Never replay the source runtime's queued/outbox tasks. Keep imported unfinished work terminal and reset renewal-only usage/request diagnostics for new measurements.
4. Keep a persisted summary boundary only if its source message still exists in the same destination conversation. Reset an invalid summary/boundary in the copy rather than silently injecting an unprovable history gap. Do not invoke an LLM to rebuild imported history during setup.
5. Preserve character cards, worlds, commands, provider/model configuration, prompt settings, presets and asset metadata exactly.
6. Copy the provider-secret file to a separate private store. Validate reference-resolution parity without printing credentials; a dangling reference already in the source is not a newly valid credential.

Record counts, the UTC cutoff, integrity checks and copy verification in a private, Git-ignored manifest, not in this public source tree.

## Shared asset reads with isolated writes

`UPLOAD_ROOT` remains the renewal runtime's writable upload directory. Optional `SHARED_UPLOAD_ROOT` supplies a fallback for existing `/uploads` files. Look up renewal files first and shared files second. Shared files are served by the static GET/HEAD surface only; do not symlink or hardlink the two writable roots.

Keep the source directory read-only in the renewal service's filesystem view. New uploads and TTS files belong to `UPLOAD_ROOT`; copied asset records may continue referring to the existing `/uploads/...` URLs. Do not copy provider-secret files into a static root.

## Verification

- Run web tests, typecheck and build with renewal dependencies.
- Run backend tests with explicit synthetic database/upload/log/secret paths before importing the application; TestClient lifespan is not isolated by request dependency overrides alone.
- Check API `/ready`, web `/api/ready`, actual process working directories, opened database files and listener addresses.
- Check source and renewal databases return different expected history counts through their respective web proxies.
- Verify both a shared existing asset and a renewal-only upload route correctly, while the renewal-only upload is absent from the source runtime.
- Recheck the original service PIDs, endpoints, branch and preexisting file hashes. Source/history manifests and credentials must remain untracked.

Keep exact host paths, VPN addresses, copied-data manifests and installed service-unit instances in the private runtime workspace, not Git.
