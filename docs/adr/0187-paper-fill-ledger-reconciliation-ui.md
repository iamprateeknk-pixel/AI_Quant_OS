# ADR 0187: Explicit paper fill-ledger reconciliation UI

- Status: accepted for operator-initiated paper comparison
- Date: 2026-10-13

## Context

P12 calls for an order lifecycle and reconciliation-diff view. The backend exposes an operator-authenticated paper-position comparison that writes an audit event. A mismatch or comparison failure can escalate safety to `NO_NEW_RISK`, so invoking it while rendering a page would be an unsafe hidden action.

## Decision

Expose the comparison on Orders only as a deliberate operator action. Require same-origin POST, an existing operator session, and an explicit `COMPARE` acknowledgement. Keep the backend path fixed, disable repeated submission while pending, bound and validate the response, and present invalid or unavailable outcomes as unknown. Show the safety consequence before submission and render matched totals, per-instrument differences and fill references only from the validated response.

## Consequences

Opening Orders does not cause a reconciliation or audit write. An operator can compare paper positions and inspect differences; the backend remains authoritative for the comparison and any safety escalation. This adds no live-broker or order-management authority. Browser visual/accessibility acceptance and broader P12/P00-P13 gates remain open.
