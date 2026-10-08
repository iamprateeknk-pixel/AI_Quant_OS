# 0087: Retain original validation stage custody beneath Temporal dispatch

Date: 2026-10-08

## Decision

Bind a local job to the original package/candidate, source-only proposed-test job,
all image pins, parent directory identities, quantities, policy and every declared
development fold. Persist reservations, preparation and acknowledgements in one
bounded SQLite transaction with an audit chain. Keep proposed-source execution in
the existing admitted Linux workers and reuse original report custody and checked
archive publication.

Add an outer optional Temporal adapter with one activity per stage, maximum attempts
one, explicit workflow/run identity, strict reply validation and retained history.
Unknown results require independent pinned reconciliation; no automatic replay of
worker calls is allowed. Cancellation drains the owning thread to an existing
prefix cleanup boundary before releasing the root. Safe pauses are audited and
globally limited to sixteen, then uncertainty is retained. The operator command
uses only literal loopback Temporal endpoints.

The existing source worker, guard, container policy, admission pins, wire models
and A10 role ceiling remain intact. The generic prefix runner accepts a trusted
optional checkpoint before reservations; it has no Temporal dependency.

## Scope

This adds local durable orchestration and archival publication, with no acceptance
or promotion authority. It does not close independent isolation/attestation,
adversarial/reality-gap/champion/shadow acceptance, full missions or production
gates. See VALIDATION_JOB_HANDOFF.md for commands, recovery and bounds.
