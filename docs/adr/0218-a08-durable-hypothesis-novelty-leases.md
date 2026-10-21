# ADR 0218: Reserve hypothesis signatures across A08 research contexts

## Status

Accepted for local advisory SANDBOX use in A08 0.3.0.

## Context

A08 0.2.0 filters drafts against completed A08 receipts captured while compiling
its context. Two contexts compiled before either graph completes can therefore
propose the same research signature. Adding active reservations changes the
mission's reported novelty scope, so the existing A08 0.2.0 contract must remain
unchanged.

## Decision

Add A08 0.3.0 with a separate `HypothesisSpecV3` output contract and an optional
`agent_version` on the authenticated SANDBOX research-context request. Both
supported A08 versions claim proposed signatures in one shared local lease
store, so the original A01 0.6.0 to A08 0.2.0 path participates in collision
control too. The application stores at most 512 short-lived signature leases
in a sibling SQLite file. Each lease is keyed by the canonical hypothesis
signature, bound to the exact content-addressed context reference, and expires
no later than the context's five-second validity window.

For A08 0.3.0, context compilation snapshots active leases, includes them as
distinct reserved signatures in checked research-memory evidence, and
atomically claims proposed drafts. If another context wins a signature between
snapshot and claim, the compiler retries against the new reservation inventory,
up to four bounded attempts. A08 0.2.0 keeps its output and evidence contracts;
when it loses a claim race, its context request fails closed and can be retried
after the short lease expires. Claims are all-or-none for one context. Existing
completed graph outputs remain the durable history after a lease expires. A01
0.6.0 continues to dispatch A08 0.2.0.

## Consequences and limits

- Concurrent A08 0.2.0/0.3.0 contexts in processes sharing the same state path
  cannot both claim the same active signature. Abandoned contexts release their
  leases by expiry without a cleanup worker.
- The reservation database is local application state, not an independent
  security boundary. A process with write access can delete or alter leases and
  cause duplicate advisory drafts. This cannot grant candidate, order, risk, or
  promotion authority.
- A request that repeatedly loses claims fails closed after four attempts.
- Existing completed-run coverage is bounded to 128 records; overflow withholds
  the new context rather than silently omitting history.
- A08 0.2.0 remains available and does not consult active leases.

## Verification

`tests/test_research_novelty.py` covers process-reopen durability, idempotent
same-context claims, lease expiry, atomic cross-instance exclusion, and invalid
requests. The A08 API regression proves A08 0.2.0 and 0.3.0 share the lease
inventory, the 0.3.0 route suppresses and reports an active reservation, and a
0.2.0 request fails closed when it loses a claim race. The published schema is
checked against the Pydantic output model.

## Rollback

Stop requesting A08 0.3.0 and revert its registry entry, implementation, API
version option, lease store, schema, and tests together. Preserve the
`.research-novelty.sqlite3` file while any reserved A08 context remains valid; all
leases expire within five seconds. Existing graph receipts and A08 0.2.0
contracts remain readable.
