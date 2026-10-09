# ADR 0117: Display declared broker interfaces separately from runtime health

Status: implemented server-rendered view; browser/native acceptance pending.

The capability API is protected metadata about local implementation boundaries.
Operators need to distinguish the simulator, demo reads and unsupported native
execution without mistaking a listed broker for an available trading route.

Read its fixed endpoint with the current server-only session and compose a bounded
five-profile table into Brokers. Validate the exact declared contract and false
native authority, project friendly metadata only, and retain explicit denial,
missing, sign-in and unavailable states. Verify its fixture against the actual
backend response rather than fabricating readiness data.

Consequences: the view remains a fetched observation and supplies no enablement
controls or legal/native acceptance. Future profile versions require explicit
frontend contract support. Browser verification is still blocked by the prior
launch policy; native broker/release acceptance and all remaining phases stay open.
