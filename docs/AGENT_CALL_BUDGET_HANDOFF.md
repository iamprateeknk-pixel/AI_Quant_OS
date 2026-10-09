# Durable agent tool-call admission

## Behavior

Configured graph tool calls reserve capacity before dispatch. Daily and monthly
ceilings use server UTC time and aggregate by agent role across versions. Routine
capacity equals the declared ceiling minus the protected recovery reserve in each
period. Failed, timed-out and interrupted admitted attempts keep their reservation;
they are not evidence of executed tools, provider calls or billable usage. No automatic
retry, refund, quota reset or recovery spending route is provided.

Admission verifies the original policy, canonical reservation bodies and complete
audit chain inside one SQLite write transaction. Competing processes sharing the
same database cannot spend the last available call twice. Backwards clocks,
corruption, changed policies, missing original databases or incomplete initialization
withhold dispatch. An independently retained creation pin prevents loss of the
database alone from silently resetting quotas. Host namespace trust is still required;
erasing or rewriting all storage as its administrator is outside this local boundary.

Quota I/O runs outside the graph event loop. Cancellation during admission prevents
tool dispatch, while an already-running reservation worker can still finish recording
the attempted call. Existing graph receipts remain unchanged: denied admission maps
to `DENIED`; cancelled graph custody keeps the existing uncertain/restart rules.
Existing health policy may apply its conservative restriction to `DENIED`; this
increment does not distinguish budget denials from other denial categories.

## Configuration

Set `AI_QUANT_AGENT_CALL_POLICY` to a local JSON policy file. Persistent paper storage
and an operator token are required. The document contains exactly `schema_version`
(`1.0`) and `rules`. There must be one rule for each role A01 through A18, with these
exact fields:

```json
{"agent_id":"A02","daily_calls":100,"monthly_calls":2000,
 "daily_recovery_reserve":10,"monthly_recovery_reserve":200}
```

That single rule illustrates the format; it is not a complete enabled policy or a
recommended production limit. Each integer must be between zero and 100000, daily
ceilings cannot exceed monthly ceilings, and reserves must fit their respective
ceilings. Boolean values, duplicate fields/roles, extra fields and missing roles
are rejected. Limits require workload-specific acceptance before production use.
`examples/agent-call-policy.json` contains a complete illustrative policy for all
18 roles. The default environment template leaves the setting blank.

The immutable budget journal is a sibling of the configured paper database:
`<stem>.agent-calls.sqlite3`, with `<stem>.agent-calls.sqlite3.pin.json`. Both must be
preserved. Removing the configuration restores the retained policy. A boundary that
started disabled adopts an archive subsequently created at its path. There is no
policy-edit endpoint; changed limits require an explicit migration that remains
pending. First initialization interrupted after its pin preserves unavailable
custody and requires administrative recovery; it is not automatically reseeded.

Protected `GET /api/v1/agents/call-budget` returns `ENFORCED`, `DISABLED` or
`UNAVAILABLE`. Only enforced, verified journals expose the 18 roles' UTC reserved
attempt counts, remaining routine capacity and untouched recovery reserve. Original
payloads, source content, credentials and task/context text are not exposed or stored
in the quota journal; reservations retain their SHA-256 request binding. Corruption
withholds quota data and tools while the independent paper safety controls remain
available. Archive bounds are 20000 reservations, 32 MiB of reservation bodies,
1024 bytes per reservation and one 65536-byte policy. Retention/migration is pending;
capacity exhaustion withholds new dispatch.

## Verified scope and remaining work

Tests cover durable admission before failure, shared version ceilings, daily/monthly
rollover, backwards clocks, concurrent admission, original-policy preservation,
missing databases, corrupt/unreadable storage, disabled-boundary adoption, permission
and payload rejection before charging, real graph denial/restart, non-blocking
cancellation, protected API configuration/status and emergency paper controls.
Evidence and source fingerprints are in `evaluations/agent-call-budgets-0098.json`.

This accounts for calls through `AgentToolBoundary` and, since ADR 0100, declared
context fetches wired to the same quota in the API composition root. Snapshot
preparation before compilation, direct library work and the legacy Jev/OpenAI
adapters remain outside that boundary. Standalone compilers require explicit
budget injection; fixed development evaluators retain their previous behavior.
Provider token admission/settlement, monetary pricing/accounting, protected recovery
spend authorization, fleet deployment/retention, adaptive routing, operator budget
workflows and independent acceptance remain pending. Recovery spending and provider
token/dollar accounting are explicitly false in the new status contract. No paid
provider, candidate source or live broker was executed. P00-P13 remains incomplete.
