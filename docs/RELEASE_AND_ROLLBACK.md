# Release and rollback rules

## Phase promotion

1. Keep each phase in development until its acceptance checklist has current evidence for every item. Link test reports, schemas, reviews, operations drills, and deployment records; do not infer completion from a merged change or a green unit suite.
2. Promote only through the critical path P00 → P01/P02 → P03/P04 → P05 → P06 → P07/P08 → P09 → P10 → P11/P12 → P13. A downstream feature may be developed early, but it cannot satisfy an unmet dependency.
3. P13 requires a signed evidence pack and independent review. Live routing and capital remain disabled while any upstream or P13 gate is open. No task, UI flag, model output, or agent lifecycle state can override this rule.
4. Every release record identifies the build/source revision, contract versions, dependency/image digests, target environment, evidence owner, approver, rollback owner, and stop conditions.

## Rollback

- A failed gate, unexplained state, uncertain order, reconciliation mismatch, stale safety evidence, or loss of critical monitoring stops promotion. The designated safety owner evaluates the evidence and issues any required deterministic safety transition; no agent or UI inference may substitute.
- Revert application code to the last reviewed revision. Keep dependency/image identities pinned; do not silently substitute a new artifact during rollback.
- Preserve databases, journals, audit chains, operator receipts, idempotency records, recovery checkpoints, and uncertain execution reservations. Run an owner-reviewed forward repair if stored data needs correction; do not delete evidence to make a rollback pass.
- For paper/shadow, resume only after source integrity, order/fill reconciliation, safety state, and monitoring are independently checked.
- For any future live release, the designated human operator owns broker-side disable/flatten procedures. Software rollback alone is not evidence that broker orders or positions are safe.
- Record the trigger, last known good revision, actions, unresolved exposures, reconciliation result, approver, and recovery evidence in the release incident record.

## Current state

This policy does not assert that P00-P13 have passed. Current acceptance evidence remains incomplete; P13 live rollout is not authorized.
