# A17 Learning & Evolution Director handoff

## Current state

A17 0.2.0 is PARTIAL/SHADOW in the protected paper graph. It reads the current agent registry inventory and bounded operational run outcome counts rebuilt from audit-verified graph receipts. Training dataset inventory, token/monetary costs, trace quality, and calibrated quality are explicit unavailable evidence markers.

The output always remains `WAIT_FOR_EVIDENCE`, with no tournament actions, no created plan, zero confidence, full uncertainty, and `promotion_allowed: false`. The registry inventory is descriptive and does not imply an approval or promotion decision.

## Implemented surface

- Operator-authenticated generic paper context and graph execution.
- Source-bound registry fingerprint, manifest count, and lifecycle/implementation status counts.
- Source-fingerprinted per-manifest graph run counts and completion rates, explicitly scoped to operational outcomes rather than prediction accuracy or calibration.
- Explicit blockers for calibrated quality, disconnected data, cost, trace, and independent promotion evidence.
- No call to `EVOLUTION_PLAN_CREATE`, no candidate writes, and no changes to lifecycle or controls.

## Acceptance evidence

The focused locked Windows suite checks A17 input/output binding, the protected graph path, catalog membership, context authorization, graph runtime, and call budgets. See `docs/evaluations/evolution-director-0173.json`.

## Remaining work

Connect calibrated quality measures, immutable training datasets, shared token and monetary cost evidence, causal trace quality, and independently reviewed promotion gates. Operational graph-receipt counts persist across restart through the existing graph ledger; they do not replace calibrated measures. Add bounded tournament design and acceptance cases before A17 can create an evolution plan. All P00–P13 release gates remain open.

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

Promotion snapshots can be retained through `PromotionEvidenceStore` in
`ai_quant_os.promotion_store`. Each canonical decision is addressed by its
content hash and bound to the local audit chain; restart reads revalidate the
hash, model, and event binding. This preserves the submitted evidence references
and gate statuses only. The store does not resolve or independently validate
those references and cannot authorize promotion.

Use the local CLI to retain and inspect a decision snapshot:

```powershell
ai-quant-evolve --workspace data-local/evolution promotion-record --input <decision.json>
ai-quant-evolve --workspace data-local/evolution promotion-get <promotion-decision-ref>
ai-quant-evolve --workspace data-local/evolution promotion-list
```

The input must satisfy the published v1 schema. Recording or reading a snapshot
does not mark its evaluations as verified or change the candidate or champion.
