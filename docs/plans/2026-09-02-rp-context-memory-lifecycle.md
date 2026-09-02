# RP Context Pressure and Memory Lifecycle Implementation Plan

**Goal:** Replace user-visible turn-count compression as the primary policy with model-aware context pressure, guarantee prompt coverage at generation time, and add lifecycle-aware RP memory that keeps closed scenes out of the active prompt unless explicitly recalled.

**Branch:** `feature/rp-context-memory-lifecycle` from `origin/dev@8cb709a9678ab2efd293327e84d0a37a7affe300`

**Safety / rollout:** Additive schema only. Existing `compression_interval_turns` remains an accepted deprecated rollback value but leaves the UI. `RP_CONTEXT_MANAGEMENT_MODE=legacy|shadow|automatic` controls rollout; default is `shadow` until transcript replay and deployment verification pass. Do not read or log message bodies, summaries, provider payloads, credentials, or extracted memory content in diagnostics.

**Non-goals:** No embeddings/vector database in this branch; no automatic relationship score resurrection; no changes to manual `CharacterMemory` user notes; no destructive rewrite of legacy `SceneState.summary`; no direct production DB mutation while developing.

---

## Architecture

### Memory levels

- **L0 raw complete turns:** contiguous suffix after the compression boundary. Source and generated reply bubbles remain one indivisible group.
- **L1 immutable episodes:** one source range per successful fold, closed by default, with evidence IDs and a unique range key.
- **L2 active rolling arc / scene:** existing `SceneState` stays the current-scene projection and backward-compatible prompt source.
- **L3 facts and threads:** typed lifecycle rows (`fact|thread`) with `current|open|resolved|superseded|expired`, validity timestamps, evidence and provenance.
- **Manual notes:** existing `CharacterMemory` remains user-authored only and is not mixed with automatic memory.

### Context policy

1. Resolve the selected chat model's context window. Prefer model metadata; otherwise use a clearly marked conservative fallback.
2. Derive the product working input budget as the minimum of model capacity minus completion/mandatory reserve and the existing room prompt budget (12k/16k/18k).
3. Estimate the same section set used by generation, then substitute the full uncompressed raw suffix for the legacy 8+2 selector.
4. `shadow`: persist sanitized pressure/coverage diagnostics but use legacy cadence.
5. `automatic`: enqueue at high watermark, catch up to low watermark, and perform bounded synchronous preflight catch-up when the next generation would have a raw coverage gap or cross the hard watermark.
6. If compression cannot restore coverage, do not silently omit middle history. Return a recoverable context-maintenance error and preserve raw messages.

### Retrieval policy

- Active lane: current facts and open threads only, filtered by conversation, branch, entity and validity.
- Historical lane: closed episodes only when the user explicitly requests retrospective recall, or a current thread explicitly depends on that episode.
- Every closed episode is rendered with a scope warning that it is historical and must not be continued unless reopened.
- Lexical retrieval is sufficient for the first safe rollout; vector retrieval is deferred until lifecycle gates and replay metrics are proven.

---

## Task 1: Model capability metadata and context-pressure primitives

**Files**
- Add `apps/api/app/services/context_management_service.py`
- Modify `apps/api/app/db/models.py`
- Modify `apps/api/app/schemas/model_providers.py`
- Modify `apps/api/app/services/model_provider_service.py`
- Modify `apps/api/app/db/session.py`
- Add `apps/api/tests/test_context_management_service.py`
- Extend `apps/api/tests/test_model_providers_api.py`

**Steps**
1. Add failing tests for model context metadata serialization, update, provider-metadata derivation and unknown-model fallback.
2. Add nullable `context_window_tokens` and `max_output_tokens` to `ModelOption`; migrate SQLite columns additively.
3. Extend provider/manual schemas and serializers without changing existing option keys.
4. Parse common provider metadata keys conservatively; never overwrite a manually configured non-null value with unknown data.
5. Implement pure dataclasses/functions:
   - `ContextCapacity`
   - `ContextPressureReport`
   - `resolve_model_context_capacity`
   - `estimate_visible_message_tokens`
   - `group_complete_turns`
   - `select_raw_tail_groups`
   - `estimate_context_pressure`
6. Keep diagnostics sanitized: IDs/counts/token estimates/status only.
7. Verify focused tests.

**Commit:** `feat: add model-aware context pressure primitives`

---

## Task 2: Complete-turn compression and coverage invariant

**Files**
- Modify `apps/api/app/services/conversation_service.py`
- Modify `apps/api/app/engine/prompts.py`
- Modify `apps/api/app/api/conversations.py`
- Modify `apps/api/app/services/post_commit_dispatcher.py`
- Extend `apps/api/tests/test_compression_pipeline.py`
- Extend `apps/api/tests/test_compression_contract.py`
- Extend `apps/api/tests/test_character_runtime.py`

