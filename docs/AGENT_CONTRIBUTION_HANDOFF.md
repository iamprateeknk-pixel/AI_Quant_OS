# Agent-linked PAPER fill observations

The protected operator route `GET /api/v1/paper/evidence/agent-contributions` groups PAPER fill cash flows and fees by an archived advisory `AgentRun` explicitly named in an intent's existing `evidence_refs` as `agent-run:<run_id>`. The report verifies the retained intent hash, order/fill ownership, archived run/audit record and source world-state snapshot before including an association.

The report is observational. It does not prove that an agent caused or generated the intent, attribute full marked P&L, or grant execution or promotion authority. Orders without a reference, unavailable runs, and ambiguous multiple-run references remain visible as gaps and their fills stay out of the attributed totals. Cash and fee totals reconcile over the linked fills only. `causal_attribution` and `promotion_allowed` are always false.

## Contract and verification

The closed v1 response contract is `docs/schemas/agent-contribution-report-v1.schema.json`. The route requires the operator token and returns `Cache-Control: no-store`. It reads existing archives; no database migration or persistence change is made.

Run `pytest -q tests/test_agent_contribution_attribution.py tests/test_agent_runs.py tests/test_paper_attribution.py tests/test_execution_attribution.py` for the focused domain, API, archive-integrity and schema checks.

## Remaining P09 work

This is a first explicit agent-to-order evidence association, not the complete P09 agent contribution deliverable. A12/A13 are still advisory and disconnected from order generation, so the report cannot establish causal contribution. Full marked-P&L attribution, live-broker comparison, financing/restatements, production shadow ingress/scheduling and external alert delivery remain open.

## Rollback

Remove the read-only route and domain report module/schema. Existing intent references and agent/order/fill archives are unchanged and remain readable.
