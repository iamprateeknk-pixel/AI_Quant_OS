# A16 Health & Repair Agent handoff

## Current state

A16 0.2.0 remains PARTIAL/SHADOW with its original summary-only contract. A16 0.3.0 adds bounded incident context and A16 0.4.0 adds audit-verified graph trace links; both are PARTIAL/SHADOW. No version creates a repair candidate, stages a workspace, launches a verifier, changes service state, or promotes code.

## Evidence limits

- Health is `UNAVAILABLE`, `RESTORED_ONLY`, `MONITORED`, or `ERROR`. A16 0.3.0 binds the count to one verified incident-store snapshot.
- A16 0.2.0 and 0.3.0 retain explicit unavailable trace markers. A16 0.4.0 resolves up to eight graph traces by incident run identity through the verified graph ledger and checks exact incident ID, failure status, and agent manifest pins. Missing traces remain PARTIAL; raw task payloads and provider error text are not included. Code-test receipts remain unavailable.
- A16 0.2.0 receives only an incident count. A16 0.3.0 receives at most eight most-recent incident IDs, failure codes, detection times, and exact agent manifest pins. Incident subjects and raw evidence are omitted. Missing or truncated incident details remain blockers.
- Both versions always return `WAIT_FOR_EVIDENCE`, zero confidence, and an empty repair plan.

The existing repair workspace, verifier, and container ownership machinery remain separate. A16 does not invoke those facilities and does not infer that they prove a repair is safe.

## Acceptance evidence

The A16 0.2.0 acceptance record is `docs/evaluations/health-repair-agent-0172.json`. The 0.3.0 and 0.4.0 increments add bounded incident context and verified trace links; see `docs/evaluations/health-repair-agent-0203.json`, `docs/evaluations/health-repair-agent-0204.json`, ADR 0203, and ADR 0204.

## Remaining work

Connect attributable code-test receipts; produce isolated repair proposals with explicit regression plans; and add independent behavior and cleanup acceptance. Shadow/canary release and all remaining P00–P13 gates stay open.
