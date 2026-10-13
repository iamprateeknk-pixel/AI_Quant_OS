# Agent-linked PAPER fill observations

The protected operator route `GET /api/v1/paper/evidence/agent-contributions` groups PAPER fill cash flows and fees by an archived advisory `AgentRun` explicitly named in an intent's existing `evidence_refs` as `agent-run:<run_id>`. The report verifies the retained intent hash, order/fill ownership, archived run/audit record and source world-state snapshot before including an association.

The report is observational. It does not prove that an agent caused or generated the intent, attribute full marked P&L, or grant execution or promotion authority. Orders without a reference, unavailable runs, and ambiguous multiple-run references remain visible as gaps and their fills stay out of the attributed totals. Cash and fee totals reconcile over the linked fills only. `causal_attribution` and `promotion_allowed` are always false.

For a marked P&L view, use `GET /api/v1/paper/evidence/agent-contributions/marked`. This v2 response adds net quantity, pre-fee cash, a shared portfolio liquidation mark, marked inventory and marked P&L by verified agent-run association and instrument. All linked runs for one instrument use the same liquidation side selected from the linked-fill portfolio net position, so their marked totals reconcile. Freshness/trust failures withhold only mark-dependent values; cash and fee evidence remains available, with explicit mark state and evidence gaps. The response includes the exact order/fill ledger fingerprint, observation time, and mark source timestamp/world-state reference when available. It does not infer causality from the run-to-intent reference.

## Contract and verification

The closed v1 response contract remains `docs/schemas/agent-contribution-report-v1.schema.json`; the marked report uses `docs/schemas/agent-marked-contribution-report-v2.schema.json`. Both routes require the operator token and return `Cache-Control: no-store`. They read existing archives and current in-memory paper quotes; no database migration or persistence change is made. After restart, if fresh quotes have not been republished, marked inventory/P&L is withheld while linked cash and fees remain.

Run `pytest -q tests/test_agent_contribution_attribution.py tests/test_agent_runs.py tests/test_paper_attribution.py tests/test_execution_attribution.py` for the focused domain, API, archive-integrity and schema checks.

## Remaining P09 work

This is a source-verified agent-to-order accounting association, not causal credit. A12/A13 are still advisory and disconnected from order generation. It covers only the explicitly linked PAPER fills, not full agent performance, live-broker comparison, financing/restatements, production shadow ingress/scheduling or external alert delivery.

## Rollback

Remove the read-only route and domain report module/schema. Existing intent references and agent/order/fill archives are unchanged and remain readable.