**Steps**
1. Add failing tests that prove:
   - source + generated bubbles are never split at the protected-tail or 48-message boundary;
   - the prompt raw suffix is contiguous after `last_compression_source_message_id`;
   - no 8+2 anchor selector may silently omit a middle message in automatic mode;
   - an oversized single turn remains intact and produces an explicit hard-pressure state;
   - catch-up continues across multiple bounded batches until low watermark or no progress;
   - CAS/retry remains idempotent.
2. Replace count-first folding in automatic mode with complete-turn/token-tail selection. Keep legacy selector for rollback tests.
3. Change generation history selection so automatic mode receives the entire verified suffix; preserve legacy bounded selection only in legacy/shadow runtime behavior.
4. Replace `should_update_scene_orchestration_summary` primary decision with pressure/coverage report in automatic mode. The deprecated interval is a debounce/cost guard only.
5. Add a bounded pre-generation coverage check. Refresh `SceneState` and message suffix after each successful catch-up batch.
6. Add a typed recoverable API error when a hard gap remains after retries; never fabricate continuity.
7. Treat a background compression task as successful only when the boundary/revision actually advances, or when a fresh selector proves there is no foldable backlog. Persisted `last_compression_error` with unchanged coverage must requeue/fail the task instead of being marked `completed`.
8. Persist only sanitized counters/ratios to `SceneState` and task metadata.
9. Verify focused tests.

**Commit:** `feat: enforce token-pressure compression coverage`

---

## Task 3: Additive episode and lifecycle schema

**Files**
- Modify `apps/api/app/db/models.py`
- Modify `apps/api/app/db/session.py`
- Add `apps/api/app/schemas/conversation_memory.py`
- Add `apps/api/app/services/conversation_memory_service.py`
- Modify `apps/api/app/schemas/conversations.py`
- Modify `apps/api/app/api/conversations.py`
- Add `apps/api/tests/test_conversation_memory_lifecycle.py`

**Steps**
1. Add failing CRUD/lifecycle/CAS/isolation tests.
2. Add `ConversationEpisode`:
   - conversation/branch
   - immutable source start/end/count
   - summary/outcome/entities/evidence
   - `closed|reopened`
   - token/version/revision timestamps
   - unique conversation+branch+source range
3. Add `ConversationMemoryItem`:
   - `fact|thread`
   - subject/entity tags/content
   - `current|open|resolved|superseded|expired`
   - `valid_from`, `valid_until`, `source_episode_id`, evidence IDs
   - `superseded_by_id`, confidence, extraction version, revision
4. Add sanitized context read models and revision-checked manual correction endpoints. Source ranges remain immutable.
5. Ensure conversation deletion cascades/explicitly deletes new rows.
6. Existing rooms need no destructive backfill. Mark coverage as `legacy_uncertain` until new dual-write artifacts establish reliable ranges.
7. Verify focused tests.

**Commit:** `feat: add lifecycle-aware conversation memory schema`

---

## Task 4: Dual-write extraction and atomic artifact persistence

**Files**
- Modify `apps/api/app/engine/compression_graph.py`
- Modify `apps/api/app/services/conversation_service.py`
- Modify `apps/api/app/services/system_prompt_service.py`
- Modify `apps/api/app/services/conversation_memory_service.py`
- Extend `apps/api/tests/test_compression_pipeline.py`
- Extend `apps/api/tests/test_compression_contract.py`

**Steps**
1. Add failing tests for strict extraction validation, evidence rejection, closed-by-default episode creation, ADD/UPDATE/SUPERSEDE/NOOP, deduplication and rollback when artifact persistence fails.
2. Add an editable system-prompt setting for memory-artifact extraction.
3. Re-enable the compression graph's memory branch with a strict JSON contract:
   - one episode projection for the exact fold range;
   - zero or more fact/thread operations;
   - evidence IDs restricted to the folded source range;
   - no relationship scores, battle standings/results, ordinary dialogue, mood or temporary actions.
4. Validate operations before DB writes. Unknown targets/entities/evidence are rejected, not repaired by guessing.
5. Apply episode + lifecycle operations in the same transaction as the summary boundary CAS. If dual-write fails, the boundary does not advance.
6. Use deterministic source-range uniqueness for retry safety.
7. Verify focused tests.

**Commit:** `feat: dual-write episodic compression artifacts`

---

## Task 5: Lifecycle-aware retrieval and prompt routing

**Files**
- Modify `apps/api/app/services/conversation_memory_service.py`
- Modify `apps/api/app/engine/prompts.py`
- Modify `apps/api/app/engine/character_runtime.py`
- Modify `apps/api/app/services/context_preview_service.py`
- Modify `apps/api/app/api/conversations.py`
- Extend `apps/api/tests/test_prompt_harness.py`
- Extend `apps/api/tests/test_character_runtime.py`
- Extend `apps/api/tests/test_context_preview_api.py`

**Steps**
1. Add failing tests proving closed episodes are excluded from normal generation and only enter an explicitly labeled historical lane on recall/reopen/dependency.
2. Implement conservative Korean/English retrospective-intent detection.
3. Retrieve current facts/open threads first; filter by validity/entity/branch before ranking.
4. Retrieve at most a small bounded number of closed episodes by lexical overlap and recency only after the hard gate passes.
5. Add distinct prompt sections:
   - `Current facts and open threads`
   - `Historical episodes — closed`
