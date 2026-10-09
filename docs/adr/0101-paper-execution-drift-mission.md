# ADR 0101: Source-bound A14 paper execution-cost comparison

Date: 2026-10-09

## Decision

Install a new immutable A14 0.2.0 PARTIAL/SHADOW manifest and deterministic mission
in the existing protected paper graph runtime. Preserve original 0.1.0 and capability
ceilings. Capture one verified paper owner/fill/initial-price ledger and bind all
context descriptors to it, explicitly marking unconnected live/shadow/predictions.

Compare declared adjacent windows by strategy/version/instrument using initial-mid
gross notional weighted spread, signed fill-price movement and fee costs. Declare
six-decimal half-even basis-point measurements. Do not infer PnL optimization or
causal signal/regime/sizing/data effects. Require original-source recomputation and
mandatory gap disclosures at output acceptance.

## Consequences

Missing policy, prices or sufficient samples remain unassessed. Positive threshold
crossings suggest human review only; confidence is zero and demotion unassessed.
Existing graph receipts, quarantine, expiry, call quotas and independent Risk Kernel
authority apply. A01's supported wake plan remains explicit and does not dispatch
A14. Physical prefetch limits and host custody remain those of existing boundaries.

Full live/shadow causal attribution, calibration, monitoring/demotion workflows,
independent comparison and every P00-P13 release gate remain unfinished.
