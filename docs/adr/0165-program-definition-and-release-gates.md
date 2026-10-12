# ADR 0165: Define completion, control ownership, and release rollback

Date: 2026-10-12

## Decision

Document current deterministic owners for critical paper actions and define measurable completion, phase promotion, and rollback rules. P13 live enablement has no current implementation owner and remains disabled. Any future P13 release needs explicit signed evidence, independent review, bounded account/instrument/capital scope, stop conditions, and a human rollback owner.

## Consequences

The documents make acceptance evidence and rollback expectations reviewable without changing runtime behavior. Existing local paper ownership does not satisfy independent custody, production identity, broker eligibility, or live release gates. See `CONTROL_OWNERSHIP.md`, `DEFINITION_OF_DONE.md`, `RELEASE_AND_ROLLBACK.md`, and `P00_GUARDRAILS_HANDOFF.md`.
