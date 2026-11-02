# Serving hardening and operator acceptance

This runbook is for an explicitly selected deployment. Source changes and synthetic tests do **not** prove that an existing service was restarted, that a host ingress policy is installed, or that a production backup exists. Do not operate the original and renewal deployments interchangeably.

## Durable generation and recovery

- A new logical send atomically accepts its source message, queued job and durable request receipt. Repeating the same request identity returns that acceptance; a conflicting payload or intentionally deleted source is not recreated. Explicit retry of a terminal job uses its expected state version and the existing source, not another user-message insert.
- Mock execution intent is captured only for newly accepted work and retained across regeneration, retries and restarts. Marked mock work uses the request-bound isolated runner, which suppresses paid TTS/compression tasks as well as provider calls. An incompatible runtime mode holds existing work instead of relabelling it; completed receipts remain readable without re-execution.
- Generation and outbox supervisors recover expired/orphaned ownership continuously, isolate iteration faults and expose actual supervisor state through readiness. Queue admission is fair across rooms, mock work cannot be claimed by real workers, and shutdown uses bounded drain/lease abandonment rather than pretending threads have stopped.
- Finalization checks the current owner, source and accepted reply set before atomically publishing messages, completion receipt and deterministic post-commit tasks. Replay verifies durable output identities; source/reply deletion fences prevent accidental resurrection. Deleting a whole room intentionally removes its related generation identities. History pagination uses a total `(created_at, id)` order so tied timestamps do not drop messages.
- The client discovers active work before its message snapshot, reconciles uncertain submission through the read-only request receipt, and converges terminal state through lifecycle SSE plus polling/readback. Old-server capability absence differs from a known job disappearing. Confirmed stop-tracking explicitly warns that server work may continue; it is not cancellation.

## Provider, parser and auxiliary boundaries

Typed non-retryable/authentication/policy errors do not trigger semantic repair, availability fallback or availability-circuit penalties. Only the selected, configured, reachable fallback participates in dispatch and context capacity, including compression preview. Invalid explicit controls fail without a send instead of being silently removed. A shared whole-task deadline and network-attempt budget span retries, fallback and repair. Synchronous SDK/bootstrap work cannot be forcibly preempted; deterministic elapsed-budget/no-send coverage is distinct from a hard real-time guarantee.

Provider-native prose and finish status remain authoritative; malformed optional usage is unknown rather than invented. Diagnostics retain safe error codes, attempt identity and count-only warnings, not provider warning text. Synthetic SDK transport coverage does not certify live credentials, provider acceptance, quality, latency or billing.

Ordinary protocol scanners advance monotonically. Server-owned render provenance cannot be supplied by generated legacy objects; malformed types, duplicate envelopes, invalid Unicode scalars and whole-turn repetition are rejected through the shared validation/persistence boundary. Preserve complete rich prose and optional action/private-character-thought parts; do not add mandatory per-bubble semantic fields. Existing bounded output-limit recovery remains analysis-only evidence and never becomes a public message/job badge.

Optional commands capture context before the await and release read transactions without committing transient replies. Each real auxiliary initial/repair request checks its own context/output reserve; a failed optional command preserves the complete primary output. Compression compares the captured source content/revision before applying and preserves the previous Arc on failure. TTS uses complete WAV staging/atomic publication plus content/voice freshness checks; completed shared caches are not automatically reclaimed.

External-memory defaults remain disabled. Bounded synthetic SDK/self-hosted paths and real-ID-only acknowledgement do **not** implement a durable remote update/delete lifecycle: note identity/revision outbox, tombstones and ambiguous-acceptance reconciliation remain prerequisites before lifecycle enablement. No real external sync or recall acceptance is claimed.

## Coherent source activation

Do not edit a source tree underneath a long-running Python backend and assume its imports stay frozen. Cached modules can be mixed with newly read dynamic imports, even when health/readiness is green. Prefer a verified immutable API/frontend source snapshot while a separate editable candidate is under review. Preserve runtime data/configuration and the selected dependency environment separately; isolate build/test Vite caches from the serving frontend.

Source acceptance requires a complete intended-file manifest, isolated full API and web gates, then independent SPEC and separate QUALITY review of that exact identity. Activate only the selected owner after idle/backup/restore checks; verify fresh PID/cwd, opened database, served frontend and protected user state. Roll back code, not newer conversation data. Do not automatically replay failed turns or claim a live provider success from import/MockTransport checks.

## Input/storage contracts

Requests are rejected, never silently clipped. Existing response DTOs and old authored rows are not retroactively subjected to ingress text budgets. Existing smaller field-specific limits remain authoritative.

