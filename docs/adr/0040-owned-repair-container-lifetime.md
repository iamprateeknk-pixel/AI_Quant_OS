# 0040 — Retain repair jobs until owned-container cleanup is confirmed

Status: Accepted for created-container lifecycle; candidate execution pending.

## Decision

Publish a unique owner/name receipt with image and input identities before a
possibly ambiguous create. Admit only the exact inspected container under the
existing fixed confinement policy. Never start it in this layer. Cleanup
revalidates owner/name/image/immutable ID, removes that container and associated
anonymous volumes, and releases the job lease only after its exact removal
response. Do not retry ambiguous creation or treat failed inspection as absence.

Replace unconditional temporary-directory cleanup with owned identity-checked
cleanup that preserves leased jobs. Refuse later staging beside unresolved leases
and reject a second owner of the same job. The command prints success only after
confirmed removal and job cleanup.

## Consequences

Uncertain create/removal and foreign ownership retain input files and diagnostic
ownership receipts rather than deleting source still potentially mounted by a
container. Fault-injection tests prove lifecycle sequencing; live daemon/kernel,
crash durability, restart reconciliation and cross-process ownership remain
unverified. See `../REPAIR_CONTAINER_HANDOFF.md` for limits and rollback handling.
