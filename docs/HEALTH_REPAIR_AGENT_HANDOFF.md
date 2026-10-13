# A16 Health & Repair Agent handoff

## Current state

A16 0.2.0 remains PARTIAL/SHADOW with its original summary-only contract. A16 0.3.0 adds a bounded, read-only view of the verified health incident journal and is also PARTIAL/SHADOW. Neither version creates a repair candidate, stages a workspace, launches a verifier, changes service state, or promotes code.

## Evidence limits

- Health is `UNAVAILABLE`, `RESTORED_ONLY`, `MONITORED`, or `ERROR`. A16 0.3.0 binds the count to one verified incident-store snapshot.
- Trace history and code-test receipts are explicit unavailable markers.
- A16 0.2.0 receives only an incident count. A16 0.3.0 receives at most eight most-recent incident IDs, failure codes, detection times, and exact agent manifest pins. Incident subjects and raw evidence are omitted. Missing or truncated incident details remain blockers.
- Both versions always return `WAIT_FOR_EVIDENCE`, zero confidence, and an empty repair plan.

The existing repair workspace, verifier, and container ownership machinery remain separate. A16 does not invoke those facilities and does not infer that they prove a repair is safe.

## Acceptance evidence

The A16 0.2.0 acceptance record is `docs/evaluations/health-repair-agent-0172.json`. The 0.3.0 increment adds source/output bounds and protected graph tests; see `docs/evaluations/health-repair-agent-0203.json` and ADR 0203.

## Remaining work

Connect attributable traces and test receipts; produce isolated repair proposals with explicit regression plans; and add independent behavior and cleanup acceptance. Shadow/canary release and all remaining P00–P13 gates stay open.
