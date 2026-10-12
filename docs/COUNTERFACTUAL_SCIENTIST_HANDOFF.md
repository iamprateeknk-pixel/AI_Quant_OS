# A15 Counterfactual Scientist handoff

## Current state

A15 0.2.0 is PARTIAL/SHADOW in the paper agent graph. The task ID selects a retained replay ID from the existing `counterfactual.sqlite3` archive. The read path verifies the report through `CounterfactualStore`; the agent does not reserve or run another simulation.

When a completed report exists, A15 returns its declared alternative PnL differences with the explicit interpretation `SIMULATED_ON_ALREADY_SEEN_DATA`. It also reports that decision lineage, linked fill lineage, and world-state archives are unavailable. If the report is absent or incomplete, it returns `WAIT_FOR_EVIDENCE`. Every output has zero confidence, full uncertainty, simulation-only semantics, and no promotion authority.

## Implemented surface

- Operator-authenticated generic paper agent context and graph execution routes.
- Exact replay ID binding between task ID, stored report, source hash, and compiled context.
- Read-only loading of an existing verified replay report; no call to `run_counterfactual` or candidate execution.
- Bounded typed alternative summaries; original immutable report retains native details.
- No order, lifecycle, or promotion permissions are added.

## Acceptance evidence

The focused locked Windows suite verifies missing-report behavior, a completed retained native report, simulation labels, context/output binding, registry membership, and the shared runtime gates. See `docs/evaluations/counterfactual-agent-0171.json`.

## Remaining work

Decision, fill, and world-state lineage are not linked into this agent output. Broader forward shadow comparisons, reality-gap analysis, independent mission acceptance, and the remaining P00–P13 gates remain open. Historical diagnostics stay separate from candidate validation and observed market facts.
