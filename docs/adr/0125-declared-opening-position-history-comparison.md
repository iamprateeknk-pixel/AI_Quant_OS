# ADR 0125 — Compare retained lot movements against a declared opening

Status: implemented locally; independent/native acceptance pending.

## Decision

Read latest normalized deal versions and the latest captured positions from one
verified projection snapshot. Require an explicit account/start-bound opening
inventory, compare signed lots by instrument and stable position identifier,
and retain all uncertainty and denied authority. Never assume flat opening from
an arbitrary bounded history window or convert current contract metadata into
historical base units.

Corrections replace the recorded contribution once; CASH versions contribute
no lots. Missing prior identities withhold the comparison instead of deleting
them. Compare per-position inventories before any instrument aggregation. Bound
rows/numeric precision and retain original source/declaration commitments.

## Consequences

This advances fill/position reconciliation as a descriptive local operation.
Consistency with an unverified opening is not broker attestation, native history
completeness, cash accounting or permission to affect portfolios. Original
non-atomic reads and expired observations remain historical. Existing domain
contracts, schemas, journal formats and scheduling behavior are preserved;
rollback keeps journals and removes only the added offline command/reader.
