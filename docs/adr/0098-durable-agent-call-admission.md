# 0098: Reserve routine agent tool calls before effects

Date: 2026-10-09

## Decision

Inject a policy-owned call-admission protocol into the existing agent tool boundary.
Persist UTC daily/monthly attempted-call reservations by role, shared across versions,
under an immutable complete 18-role policy. Protect the declared recovery capacity
from routine work. Bind reservation identities, payload/context hashes and policy to
a checked audit journal and a separate original creation pin. Withhold dispatch for
corruption, exhaustion, uncertain initialization, changed policy and backwards clocks.

Perform quota I/O outside the graph event loop and recheck permissions afterward.
Cancellation prevents subsequent dispatch; its existing admission worker can still
retain the attempted-call charge. Never automatically release uncertain charges or
replay a tool. Keep graph/health receipt formats and independent paper controls.

## Consequences

Configured graph calls have a durable resource ceiling and observable reservation
counts. This is not provider token usage, monetary billing or recovery authorization.
Legacy providers, source compilation and direct libraries still require integration.
The guard is disabled without a policy or retained archive, and cannot be disabled
by removing a retained configuration. Immutable-policy migration, bounded retention,
independent review and full cost governance remain open. Existing `DENIED` health
handling stays conservative; its finer budget classification remains pending.
