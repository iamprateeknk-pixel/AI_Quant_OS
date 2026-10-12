# A17 Learning & Evolution Director handoff

## Current state

A17 0.2.0 is PARTIAL/SHADOW in the protected paper graph. It reads the current agent registry inventory and emits an evolution readiness report. Quality metrics, training dataset inventory, token/monetary costs, and trace quality are explicit unavailable evidence markers.

The output always remains `WAIT_FOR_EVIDENCE`, with no tournament actions, no created plan, zero confidence, full uncertainty, and `promotion_allowed: false`. The registry inventory is descriptive and does not imply an approval or promotion decision.

## Implemented surface

- Operator-authenticated generic paper context and graph execution.
- Source-bound registry fingerprint, manifest count, and lifecycle/implementation status counts.
- Explicit blockers for disconnected quality, data, cost, trace, and independent promotion evidence.
- No call to `EVOLUTION_PLAN_CREATE`, no candidate writes, and no changes to lifecycle or controls.

## Acceptance evidence

The focused locked Windows suite checks A17 input/output binding, the protected graph path, catalog membership, context authorization, graph runtime, and call budgets. See `docs/evaluations/evolution-director-0173.json`.

## Remaining work

Connect calibrated quality measures, immutable training datasets, shared token and monetary cost evidence, causal trace quality, and independently reviewed promotion gates. Add bounded tournament design and acceptance cases before A17 can create an evolution plan. All P00–P13 release gates remain open.
