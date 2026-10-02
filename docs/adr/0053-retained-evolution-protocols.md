# ADR 0053: Retain prospective evolution datasets and protocols

Status: Accepted for local archival declaration; evaluation and release gates open.

## Context

Immutable genomes previously pinned caller-owned opaque objective hashes. P11
needs retained measurement definitions and datasets before comparisons can bind
their declared inputs. Historical graph records lack deployed-source and elapsed
execution evidence, so they cannot safely supply retrospective genome scores.

## Decision

Retain bounded typed datasets/plans in a separate audited SQLite catalog. Derive
new genome objectives from an exact same-agent retained plan using `seed --protocol`.
Reuse the existing metric-direction contract without changing its serialized genome
representation. Preserve the opaque objective path for existing archives.

Dataset reads require current registry capabilities and quarantine admission.
Canonical input hashes cannot cross training/holdout classifications under renamed
cases or datasets. Methods and provenance remain declarations. Neither registration
nor CLI export executes any source, grants authority or computes a score.

## Consequences

Plans and exact dataset bytes survive restart with transactional audit binding,
immutable versions and bounded canonical identity. Existing genome/artifact bytes
remain compatible. Portable schemas and Windows/Linux CI checks are included.

Physical isolation, statistical independence, authenticated actor binding,
mission-specific executable adapters, deployed-source provenance, measured usage,
isolated evaluation, independent acceptance and production release remain open.
Exact-input exclusion does not detect broader observation overlap. An administrator
can rewrite the complete database/head; no external signature or storage boundary
is claimed. Capacity bounds body/audit contents rather than physical database size.

See `docs/EVOLUTION_PROTOCOL_HANDOFF.md` for commands, evidence and rollback.
