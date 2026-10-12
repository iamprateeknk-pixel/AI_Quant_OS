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

## Shared promotion evidence contract

`docs/schemas/promotion-decision-v1.schema.json` publishes the strict v1 evidence
snapshot contract implemented in `ai_quant_os.promotion_contracts`. Offline,
replay, adversarial, shadow, and canary evaluations carry a status and, when
completed, timestamped evidence references. Pending stages require an explicit
reason. Candidate, current champion, rollback target, and decision timestamp are
validated together. The decision remains `WAIT_FOR_EVIDENCE` and
`approved_by_policy` remains false for every payload. The model records evidence;
it does not verify referenced artifacts, run evaluations, consult an independent
policy owner, change candidate lifecycle, execute promotion, or perform rollback.
Those connected P11 gates remain open.