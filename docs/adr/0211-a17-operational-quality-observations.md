# ADR 0211: A17 operational quality observations

## Status

Accepted as a bounded local PAPER diagnostic; calibrated quality and promotion remain unavailable.

## Decision

Rebuild operational agent outcome counts from the current graph receipts retained
in the existing audit-bound graph ledger. The ledger verifies each current
receipt, checks the graph-run/audit inventory, and rejects malformed or
over-capacity histories. The bounded A17 context includes per-manifest run
counts and status counts, a completed-observation rate, and a fingerprint over
the receipt identities and outcomes. No second writable quality database is
needed: after restart, the metrics are reconstructed from the same retained
receipts and produce the same observation fingerprint for the same receipt set.

These are operational outcome counts only. They do not measure prediction
accuracy, calibration, cost, trace quality, or evidence independence. A17 keeps
`QUALITY_CALIBRATION_UNAVAILABLE` in its blockers and `promotion_allowed` false.
No agent quality result can authorize activation, orders, or promotion. An
empty ledger and an unavailable ledger have distinct explicit states; histories
over the bounded receipt window are withheld rather than silently truncated.

## Recovery and limits

The graph ledger's existing append-only audit chain and current-record checks
are the recovery authority. Journal corruption, missing graph rows, malformed
transition inventory, or a changed current receipt prevents the observation.
The audit chain is local SQLite and cannot protect against an attacker who can
rewrite the complete database and audit metadata. There is no cross-process
lease and no calibrated or independent quality claim.

## Verification

Tests prove operational outcome aggregation, missing/empty states, duplicate
and wrong-manifest refusal, graph-receipt reconstruction after restart, and
A17's continued calibration/promotion blockers. Focused command:

```powershell
python -m pytest -q tests/test_agent_quality.py tests/test_graph_runtime.py tests/test_evolution_director_agent.py tests/test_evolution_cli.py
```