| Boundary | Budget | Compatibility rationale |
|---|---:|---|
| Whole HTTP request | 10 MiB | Matches bundled nginx `client_max_body_size 10m`; requires `RequestBodyLimitMiddleware` installation before multipart parsing |
| Authored string | 262,144 Unicode characters | Storage safety ceiling, **not** model output/context policy; synthetic 57,000-character multilingual card/message is preserved byte-for-byte |
| Identifier/URL/name/title | 2,048 characters | Existing tighter limits such as world title 120 and command name 40 stay unchanged |
| Aggregate request text | 1,048,576 characters | Generous large-card/import allowance, independent of UTF-8 ingress bytes |
| Structure | depth 16; 8,192 nodes; ordinary list 1,024 | Iterative rejection before service writes; metadata has the same structure budget |
| Participants / command selections | 64 | Deliberately above normal interactive-room/enum counts; no deduplication/truncation used to hide oversized input |
| Image file | avatar/thumbnail 5 MiB; asset 8 MiB | Existing accepted byte ceilings retained; reads are at most 64 KiB and stop at limit+1 |
| Image geometry | 24 million pixels; side 16,384; 128 frames | Limits accidental rendering/decompression pressure, not generation aesthetics |

Repository seed compatibility measurement (JSON only, no live DB): 2 seed files; maximum persona 53, world_seed 303, opening_scene 92, description 73, title 16 characters. This is **not** a measurement of private authored cards. The substantially larger synthetic lossless fixture is the stronger large-card compatibility gate. Requests above these new storage budgets now receive 422; operators must review unusually large imports rather than bypass limits or truncate them.

Required persisted scalar fields reject explicit PATCH `null`; omission preserves the stored value. Nullable descriptive fields retain clear/reset semantics. Character/world collection fields whose response contracts require collections also reject null instead of writing an unreadable row. Whitespace-only required authored fields fail before service normalization.

`context_sources` is a new nullable command field backed by nullable JSON storage with a non-rewriting NULL migration. Unset/None inherits; explicit `[]` selects no sources, and name-only saves preserve that intent. Never reinterpret or rewrite historical `postprocess_context_options=[]`: older schema defaults and older consumers made that value ambiguous. New intent is not evidence about old intent.

## Upload publication and media lifecycle

Both upload paths use bounded reads, verify MIME against original container bytes, and run structural plus full native pixel decoding with pinned Pillow 13.0.0 before publication. Accepted PNG/JPEG/GIF/WebP inputs are verified and reopened for complete frame loading; GIF checks every frame. Invalid PNG filters/palettes, truncated entropy, forged dimensions and aggregate pixel/frame/decoded-byte excess are rejected. Animated PNG/WebP remain conservatively rejected.

Decoding requires Linux resource limits and anonymous memfd diagnostics. Each disposable worker has a 5-second wall allowance, 3/4-second CPU limits, a 768 MiB address-space cap and 128 MiB decoded-byte budget. At most two workers are admitted per API process, without an unbounded native-work queue. Address-space caps are not reserved RSS; size the container for the application/SDK plus both workers, and account for multiplied limits with multiple API processes. Async uploads do not block the event loop; timeout/cancellation kills and reaps the owned worker. Invalid or over-budget content receives sanitized 422; busy/spawn-unavailable receives 503 with Retry-After. OS scheduling/reaping overhead prevents a hard-real-time claim. This is resource isolation, not an OS capability sandbox.

Pillow's ordinary JPEG load can tolerate corrupt entropy warnings. Strict supplementary coefficient validation therefore uses the pinned Pillow core's public libjpeg 6.2 ABI, rejects warnings and fails closed on ABI/struct mismatch before ordinary pixel loading. Verify positive and malformed JPEGs inside each final runtime image; a successful host test does not certify another wheel/platform. No unrelated system codec is substituted, truncated-image loading is not enabled, and raw image/codec diagnostics never become HTTP or log content. Conservative rejection can exclude recoverable or unusually expensive valid files.

Asset target and form metadata are checked before publication. The default-avatar association and asset row now commit together. A failed write/registration removes only that request's exclusively created UUID file; rollback/read-back is used to avoid deleting bytes after a successful commit followed by a refresh/serialization error. If durable-reference verification fails, retain the file for inspection rather than risk deleting referenced data. Generic avatar/thumbnail uploads intentionally precede user association; abandoned uploads are inventory candidates, not automatically deleted.

`python -m app.ops.maintenance media --database /resolved/db --upload-root /resolved/private-uploads [--shared-root /resolved/shared-uploads]` is **read-only**. It inventories all text/JSON DB columns for upload URLs, including avatar, thumbnail and message/TTS metadata references. It skips symlinks and refuses overlapping private/shared roots. It never unlinks files, follows shared roots for deletion, or modifies historical rows. Absolute/percent-encoded URL paths are normalized conservatively. This is a candidate report, not an automatic deletion authorization: references in nonstandard external formats and concurrent publication require operator review. Store output privately; candidate names may be operationally sensitive.

