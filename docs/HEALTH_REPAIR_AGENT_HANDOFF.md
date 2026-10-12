# A16 Health & Repair Agent handoff

## Current state

A16 0.2.0 is PARTIAL/SHADOW in the protected paper agent graph. It returns a bounded health and repair readiness report from the current watchdog status. It never creates a repair candidate, stages a workspace, launches a verifier, changes service state, or promotes code.

## Evidence limits

- Health is `UNAVAILABLE`, `RESTORED_ONLY`, `MONITORED`, or `ERROR`; only the status and open incident count are exposed.
- Trace history and code-test receipts are explicit unavailable markers.
- Incident context may include only a summary count, never incident details.
- The agent always returns `WAIT_FOR_EVIDENCE`, zero confidence, and an empty repair plan.

The existing repair workspace, verifier, and container ownership machinery remain separate. A16 does not invoke those facilities and does not infer that they prove a repair is safe.

## Acceptance evidence

The focused locked Windows suite covers the A16 source/output contract, protected graph route, catalog and context admission, and neighboring repair preparation/replay boundaries. See `docs/evaluations/health-repair-agent-0172.json`.

## Remaining work

Connect attributable traces, test receipts, and bounded incident context; produce isolated repair proposals with explicit regression plans; and add independent behavior and cleanup acceptance. Shadow/canary release and all remaining P00–P13 gates stay open.
