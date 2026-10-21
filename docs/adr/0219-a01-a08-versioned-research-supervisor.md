# ADR 0219: Versioned A01 and A08 research supervision

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

A08 0.3.0 adds durable hypothesis-signature leases, while A01 0.6.0 is fixed to
A08 0.2.0. Changing that child in place would alter the existing parent contract
and make persisted runs ambiguous.

## Decision

Preserve A01 0.6.0 and its existing route. Add A01 0.7.0 as a separate partial
SANDBOX cohort with a typed `/api/v1/sandbox/research/supervise/v3` route and a
fixed A08 0.3.0 child. The v3 evidence catalog and dispatch bind exact versions;
the parent stays advisory with a one-call ceiling. Generic PAPER context
compilation rejects A01 0.7.0.

## Consequences

The new TaskPlanV3 and supervision input schemas version the parent output
contract. Both A08 versions share the durable local lease inventory. SANDBOX
does not provide operating-system isolation. This increment grants no trading,
promotion, or approval authority, and does not close P07 or P00-P13 acceptance.
