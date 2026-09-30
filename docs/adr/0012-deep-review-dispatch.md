# 0012: Typed advisory deep review dispatch

## Decision

Connect the fast gateway's `DEEP_REVIEW` route to `DeepReviewDispatcher`. A `DeepReviewer` receives the archived world-state snapshot and parent fast decision. The provider boundary is injectable through `create_app(deep_reviewer=...)`; no production reviewer adapter, credential setting or external call is introduced in this increment. Default execution reports `NOT_CONFIGURED` for deep review and `WAIT_FOR_DATA` when the fast gateway lacks sufficient evidence.

The authenticated classification response retains the existing fast AgentEnvelope fields and adds `deep_review`, a schema 1.0 `RegimeReview` envelope. It has task and world-state identity, input/evidence references, confidence/uncertainty, contradictions, creation/expiry, provider route/version and a separate trace ID. Its typed output identifies the parent trace, instrument, status and optional classification/rationale. It cannot issue RiskDecision or OrderCommand objects. `requires_escalation` is the literal `true` even after a completed high-confidence review.

`GET /api/v1/decisions/provider` adds configured deep reviewer identity/version. The classification audit event links both traces and records deep status, provider version, classification, confidence and expiry. Full review envelopes and rationale are not durably stored yet; the input snapshot is. Durable agent run storage remains a P07 dependency.

## Validation, expiry and failure

Validate the parent before dispatch against its world-state version, original expiry, instrument and evidence. A WAIT_FOR_DATA parent never invokes the provider. Review candidates must name the same snapshot, instrument and parent trace, and use nonempty evidence references contained in the supplied instrument. They must have aware creation/expiry timestamps, a supported classification, finite confidence in [0,1], bounded rationale/contradictions and no extra approval fields.

Review creation cannot be before the snapshot or after the current clock. Output expiry is bounded by snapshot, parent and candidate expiry. Check source and parent freshness after provider completion; check both returned envelopes again after audit persistence. If either expired, the API returns 409 and withholds the envelopes. A longer provider expiry never extends the source deadline. Contradictory classification is preserved as `FAST_REGIME:<classification>` alongside bounded reviewer contradictions.

Non-success statuses are `NOT_CONFIGURED`, `WAIT_FOR_DATA`, `BUSY`, `TIMEOUT`, `FAILED` and `INVALID`. They withhold candidate classification/rationale, return confidence zero, and retain escalation. Underlying exceptions and provider response details are not exposed in status. `COMPLETED` means validation completed, not that the review is accurate, calibrated or accepted for trading.

The default cooperative timeout is two seconds, reduced to the remaining source/parent validity. A locked per-dispatcher limit admits at most four concurrent calls; excess calls return BUSY immediately instead of waiting on a queue. Slots release on success, failure, timeout and caller cancellation. Caller cancellation propagates. Providers must cooperate with cancellation and bound their own network/payload I/O; synchronous blocking code cannot be interrupted by the event-loop deadline. Concurrency is process-local and supplies no monetary or fleet budget.

## Verification and remaining integration

Fixture providers exercise successful review, disagreement, missing configuration, failures, invalid identities/evidence/contracts, timeouts, concurrency, cancellation, parent expiry during the provider call and shorter review expiry during audit. API verification checks linked traces and absence of order creation. No paid or production model call occurred.

Next integration is a versioned production model-gateway adapter, with server-only credentials, bounded structured output, durable monetary accounting, independently reviewed evaluation and provider-specific retry/circuit policy. This dispatcher does not complete P06 or P07.

## Rollback

Revert the dispatcher and API wiring to restore route-only deep escalation. There is no database migration; existing snapshot archives and audit events remain readable. Older clients can continue using the unchanged top-level fast envelope fields while ignoring the additive `deep_review` response.
