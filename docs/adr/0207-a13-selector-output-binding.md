# ADR 0207: Bind A13 to an explicit A12 graph result

## Status

Accepted for partial PAPER/SHADOW implementation on 2026-10-19.

## Context

A13 0.2.0 captures an unavailable strategy-set marker because the context compiler did not accept a checked A12 graph result. Selecting the latest A12 receipt implicitly would permit cross-task or stale evidence to enter an allocation context. Changing the meaning of existing A13 0.2.0 receipts would also break their immutable manifest contract.

## Decision

Add A13 0.3.0 while preserving A13 0.2.0. Context requests for A13 0.3.0 must name one upstream graph run. The runtime verifies the audit-bound graph record, current context, A12 0.3.0 manifest, typed output, and output-to-context binding before the context compiler accepts it. The source record binds the run ID, context reference, canonical output digest, selector state, candidate IDs, registry digest, and blockers. Missing runs return 404; noncurrent, incomplete, or wrong-version runs are rejected.

A13 0.3.0 remains deterministic advisory output. It always has an empty allocation list, `WAIT_FOR_EVIDENCE`, zero confidence, full uncertainty, and false execution/promotion authority. Covariance, calibrated risk budget, and full cross-instrument exposure remain required blockers. A12 eligibility, drift, and approval policy are not implied by graph connectivity.

## Consequences

- Existing A13 0.2.0 manifests and outputs retain their original contract.
- A13 0.3.0 records explicit selector lineage without selecting an implicit latest run.
- Current A12 output remains blocked and produces no eligible strategy IDs; this change does not fabricate evidence or relax promotion rules.
- Allocation acceptance, full portfolio sources, independent review, and P00-P13 release gates remain open.

## Verification

See `docs/evaluations/meta-allocator-0207.json` for focused API and graph tests. No live broker, order, or promotion path is involved.
