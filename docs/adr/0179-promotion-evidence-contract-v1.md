# Publish fail-closed promotion evidence contract v1

## Decision

Publish a strict `PromotionDecision` v1.0 contract for candidate and champion identity, offline/replay/adversarial/shadow/canary evaluation evidence, decision, rollback target, policy approval, and timestamp. Completed evaluation states require timestamped evidence references (at most 32 references, each 1–512 characters). Pending or unavailable states require a non-empty explicit reason. The only permitted decision is `WAIT_FOR_EVIDENCE`, and `approved_by_policy` is fixed false.

## Consequences

- The canonical promotion fields have an explicit, versioned shape and unknown fields are rejected.
- The contract stores references and statuses; it does not verify evidence contents or freshness beyond the timestamps, run evaluations, consult an independent policy owner, change a candidate lifecycle, promote, or roll back.
- P11 still requires connected and independently reviewed gates before any promotion authority can be introduced. The v1 contract cannot represent an approved promotion.

## Verification

- Promotion contract tests validate unavailable/pending states, provenance requirements, approval rejection, and JSON Schema parity.
- Promotion, architecture inventory, canonical schema, and broker health tests passed: 15 passed in a fresh archive checkout with changed files overlaid.
- Ruff lint and formatting passed.

## Rollback

Remove the promotion evidence model, schema, tests, architecture inventory entry, and documentation. No persisted record or candidate state is changed.