# ADR 0206: Research selection-rank diagnostic

## Decision

Add research report version 1.3 as an opt-in extension. On each walk-forward validation block, it runs every candidate declared in the immutable plan using the same native dataset and baseline costs. The training-selected candidate is identified from its existing validation result. Results are ranked by descending net P&L, with genome SHA-256 breaking ties. The report retains candidate-bound validation runs and the selected winner's rank for every fold, plus the count and fraction ranked below the median.

The report also computes a combinatorially symmetric cross-validation (CSCV) estimate over the candidate-by-validation-fold net-P&L matrix. For each unique split of the equal-length folds into equal halves, it selects the best candidate on one half, ranks that candidate on the other half, and counts selections ranked below the candidate median. The reported CSCV probability is that count divided by the number of unique splits. Version 1.3 requires at least four complete validation folds and an even fold count; this is checked before a holdout reservation.

Versions 1.0–1.2 retain their original execution behavior and canonical archive bytes. Version 1.3 requires its declared Monte Carlo and adversarial policies, is available through `--selection-diagnostics`, and applies a larger preflight native-call and bar-visit budget before holdout reservation.

## Interpretation and limits

The CSCV calculation estimates selection overfit only for the candidate set and validation folds declared in this plan. It uses aggregate net P&L for equal-length folds; it does not model within-fold return paths, establish independent folds, account for candidates/seeds/experiments searched outside this plan, produce an alpha-test p-value, or provide a general multiple-testing correction. It is diagnostic evidence and is not a promotion gate. Version 1.3 continues to use the existing walk-forward selected-winner validation and stress runs for measured gates, so additional candidate runs cannot change selection or gates. Promotion remains prohibited.

## Verification

The native integration test verifies candidate identity and dataset binding, archive round-trip, output schema validation, and the unchanged promotion prohibition. A separate capacity test verifies rejection before a holdout reservation. `docs/evaluations/research-cscv-baseline.json` retains a synthetic four-fold CSCV run (probability 1/3) with its source lineage, canonical report digest, and `REJECTED` outcome caused by missing minimum activity; it is interface evidence only. Legacy report hash and 1.1 Monte Carlo tests cover compatibility.
