# ADR 0200: Audited PAPER broker route control

## Status

Accepted for the local PAPER simulator only. Live execution remains unreleased.

## Context

The Brokers page displayed the simulator adapter's enabled flag but did not provide the specified operator control for stopping new PAPER order admission. The flag alone was not an auditable operation, and changing it through a browser-side broker SDK would bypass backend admission policy.

## Decision

Expose session-bound `POST /api/v1/brokers/PAPER/route-controls` with exact typed confirmation, fresh reauthentication, reason, action identity, expected route state and a UTC client timestamp. Require durable PAPER storage. Serialize route transitions with new-order routing and append accepted or rejected decisions to the existing hash-linked audit journal. Retries with the same action identity return the original receipt; conflicting reuse is rejected. On startup, restore the persisted route state before order admission begins.

Only the PAPER route is controllable. Re-enabling requires NORMAL safety state, a connected broker, and current PAPER eligibility. Disabling changes new-order admission only; existing order lookup and fill handling remain available. Route control does not change the global safety state. The web application keeps the session token and reauthentication code server-side, requests an exact ENABLE/DISABLE confirmation, and retains the original action identity for retry and receipt lookup.

## Consequences

- Route-control commands and receipts have published Pydantic JSON schemas.
- A process without durable PAPER storage returns 503 for route changes.
- Accepted and policy-rejected commands are auditable and idempotent.
- Browser code has no authority over route state; the backend remains authoritative.
- This is a local PAPER control and does not satisfy identity, separation-of-duties, operational recovery, independent audit storage, or any P00-P13 release gate.

## Verification

Focused router, persistence, session, receipt, restart and schema tests cover the backend. Web tests cover same-origin proxying and credential isolation. Full-suite and browser acceptance evidence is tracked in `BUILD_STATUS.md` and `BROKER_ROUTE_CONTROL_HANDOFF.md`.
