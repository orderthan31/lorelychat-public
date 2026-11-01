# LLM transport (experimental SDK opt-in)

The revised native-response and wire-control acceptance tests pass with synthetic
HTTP fixtures through the pinned SDK. Activation still requires independent
review and the separate dependency-risk decision below; this is not live-provider
certification. The default remains the legacy transport.

`LLM_TRANSPORT=legacy_http` is the compatibility default. `LLM_TRANSPORT=litellm`
selects the in-process, pinned `litellm-core==1.106.6` SDK. `LLM_MOCK=true`
continues to bypass both transports. No proxy, Router, additional model, response
cache, automatic SDK fallback, or new account UI is introduced. LangGraph,
application model selection, explicit configured failover, parsers and atomic
reply persistence remain application-owned.

## Installation and rollout

Use the reviewed hash locks, without full `litellm` or extras:

```sh
python -m pip install --require-hashes --only-binary=:all: -r apps/api/requirements.txt
# Development instead:
python -m pip install --require-hashes --only-binary=:all: -r apps/api/requirements-dev.txt
```

The Dockerfile uses the same wheel-only, hash-checked policy. Initial offline
runtime validation targets Linux x86_64 / CPython 3.12. Wheel hashes for other
platforms are allowed artifacts, not runtime certification. Package/source hash
checks do not establish native-binary reproducibility or prove supply-chain
safety. Production use with real BYOK credentials requires a separate dependency
risk decision and deployment review; local mocked-wire tests are not that gate.

After that review, set `LLM_TRANSPORT=litellm` in the deployment configuration,
verify the resolved Compose environment with `docker compose config`, then
rebuild/recreate the API during an idle, backed-up maintenance window. For a
non-Compose deployment, set the same environment variable on its API owner.
Do not enable a real provider merely to test configuration.

Rollback sets `LLM_TRANSPORT=legacy_http` and recreates/restarts that same API
owner. No schema migration or conversation restoration is required. Rollback
restores the legacy transport's existing route limitations too; it is not a
promise that every newly tested native SDK route works in legacy mode.

## Account and endpoint contracts

The selected account must supply its own key. Missing dynamic credentials never
inherit environment credentials or another account. Native defaults are explicit:

| Account type | SDK protocol / base |
| --- | --- |
| google / gemini | Gemini generateContent, `https://generativelanguage.googleapis.com/v1beta` |
| openai | Chat Completions, `https://api.openai.com/v1` |
| xai | xAI Chat Completions, `https://api.x.ai/v1` |
| openrouter | OpenRouter, `https://openrouter.ai/api/v1` |
| anthropic | Anthropic Messages, `https://api.anthropic.com` |
| openai_compatible | Chat Completions; explicit account base required |

A native account with a conflicting custom base is rejected before dispatch;
use an explicitly compatible account for a compatible gateway. In particular,
Google URLs formerly ignored by the legacy adapter do not silently become active.
Model slugs, including slashes, are not protocol selectors. Keyless operation is
limited to explicitly configured legacy compatible operation, not dynamic accounts.

## Preserved behavior and limits

- Application retry/circuit/task-budget ownership remains in place. Each SDK
  invocation is guarded below the SDK: at most one actual HTTP send, no redirects
  or environment proxies, no hidden retry/continuation. Only real sends consume
  the SDK path's network budget. Configuration rejection is not provider outage.
- The first native response is captured up to 8 MiB. Larger responses are rejected,
  never accepted after truncation. Native visible text, finish reason, reported
  model and usage remain authoritative. Missing/null or wrongly typed usage stays
  unknown, not SDK-estimated zero. Invalid metadata containers do not invalidate
  otherwise valid prose. Gemini visible candidate tokens and reasoning tokens remain
  separate; no totals are invented. Gemini's existing visible-text strip behavior
  is retained. Admission requires a successful native HTTP status and valid native
  text/finish grammar, independent of SDK normalization success or exception type.
  Error envelopes, malformed or unsupported content and non-2xx responses are
  never admitted. Valid Gemini empty/blocked replies retain compression diagnostics.
  A post-response SDK failure adds only a fixed private analysis code; no exception
  message or new public status is stored. Pre-capture SDK errors remain sanitized
  failures; transport errors retain application retry ownership.
- Output-limit responses go to the existing parser and bounded prefix proof; no
  SDK regeneration, concatenation or JSON repair. Recovery diagnostics remain in
  private analysis metadata, not visible reply/job markers.
- Existing sampling, thinking-OFF and cache-affinity capability gates remain
  narrow. Controls selected for serialization preserve explicit zero and
  `strict=false`; unsupported serialized SDK controls are rejected rather than
  silently dropped. Existing application-policy omissions remain separately
  recorded below. OFF is not universally translated to
  `reasoning_effort=none`; that can mean LOW rather than OFF on Gemini.
- Private transport analysis separates explicit requested controls, application
  controls/omissions, actual serialized wire controls and SDK-added defaults.
  Only allowlisted scalar controls are extracted at the HTTP guard, with native
  spellings mapped to canonical diagnostic names; messages, schemas, headers,
  keys and prompt/prose are not captured in these fields. For newer Gemini models,
  the SDK inserts `temperature=1.0` when the app omits sampling. This is an
  SDK-inserted default, not an explicit user choice or a wire omission. Existing
  capability-based application omissions (including suppressed explicit values)
  stay visible separately; no SDK request body is patched. Thinking-OFF claims
  are confirmed against captured scalar wire values, not merely requested intent.
- Structured-output schema conversion uses a copy; the application validator is
  authoritative. Tagged-text calls receive no JSON schema or extra schema prompt.
- `min_output_tokens` still supplies a maximum ceiling through the existing legacy
  mapping. SDK adoption does **not** provide universal minimum/target output length.
- SDK callbacks/telemetry and sensitive SDK logging are disabled; cost metadata is
  loaded locally. Request kwargs are constructed by the application, not forwarded
  from arbitrary browser input. Owned OpenAI/compatible clients enable response
  validation before SDK serialization, preventing malformed usage values from
  escaping in Pydantic warnings. The validated original native response still
  supplies usable prose and unknown usage; no global warning filter or extra
  request is introduced. `_strict_response_validation` is an internal OpenAI
  client option, not a stable public API: retain the exact dependency pins and
  privacy, ordinary-usage and concurrent-account regressions when upgrading.

Synthetic real-SDK wire tests cover the six account types, shared runtime budgets
across parse repair and explicitly configured fallback, zero-send circuit/budget
gates, combined overlays/auxiliary truncation and atomic application job prefix
finalization, including post-200 SDK failures. They do not demonstrate live provider acceptance, prose
quality, throughput, lower cost, or billing savings. Streaming and Responses API
migration are outside this transport change.
