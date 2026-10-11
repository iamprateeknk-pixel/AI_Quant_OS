# Advisory classification trace stages

Increment 0143, 2026-10-11. Development evidence, PAPER only.

## Operational questions

- Was time spent capturing the market snapshot, classifying, reviewing or archiving?
- Was a provider actually invoked after logical quota/owner admission?
- Did a provider exception or cancellation cause a fallback while safety remained available?
- Which original advisory envelopes belong to these observed stages?

The existing protected `POST /api/v1/paper/decisions/classify` now creates four
native SDK stages under its original fresh HTTP trace: `paper.market.snapshot`,
`paper.agent.classify`, `paper.agent.review` and `paper.agent.archive`.
The original response `X-Paper-Trace-Id` identifies that SDK trace when enabled.
Classification and review provider invocation spans are children of their
respective stage. Existing async `wait_for` preserves this parent context.

`paper.provider.classify` and `paper.provider.review` start only after the
original durable logical call reservation. They wrap the adapter's guarded
invocation plus the existing post-call authorization check. They are invocation
attempts, not proof of network dispatch, provider billing or success; adapter
spending/preflight/circuit checks may still deny actual HTTP. Logical admission
denial creates no invocation span or provider call. Default standalone wrappers
use `nullcontext` and do not create a telemetry runtime.

## Original identities and outcomes

Classification/archive stages record the original UUID advisory envelope ID.
Review records its own UUID, original parent-envelope UUID and allowlisted
review-status enum. Classification records DEEP_REVIEW or WAIT_FOR_DATA.
These are original advisory evidence IDs, not replacements for SDK trace IDs.
They link observed stages to existing retained envelopes without changing their
wire/audit schemas. No durable audit-to-SDK-trace binding is claimed.

Task, instrument, world-state reference, evidence refs, prompts, rationale,
provider/version strings, credentials and raw exceptions are omitted. Existing
fixed exception status has no exception events/stack/message. Timeout cancellation
finishes the original provider span with that fixed error; existing caller
fallback and advisory expiry rules remain authoritative.

## Verification

Actual SDK 1.45.0 spans are emitted by the original API with fixture classifier/
reviewer implementations, explicit owner binding and durable logical quotas.
Tests verify all seven spans' actual context/parent IDs, original UUID fields,
completed review, archived run and secret omission. Exhausted quotas permit zero
calls and no provider invocation spans. Provider error and timeout preserve
fallback; timeout confirms cancellation cleanup. Disabled/broken SDK starts still
allow native review, archival, zero orders and HALT. These are native SDK/API tests
with provider doubles, not live-provider HTTP or complete mission acceptance.

Focused regressions include existing provider adapters/spending, quotas, advisory
envelope/audit persistence, SDK queue/transport/status and paper risk/control tests.
Prior schemas, both dependency locks, frontend and deterministic domain remain
unchanged. See evaluation 0143 and ADR 0143 for source identity and scope.

## Remaining scope

Actual provider network/billing correlation, graph/other role instrumentation,
complete cross-request market/agent/risk/order/fill lineage, durable SDK trace
binding, protected SDK trace search, collector/Grafana/alerts, production load/
sampling acceptance and independent release review remain pending. All P00–P13
remain incomplete. Browser launch refusal and uncertain original candidate/Docker
custody are preserved. Rollback needs no migration or journal reset.