Do not add a check-then-unlink API deletion path without a common publication/reference lock or quiesced maintenance transaction. An asset used as another avatar or by a message must survive removal of one association. TTS cache invalidation and cache files are a separate service-owner contract. Original shared-read-only assets are never cleanup targets.

## Pagination and resource cost

`paginated=true` character/world/command routes execute a SQL count and SQL ordered LIMIT/OFFSET query instead of reading all cards and slicing Python lists. Unicode `.lower()` search and literal substring semantics (`%` and `_` are not wildcards) are preserved with a connection-local SQLite function. Command bootstrap still runs before listing and preserves existing authored rows. Exact substring search/count can still scan the catalog in SQLite; this is not a claim of indexed full-text search or constant CPU cost. Legacy unpaginated list contracts remain available and unbounded for compatibility. Use the paginated UI path for interactive catalogs; a separate bounded/export contract is required before retiring legacy callers.

Usage ranking groups message/room counts in SQL rather than transferring every historical speaker ID. Offstage name matching streams only IDs/names in 256-row batches, retains only the requested best candidates, then loads the selected full cards. It still scans names to preserve alias behavior; no claim of constant catalog CPU cost is made.

## Diagnostics, capacity and retention

Final log formatting drops arbitrary exception values, chained cause values, cached traceback text and source lines; it retains exception classes and basename/function/line identity. Message/query/Bearer/JSON-secret patterns and private absolute paths are redacted. Managed app/error handlers and existing root stream handlers use this formatter; Python warnings are routed to a safe category-level message. Raw arbitrary `print()`/third-party stderr outside logging/warnings is not globally intercepted; provider wrappers must prevent unsafe serialization at source. Do not log raw provider replies, credentials, prompt text or user prose in warning messages.

Run from `apps/api` using that deployment's existing interpreter:

```sh
python -m app.ops.maintenance capacity --database /resolved/db --uploads /resolved/uploads
python -m app.ops.maintenance snapshots --database /resolved/db --days 7 --batch-size 500
# Explicit diagnostic retention maintenance only, after reviewing the dry run:
python -m app.ops.maintenance snapshots --database /resolved/db --days 7 --batch-size 500 --apply
```

Capacity reports are read-only: DB+WAL+SHM bytes, diagnostic row counts, queue status/depth/oldest age, expired snapshot count, upload bytes/file counts, disk free/total and a warning below either 1 GiB free or 10% free. Thresholds are conservative operator watermarks, **not** automatic write admission/deletion. No endpoint exposes this publicly. Filesystem scans may be expensive on large stores: run outside the request path. No live host-monitoring integration is implied.

Snapshot maintenance deletes at most 1,000 expired **diagnostic snapshots** in one transaction and defaults to dry-run; retention cannot be less than seven days. Authored conversations/messages, usage rows, jobs/events, failed-task evidence and media are never deleted by this helper. Existing foreground/startup snapshot pruning belongs to the snapshot-service owner; this new module does not silently schedule periodic housekeeping. To enforce expiry while idle, explicitly schedule bounded maintenance against the resolved deployment after backup/approval, and monitor successful runs. Seven days of retention is not a byte cap. Do not retry dead/old outbox rows automatically: inspect the safe status/age report, establish task idempotency/source revisions and obtain an explicit retry scope from the task owner. No retry control is implemented here.

## Complete backups and isolated restore acceptance

Never archive/copy only an active SQLite main `.db` file: committed data may be in WAL. The existing `scripts/sqlite_ops.py backup` uses SQLite online backup. `verify_restore()` now requires its checksum manifest and verifies both the source and separate writable restore bytes against it, plus full integrity/FK/required-table checks. Existing backups without a manifest require a separately reviewed provenance/checksum process; the verifier will not invent one.

For a coherent complete bundle:

1. Identify the active process/container, exact source SHA, resolved database, **private** upload root, shared fallback dependencies, provider secret root and deployment config. Do not print environment/credential values. Record permissions and runtime identity outside the public repository.
2. Stop admission and quiesce **all** DB/media/config/secret writers, including background workers and external operator jobs. Wait for bounded drain or verified lease abandonment; confirm ownership before claiming quiescence. The bundle tool's `--quiesced` flag is an operator assertion, not a process supervisor. Do not stop a live deployment without explicit maintenance approval.
3. From `apps/api`, create a new off-repo, protected destination:

```sh
python -m app.ops.backup_bundle create \
  --database /resolved/db --uploads /resolved/private-uploads \
  --secrets /resolved/secrets --config /resolved/deployment.env \
  --destination /encrypted-storage/new-unique-bundle --quiesced
python -m app.ops.backup_bundle verify /encrypted-storage/new-unique-bundle
```