6. Include provenance IDs/status in rendering, but no hidden/provider data.
7. Make context preview use the same retrieval and pressure estimator as production.
8. Verify focused tests.

**Commit:** `feat: gate historical episode recall by lifecycle`

---

## Task 6: Remove turn UI and add pressure/coverage observability

**Files**
- Modify `apps/api/app/schemas/runtime_settings.py`
- Modify `apps/api/app/services/runtime_settings_service.py`
- Modify `apps/api/app/schemas/conversations.py`
- Modify `apps/api/app/api/conversations.py`
- Modify `apps/web/src/components/organisms/RuntimeSettings.tsx`
- Modify `apps/web/src/components/organisms/ConversationInfoDrawer.tsx`
- Modify `apps/web/src/app/App.tsx`
- Modify `apps/web/tests/frontend-contract.test.mjs`
- Extend API runtime/context tests

**Steps**
1. Keep deprecated `compression_interval_turns` accepted by API/database for rollback, but remove editable turn-frequency options from web payload and UI.
2. Return mode, pressure ratio, projected/capacity/reserve tokens, coverage status/gap count, backlog groups, last success/error, and artifact counts from the conversation context endpoint.
3. Show `자동 컨텍스트 관리` state in the drawer:
   - shadow/automatic/legacy badge
   - normal/high/hard pressure
   - coverage healthy/legacy uncertain/gap
   - last compression/error and manual recovery action
4. Show active facts/open threads separately from historical closed episodes. Do not merge them with user notes.
5. Preserve existing Story Arc and manual note editing for rollback/correction.
6. Update frontend/i18n contracts, test, typecheck and build.

**Commit:** `feat: expose automatic context management status`

---

## Task 7: Migration, replay and final verification

**Files**
- Add `apps/api/tests/fixtures/rp_context_replay.py` or a privacy-safe synthetic fixture
- Add `apps/api/tests/test_rp_context_replay.py`
- Update docs in this plan with measured results

**Synthetic replay scenarios**
1. Variable-length turns that cross pressure between cadence boundaries.
2. Multi-bubble generation whose source/reply group crosses the old 48-message boundary.
3. Closed conflict followed by calm current scene; ensure no false active recall.
4. Explicit “그때 기억나?” recall; ensure historical lane appears with closed warning.
5. Resolved thread superseded by a new fact; ensure old fact excluded from active lane.
6. Legacy summary with no boundary, dangling boundary and large backlog.
7. Compression LLM unavailable and CAS conflict/retry.
8. One oversized indivisible turn.

### Live league-room compression quality gate

Synthetic replay is a regression safety net, not the final RP-quality verdict. At every compression-behavior milestone, discover the live room titled `리그` through the running API and use the shipped `POST /conversations/{id}/compress-now` path. Do not hard-code a stale room ID in source.

1. Before each call, capture content-free metadata from `/context` and `/compression-preview`: revision, boundary, error state, summary chars/lines, total backlog and next fold-batch count.
2. Inspect the exact folded source and returned Arc ephemerally for scoring, but never write raw messages, Arc text, personal names, provider payloads or credentials to Git, QA artifacts or chat reports.
3. POST exactly once per scored run, then verify API read-back: revision/boundary advance, error state, next batch and structural Arc metrics.
4. Score every result out of 100 with the fixed rubric:
   - factual/key-event preservation 25
   - chronology/causality 15
   - official league ledger contamination avoidance 15
   - active-vs-closed lifecycle distinction 15
   - next-turn continuity usefulness 10
   - compression density/no raw copy 10
   - Rolling Story Arc structure 5
   - revision/boundary operational health 5
5. PASS requires `>= 85` and no critical flag. Invented winner/ranking, a closed event reactivated as current, uncovered folded source, malformed/blank Arc, or raw-copy leakage is an automatic failure.
6. Keep only a redacted score ledger outside Git: implementation commit, runtime build identity, provider/model key, pre/post revision, whether the boundary advanced, batch counts, numeric subscores, critical flags and short paraphrased defect codes.
7. Compare the current stable baseline with the feature-branch verification instance under the same room/model contract. Do not assume a working-tree edit is active; restart the intended verification service and verify `/ready`, `/health` and runtime build identity first.

**Commands**
```bash
cd apps/api
.venv/bin/python -m pytest -q

cd ../web
npm test
npm run typecheck
npm run build
npm audit --omit=dev
```

**Review gates**
- Requirements review against this plan.
- Code-quality/security/privacy review.
- Live league-room score is at least 85 with no critical flag.
- Inspect `git diff --check`, changed-file list and commit history.
- Push only the feature branch; do not merge or modify `dev`/`main`.

**Final commit:** fixes from review only, then push `origin/feature/rp-context-memory-lifecycle`.
