# 0014: Optional OpenAI Responses reviewer

## Decision and wire contract

Implement `OpenAIReviewer` behind the existing DeepReviewer contract. The API uses an explicitly injected reviewer first, otherwise the environment factory. Defaults remain disabled and preserve NOT_CONFIGURED escalation. No broker, risk approval, tool implementation or automatic live-capital path is added.

POST only to `https://api.openai.com/v1/responses`. Use a named strict JSON schema in `text.format`, generated directly from `ReviewText`, with all properties required and additional properties forbidden. Accept only a completed response identifying the configured model, with exactly one completed assistant message containing one output_text item. Reasoning items may be present but are discarded. Refusals, tool calls, unexpected output items, incomplete responses and invalid structured text fail closed. See [official Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

Set `tools=[]`, `store=false` and a default `max_output_tokens=1024`. The adapter never follows a tool request, chains a conversation or runs background work. `store=false` disables response application-state storage; it does not establish a zero-data-retention agreement or override all provider data controls. See [Responses migration guidance](https://developers.openai.com/api/docs/guides/migrate-to-responses) and [data controls](https://developers.openai.com/api/docs/guides/your-data).

## Configuration and input

- `AI_QUANT_REVIEW_ENABLED` defaults to false. Only true/false are accepted, case insensitive.
- Enabling requires server-only `AI_QUANT_REVIEW_API_KEY` and `AI_QUANT_REVIEW_MODEL`.
- The model must be a dated GPT snapshot ID ending in YYYY-MM-DD, and the response must echo it. Moving aliases are rejected. No model is selected automatically; model availability, account access and schema support require credentialed verification.
- Startup validates configuration without making an API request. Authenticated classification invokes the reviewer only for a DEEP_REVIEW route with current source/parent evidence.
- Send only the selected simulated instrument's features/evidence references, source timestamp and compact fast classification/confidence/contradictions. Do not send task/operator identifiers, orders, positions, credentials or arbitrary operator text. Prompt instructions tell the model to treat input as evidence.
- The provider returns classification, confidence, evidence references, contradictions and bounded rationale. Snapshot identity, parent trace and validity timestamps are bound locally. Invented references and extra approval fields are rejected.

Confidence is a model self-assessment, not a calibrated probability or independently verified evidence of correctness. Completed review remains uncalibrated and always requires escalation. Rationale is untrusted archival text. The existing dispatcher and run store retain provenance and cannot issue order approvals.

## Failure and usage controls

Default request/response limits are 32/64 KiB. Stream response bytes with Accept-Encoding identity and reject other encodings. Use a 1.5-second HTTP phase timeout within the dispatcher's cooperative deadline, bounded by source expiry. No redirect, retry, URL override or ambient proxy is permitted. HTTP clients are created per call; no production latency or throughput is claimed.

ProviderAttempts locks per-instance request reservation, caps attempts at 100, opens after three consecutive failures for 30 seconds, and admits only one recovery probe. An older in-flight success cannot close a newer open circuit. Cancellation records failure and propagates. Process-local counters record typed input/output tokens and attempts without valid reported usage. Typed usage is counted before model/completion checks, so a rejected response can still consume known tokens. Unknown usage never means zero cost. State resets on restart; this is not durable monetary or fleet-wide cost governance.

GET /api/v1/decisions/provider includes sanitized reviewer identity, counters, allowance and circuit state in deep_review. No raw response, credential or underlying exception detail is exposed there. Failure leaves deep review pending and does not block independent deterministic paper risk evaluation.

## Verification and remaining work

Mock transport tests exercise request schema/storage/tool settings, local identity/expiry binding, model mismatch, incomplete/refused/tool output, negative usage, invalid confidence/evidence/extra fields, redirect/size rejection, request-cap concurrency, cancellation, circuit races and a single cooldown probe. A TestClient flow verifies review persistence, sanitized status and no orders. No paid or credentialed provider request was made.

Credentialed provider verification, held-out review evaluation, production secrets/access controls, durable monetary budget and the full multi-provider model gateway remain open. This is an implemented HTTP adapter, not a completed production release.

## Rollback

Revert the reviewer adapter and environment wiring; leave existing run and audit evidence intact. No database migration or dependency change is introduced. Existing injected reviewer interfaces and disabled-by-default behavior are retained.
