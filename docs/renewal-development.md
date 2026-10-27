# Renewal development isolation

`renewal` is the shared frontend/backend branch in this monorepo. Run it from a separate Git worktree so the ordinary development checkout can keep serving its existing users. Runtime isolation and response-protocol selection are separate: the protocol defaults to the existing JSON contract unless explicitly enabled for a deployment.

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

## Response protocol opt-in

Set `CHAT_RESPONSE_PROTOCOL` in the API deployment environment, then restart only that isolated API owner:

- `legacy_json` (default): retains the existing JSON contract and legacy validation.
- `tagged_text`: requests plain text without a provider JSON response schema. A bubble has an explicit speaker alias and a natural body, for example `<m c="C1"><a>She turns.</a>Hello.<t>A private possibility.</t></m>`. `C1`, `C2`, etc. map exactly to the speaking room cast; `N` denotes narration.
- `free_body_json`: alternative minimal envelope `{"replies":[{"who":"C1","text":"Hello.","emotion":"calm"}]}`. Only `who` and `text` are required; the body uses the same optional semantic tags.

There is no extra model, embedding key or classifier call. Action and character-inner-thought tags are optional, can alternate with dialogue, and are never inferred from parentheses or Markdown emphasis. Inner thoughts here are fictional character text, not provider reasoning. Existing output-token and compression-quality settings are unchanged.

Ordered body parts and the original body are persisted alongside compatibility fields, but public history/compression use one ordered public representation without private character thoughts. HTML is not executed. Unknown or ambiguous speakers, incomplete envelopes, truncated responses and degenerate repetition loops fail/retry the whole turn rather than saving a partial result.

Existing messages retain their old representation. New free-body messages use one body editor plus optional emotion; edits reparse the body and invalidate TTS cache. TTS uses explicit spoken dialogue only, so action-only or thought-only bubbles do not create speech. Standalone command postprocessing remains the compatibility and repair path; a spoken-content rewrite invalidates the old ordered free-body view rather than displaying stale prose.

The free-body prompt keys live in the current version-controlled prompt registry and do not overwrite the legacy keys. To roll the generation protocol back, restore `CHAT_RESPONSE_PROTOCOL=legacy_json` and restart that API owner; already stored free-body messages remain readable and editable.

Local parser, mocked-provider HTTP, atomic API persistence and real-browser tests do not establish live model creativity, provider acceptance or billed-token savings. Those require a separately approved controlled comparison with the same room, model and length settings. See `apps/web/tests/README.md` for the offline browser regression workflow.

## Command request optimization

Commands declare optional `postprocess_block_types` in the command editor/API. An empty list preserves the generic legacy-compatible block contract; an explicit list limits the requested/accepted UI block types. Unknown types are invalid. For example, a broadcast-comments command can declare `comments`, `note`, and `table` without receiving instructions for polls, cards, or timelines. The editor's empty selection means all supported types, not a disabled panel.

The standalone path represents the current source and generated target prose once instead of repeating it in both the selected current-turn context and the target block. It still respects the configured context scope and preserves the original primary reply on command failure.

`CHAT_COMMAND_POSTPROCESS_MODE` is an independent API deployment opt-in:

- `separate` (default): main generation followed by standalone command processing.
- `combined_overlay`: eligible explicitly configured first/last overlays can be generated alongside the main free-body response. Main character prose and command-owned panels have separate validation boundaries, while the stored panel shape and existing renderer stay compatible.

Legacy JSON, all-bubbles rewrites and unsupported command contracts retain standalone behavior. Combined overlays support explicit `text`, `note`, `table`, `comments`, and `commentary` contracts; other block types, a generic empty contract, scoped recent-message/relationship-memory context, and turns mixing content rewrites remain standalone with a recorded reason. A missing or invalid auxiliary panel falls back only for that command; it must not regenerate valid character prose merely to repair a panel. Primary speaker/structure/repetition/truncation failures remain whole-turn failures. Tagged mode still sends no provider JSON response schema. Selected output-token limits and compression quality are unchanged.

The combined path can reduce duplicate input and request count, not eliminate the cost of writing the panel itself. A valid combined fixture can prove one provider request instead of two; malformed auxiliary output may still require the separate call. Local same-input character counts are not measured billed-token savings, equivalent creative quality, or a live-provider success rate. Do not count omitted character thoughts or shorter scenes as an optimization win.

For Docker Compose, `.env.example` documents both protocol flags and Compose forwards them explicitly. Direct API deployments must set the flags in their own environment. Roll back only the combined path with `CHAT_COMMAND_POSTPROCESS_MODE=separate`; existing messages and command settings remain readable. The command field is an additive database change, not permission to replace the database with an old backup over newer user messages.

## Verification

- Run web tests, typecheck and build with renewal dependencies.
- Run backend tests with explicit synthetic database/upload/log/secret paths before importing the application; TestClient lifespan is not isolated by request dependency overrides alone.
- Check API `/ready`, web `/api/ready`, actual process working directories, opened database files and listener addresses.
- Check source and renewal databases return different expected history counts through their respective web proxies.
- Verify both a shared existing asset and a renewal-only upload route correctly, while the renewal-only upload is absent from the source runtime.
- Recheck the original service PIDs, endpoints, branch and preexisting file hashes. Source/history manifests and credentials must remain untracked.

Keep exact host paths, VPN addresses, copied-data manifests and installed service-unit instances in the private runtime workspace, not Git.
