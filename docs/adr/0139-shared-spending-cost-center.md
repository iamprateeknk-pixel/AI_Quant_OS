# 0139: Observe shared spending under one original head

Date: 2026-10-11. Development, PAPER scope.

## Decision

Expose a protected read-only shared spending observation from the original store.
Verify and capture all eighteen aggregate role rows and every admitted model's
eighteen rows inside one read transaction. Bind the configured real provider
sources, original path, creation identity, policy and owners before and after the
capture. Reject separate ledgers or incomplete configuration without inferred
combined capacity. Recheck operator authorization after the observation.

Validate exact aggregate/model sums and native accounting at both HTTP and UI
boundaries. Money remains canonical integer strings, displayed with BigInt at
nanodollar precision. A breach retains original counters and blocks generation;
reading or refreshing has no refund, retry, reset or provider effect.

Costs shows one aggregate panel with model drilldowns. With a verified shared
book it suppresses the standalone review panel, whose preserved schema cannot
describe mixed accounting. Other states retain the standalone panel where useful.
Call accounting remains a separate observation. Fixed private server reads allow
256 KiB for the bounded shared inventory; existing reads retain their own bounds.

## Limits

This is an explicit refresh snapshot. Realtime costs, platform-wide allocations,
other cost families, actual billing and pricing acceptance, recovery spending,
retention/migration, deployment custody, independent and browser acceptance remain
pending. All P00–P13 are incomplete. Prior schemas and uncertain worker custody
are preserved. No live provider, broker, candidate or capital was used.
