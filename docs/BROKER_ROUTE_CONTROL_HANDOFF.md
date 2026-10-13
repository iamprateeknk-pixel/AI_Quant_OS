# Broker route control handoff

The `/brokers` page now exposes an audited control for the local PAPER simulator's new-order route. The operation does not alter global safety state. Existing order lookups and fills remain available, and safety, connection, and eligibility checks still govern order handling.

The browser obtains route state from the configured adapter snapshot. A route command requires an active operator session, fresh access-code reauthentication, a reason, exact `ENABLE` or `DISABLE` text, a UTC timestamp, and the expected current state. The same-origin server route keeps both the session cookie and reauthentication code out of browser requests to the backend. The browser retains an action ID for retry and supports receipt lookup after an uncertain response.

The backend accepts only the PAPER broker ID, requires durable state storage, serializes route changes against new order routing, persists the new enabled flag, and records accepted or policy-rejected commands in the hash-linked audit journal. Startup restores the durable flag before the app serves requests. Re-enabling is denied unless safety state is NORMAL, the broker is connected, and PAPER eligibility allows admission.

## Verification

- Backend route, state-store, router and schema tests pass.
- Web suite passes 179 tests; ESLint and the production build with TypeScript pass.
- Full backend suite status is recorded in `BUILD_STATUS.md`; failures outside this feature must be triaged before release.
- Authenticated production-build browser acceptance passed in the isolated PAPER database: the operator disabled new-order admission with a reason and reauthentication, received a durable receipt, then enabled it again and received a second receipt. The final displayed state was ENABLED. Screenshot: `apps/web/output/playwright/broker-route-control-smoke/broker-route-control-enabled.png` (local ignored output).
- The same browser run exposed intermittent errors in the separate realtime SSE proxy (`Invalid realtime state`). The proxy now reports the rejected validation category to server diagnostics without logging or returning payloads, and tests cover expired leases and mismatched event IDs. A fresh live reproduction is still needed to identify the category and confirm the stream fix; realtime acceptance is not claimed.

## Remaining release work

This local PAPER route control does not complete P00-P13. Independent security and architecture review, production identity and MFA, independent immutable audit storage, operational recovery, and all phase-specific acceptance gates remain open. Live execution is unreleased.

See [ADR 0200](adr/0200-audited-paper-broker-route-control.md).
