# ADR 0052: Immutable agent genomes and archival lineage

Date: 2026-10-02
Status: Accepted for archival P11 registry; evolution/runtime gates pending

## Decision

Capture exact installed source and manifests for A02/A07 0.2.0 using their fixed
server-owned editable boundaries. Share that immutable boundary with existing
repair construction while retaining the `REPAIR_SOURCES` interface. A genome
retains its sources, prompt/model/feature configuration, parent and predeclared
metric/protocol pin. Registration never imports retained source or creates a
runtime implementation.

Require canonical increasing versions and one immutable genome per agent/version.
Require retained same-agent parent lineage, unchanged objective/role/contracts,
nonexpanding tools/deadlines and changed source/configuration. Persist canonical
bytes and their exact audit binding atomically in a bounded SQLite FULL/WAL store.
Reject numeric zero in authority fields; execution/promotion require literal false.

## Consequences

Roots are archival baselines, not approved production champions. Only the two
implemented shadow agents currently have bootstrap source boundaries; other
missions require reviewed implementation ownership before admission. Opaque
protocol pins and declarative model/feature fields prove no dataset isolation,
provider configuration, evaluated behavior, improved metric or budget enforcement.
Full package/verifier/image closure, quality/reputation, generation/DSPy,
scheduling/tournaments, shadow/canary/promotion/rollback and independent review
remain pending. No existing shared trading/envelope contract is changed.

Rollback removes/disables the new CLI/modules and additive entry point. Preserve
the separate evolution directory and SQLite/WAL/SHM before rollback; no existing
application database migration or destructive down migration is required.
