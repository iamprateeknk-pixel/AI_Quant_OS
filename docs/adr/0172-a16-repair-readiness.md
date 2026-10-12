# ADR 0172: A16 reports repair readiness without preparing repairs

## Decision

Register A16 0.2.0 as a PARTIAL/SHADOW paper graph mission. Bind current bounded health status and incident count while representing trace and code-test evidence as unavailable. Return a typed `RepairCandidate` envelope in WAIT_FOR_EVIDENCE with no repair plan or candidate creation.

## Consequences

The agent reports readiness gaps without running source, tests, repair jobs, or containers and without changing health, risk, lifecycle, or deployment state. Full trace/test/incident evidence, isolated repair proposals, independent verification, shadow/canary review, and all remaining P00–P13 gates stay open.
