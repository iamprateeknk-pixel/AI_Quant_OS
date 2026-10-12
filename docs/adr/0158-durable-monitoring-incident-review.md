# 0158 — Record monitoring episodes and review acknowledgements

## Decision

Add a local append-only SQLite journal for state transitions selected from the
existing original Prometheus rules. Build episode identity from the rule name
and source `active_since` timestamp. Retain gaps, inactive observations, and
replacement episodes as explicit states. Verify the existing hash chain and
semantic event bindings before reads and writes; refuse damaged metadata rather
than rebuilding it.

Expose session-protected incident history and an idempotent acknowledgement
route. Require the caller's expected journal head and action UUID. Revalidate
the operator session immediately before the journal append. Acknowledgements
are review receipts only and explicitly carry `control_authorized: false`.

## Evidence and limits

The original supplied journal tests cover same-state deduplication, pending to
firing progression, unavailable and inactive observations, persisted review,
cross-connection replay, actor/action conflicts, head changes, authorization
revocation before commit, damaged event/head/metadata and bounded capacity. The
HTTP integration test covers session authorization, protected observation
capture, acknowledgement, restart persistence and the false control-authority
claim. Existing local Prometheus source/API checks remain in scope.

The local hash chain is corruption evidence, not an independent signature or
remote custody guarantee. Browser acceptance, multiple-host SQLite operation,
external notification delivery, engine-stopped detection, and remaining P00–P13
gates remain open.

## Rollback

Revert the monitoring journal, its API integration, session revalidation helper,
schema, tests and handoff. The prior point-in-time monitoring observation API
and view can remain without durable history or acknowledgement controls.
