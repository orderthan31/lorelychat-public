# Opt-in harness comparisons

These deployment controls are experimental. They do not select a new model,
change the response protocol, reduce output length, or lower compression quality.
They require no new account, database migration, UI, embeddings, or recurring
model task. Offline mock tests are not evidence of better writing or lower bills.

| Environment variable | Default | Accepted values |
| --- | --- | --- |
| `CHAT_HISTORY_SERIALIZATION` | `transcript` | `transcript`, `native` |
| `CHAT_CONTEXT_POLICY` | `compact` | `compact`, `expanded`, `full_history` |
| `CHAT_CONTEXT_INPUT_BUDGET_TOKENS` | unset | Positive integer |

Invalid modes and nonpositive budgets fail settings validation. An unset target
retains the compact working target. A target is ignored under `compact`; under
`expanded` and `full_history` it is clamped to the narrowest reachable primary or
fallback context after reserving the unchanged actual output allowance and
mandatory capacity reserve. Unknown context metadata cannot authorize expansion.
There is no implicit use of a model's entire large context window.

The repository Compose file forwards these controls from its environment or
project `.env`. Leave the optional budget unset unless a positive target is
intended; it is inherited without substituting an empty string. Recreate only
the API service after deliberately changing deployment controls. The examples
and tests do not enable experiments on an existing deployment.

## Representation

`transcript` retains the current system-prompt public-history section. `native`
uses a separate prepared history channel: historical user dialogue and scene
directions are provider `user` **data**, and fictional cast/narration is provider
`assistant` data. Canonical labels and each historical bubble's complete public
text remain present, in chronological order; private thoughts are excluded from
shared history, not removed from generated character expression. Each historical
row has its own message boundary, including consecutive same-role rows. Current
source input remains separate and occurs once; character-authored sources retain
the existing user continuation request.

Selection and source boundaries are unchanged by the serializer. The prepared
channel and selected modes are frozen for retries. Snapshots record the actual
message array; comparison metadata contains only modes, IDs, a public-history
digest and heuristic capacity counts, not private prose.

## Retention policy

- `compact`: existing target, Arc, complete-turn grouping and compression policy.
- `expanded`: changes only the explicitly requested working input target. It
  retains existing compression quality, source boundary and complete-turn policy.
- `full_history`: loads the complete available prefix through the selected source,
  including messages before the saved Arc boundary. The main generation harness
  omits derived Arc and stale initial scene/opening fields using explicit render controls
  once history exists (a genuinely fresh room retains its authored opening),
  while retaining world, cards, persona, authored notes and official domain state.
  Saved Arc, cutoff and revision are not cleared. If required material cannot fit,
  generation fails with `context_maintenance_required` before main generation or
  automatic compression, retaining the source and producing no partial replies.

Full-history turns neither perform automatic catch-up nor enqueue compression.
Automatic compression task execution also recognizes a captured full-history
policy after a settings rollback. Manual `compress-now` remains available and
may deliberately change the saved Arc; it is not an automatic comparison step.
Asset/TTS tasks retain their existing ownership.

Explicit command contexts follow the same captured policy. Combined scene
supplements cannot restore scene fields intentionally omitted by the main
renderer. Standalone scene context retains fixed canon and the appropriate fresh
opening, without derived Arc or stale scene fields. An explicitly selected
`recent_messages` context uses the complete public source-bounded prefix under
`full_history`, excluding already represented source/target IDs and private
thoughts. Existing command eligibility and compact/expanded recent-context
selection remain unchanged.

Expanded/full-history standalone requests are admitted separately, after main
generation, using their actual target text, schema and unchanged output reserve.
Every semantic retry is checked again. If an auxiliary request cannot fit, its
typed capacity failure preserves the valid main reply; it does not claim a
provider call or usage for the blocked request. The main preview cannot predict
future generated target text and does not estimate that later auxiliary request.

## Preview and limits

Context preview labels native history outside the system prompt, exposes selected
history IDs/digest, working input, projected input, output/control reserves and
pressure warnings. Estimates use the existing character heuristic plus a fixed
native-message framing allowance. They are not a billed tokenizer or a guarantee
that a provider will accept an input. The initial request and semantic retries
remain guarded without shortening canon or output to make the input fit.

## Rollback

Set `CHAT_HISTORY_SERIALIZATION=transcript` and `CHAT_CONTEXT_POLICY=compact`;
unset the optional input target. Do not restore an old database over newer turns.
The separate response-protocol and command-postprocess flags are unaffected.
These examples are documentation only; no deployment is enabled by this change.
