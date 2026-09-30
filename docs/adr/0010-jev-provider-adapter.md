# 0010: Bounded TypeSafe Jev advisory adapter

## Decision

Implement `JevClassifier` behind the existing `FastClassifier` contract. The API selects the environment-configured adapter unless a classifier is explicitly injected. Defaults retain quantitative fallback. Neither classification nor provider failure creates a risk approval or changes paper order authorization.

The wire contract uses the official [TypeSafe OpenAPI specification](https://api.typesafe.ai/openapi.json): POST `https://api.typesafe.ai/v1/systemone`, Bearer authentication, a named Choice question, criteria, and typed model/answer/usage response. The URL is fixed. No endpoint override, redirect, retry or ambient proxy is accepted.

The application confidence is the selected choice's probability. TypeSafe's separate confidence statistic describes the distribution's shape; it is retained as `last_provider_confidence` in sanitized status and is not substituted for the selected probability. See [TypeSafe confidence semantics](https://docs.typesafe.ai/confidence). Require the exact four regime labels, finite probabilities in [0,1], a sum within 0.00001 of one, and a selected choice at the maximum probability. Responses must identify the configured model version and the single requested question.

## Configuration and data boundary

- `AI_QUANT_JEV_ENABLED` defaults to `false`; only `true` and `false` are accepted, case insensitive.
- Enabling requires server-only `AI_QUANT_JEV_API_KEY` and `AI_QUANT_JEV_MODEL`. Never put credentials in frontend configuration or source control.
- Model IDs must match `jev-<major>.<minor>.<patch>`. Moving aliases are rejected; the provider must echo the pinned version. Version availability remains an operator verification step; see [TypeSafe models](https://docs.typesafe.ai/models).
- Startup validates configuration without making an HTTP call. Only an authenticated classification request with fresh paper evidence can invoke the provider.
- Requests contain the selected instrument's simulated market features, evidence hashes and source timestamps. The baseline regime label is excluded. Account data, order data and arbitrary operator text are not included.
- Candidate identity, evidence references and timestamps are bound locally to the supplied snapshot. The provider does not establish source freshness. Gateway expiry checks remain in force after the call and after audit persistence.
- All results remain uncalibrated and require escalation. No credentialed run, held-out provider benchmark, accuracy claim or release-gate acceptance has been completed.

## Bounds and accounting

Default limits are 32 KiB request JSON, 64 KiB response, a 150 ms HTTP timeout and the gateway's 200 ms overall cooperative deadline. Request `Accept-Encoding: identity`; reject other response encodings. Response bytes are counted during streaming. No raw response, request, credential or underlying exception detail is returned in provider status or fallback reasons.

A locked request reservation limits each adapter instance to 100 attempted calls. Three consecutive failures open the circuit for 30 seconds. After cooldown, one concurrent probe is permitted. A success from an older group of in-flight calls cannot close a circuit opened by later failures. Cancellation records failure and preserves unknown usage accounting.

Typed response usage is counted before checking model/question identity. `unreported_usage_count` counts reserved attempts without a valid usage report, including in-flight attempts and failures; it is not evidence of zero vendor cost. Request caps, token counters and circuit state are process-local and reset on restart. They are not durable, monetary or fleet-wide budgets. Full P12 cost governance remains open. Clients are created per call; no production latency or connection-pool performance claim is made.

`GET /api/v1/decisions/provider` publishes schema 1.0, PAPER mode, configured identity and sanitized counters. RiskKernel remains usable when this advisory provider's request allowance is exhausted.

## Verification and rollback

Mock transport tests verify request shape, probability mapping, invalid identities and distributions, bounded response handling, redirects, timeout cancellation, concurrent request caps, stale-success circuit behavior, one recovery probe, usage accounting and independent paper risk authorization. Full backend verification: 230 passed, 11 optional integration tests skipped; lint and format checks passed. Dashboard lint and production build also passed during this increment. No paid request was sent.

Rollback can revert the adapter, API wiring and runtime dependency change. No database migration is introduced. Retain existing audit and calibration evidence; reverting code does not erase recorded advisory traces.