4. The tool takes a WAL-safe DB backup and copies uploads/secrets/config; no source row/file is removed. It refuses symlinks in copied trees. Bundle directory/files are 0700/0600. The manifest includes relative filenames, byte lengths and SHA-256, never secret values. Protect/encrypt both bundle and manifest together; checksums detect corruption, **not malicious replacement/authenticity**. Store a trusted manifest digest in independent protected storage. Logs are optional diagnostic archives and are not required application state; preserve separately under their retention policy.
5. Verification checks the exact inventory/hashes, a separate writable restore's integrity/FKs/required tables/hash, DB upload-reference resolution and provider-secret-presence booleans. It never calls a provider or prints stored refs/values. A bundle relying on shared assets must contain independent private copies of all required shared references or have a separately authenticated immutable shared-media bundle; unresolved references fail acceptance. Do not mutate/copy the original shared assets just to make a drill pass.
6. Restore into a **new isolated** writable DB/uploads/secrets/config tree, never over a newer active DB. Keep the verified backup immutable; copy to restore targets. Reject archive path traversal/symlinks if transporting as an archive. Reapply 0700/0600 and correct runtime ownership (Compose UID/GID 10001); do not chmod an unrelated host tree.
7. Start only an isolated mock deployment on distinct loopback ports/volumes with all restored paths explicitly set before imports, paid-provider/network activity disabled, and no live services/dispatchers. Verify health/readiness, required schema/migrations, referenced media GETs and provider configuration as presence booleans; this is a distinct gate from offline bundle verification. Restore config is a record, not permission to reuse live external endpoints.
8. Compare critical row/message-content hashes and source version with the backup manifest; migration success is not a reversible data rollback. A code downgrade after schema/data migration needs separate compatibility proof. Never replace newer user turns with an older backup. Approve cutover explicitly, retain the prior complete bundle, then verify the actual service owner and resolved opened DB.
9. Schedule recurring restore drills in protected scratch storage; monitor age/integrity and missing-reference results. Pruning backup bundles requires explicit retention approval and paired manifest removal. This change did not create a live backup, configure a backup schedule or perform cutover.

## Shutdown and build acceptance

Shared settings retain existing product/protocol/model defaults:

- `LLM_TASK_DEADLINE_SECONDS=300` (whole-task transport owner consumes it);
- `DISPATCHER_SHUTDOWN_TIMEOUT_SECONDS=30` (core drain/abandonment owner consumes it);
- `TTS_TIMEOUT_SECONDS=120` (TTS owner consumes it).

They accept strictly positive values; explicit programmatic `None` and blank environment strings are rejected rather than disabling a deadline. Compose supplies explicit defaults. Container grace 45s exceeds uvicorn graceful 35s, which exceeds the default dispatcher budget 30s. If changing the dispatcher budget, increase both process/container budgets accordingly. Grace settings alone do not stop thread work: the core must hold runtime ownership until owned work drains or leases are safely relinquished. Durable async jobs can exceed the proxy's synchronous timeout; lifecycle events/polling must reconcile the durable result. These files do not assert SIGTERM recovery acceptance; isolated fault-injection belongs to the core lifecycle gate.

The web build uses Node 22 Debian/glibc and provisions Chromium/system libraries via the **lockfile-installed local** `./node_modules/.bin/playwright`, not an unconstrained `npx` download. All existing typecheck/test/build gates remain. The actual repository-root `.dockerignore` excludes sibling applications after re-opening the `apps/` parent, then admits only the web child and required nginx config. Web node_modules, dist, .env files, caches, runtime/secrets, reports and logs are excluded **after** source re-inclusion; a nested `.dockerignore` is not relied upon. Pillow is the sole added application dependency; existing API dependency versions/hashes and frontend dependency versions are preserved. The API context independently allowlists only production dependency manifests, entrypoint and app/scripts/seeds source. Recursive exclusions after source inclusion reject nested environments, caches, runtime/secret files and SQLite WAL/SHM companions; root-only suffix patterns are not sufficient.

Container acceptance requires a network-approved clean export build with lockfile-installed Chromium, all browser gates, actual COPY-context inventory and image-layer exclusion checks using synthetic sentinels. Static ignore-pattern assertions alone are insufficient: re-opening a parent directory can recursively include sibling runtime files. Verify API image imports/codec ABI and synthetic full-stack startup, health, uploads and configuration isolation separately from web image construction. Preserve image IDs and resolved base/layer provenance privately. Base image tags and OS package repositories are not digest-pinned reproducibility guarantees. Image build success is not live activation, a provider success or permission to retry previously denied container cleanup.
