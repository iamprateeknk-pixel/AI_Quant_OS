# ADR 0205: Preserve immutable archive publication when Windows blocks hard links

Date: 2026-10-14

## Context

P08's local research CLI archives raw CSV and normalized Parquet objects with
content-addressed names. The existing publisher creates and fsyncs a temporary
file, then uses a hard link to publish without replacing an existing object. In
the managed Windows workspace, hard-link creation returns access denied, so the
research archive and its tests cannot run even though the temporary and target
files share a volume.

## Decision

Keep hard-link publication as the primary path. Only when Windows reports
`ERROR_ACCESS_DENIED` (`winerror == 5`), publish with `os.rename` from the
same-directory temporary file. Windows rename refuses to replace an existing
destination and is atomic on the same volume. If the destination already exists,
compare its bytes: identical content is idempotent; different content is rejected.
All other errors and all non-Windows hard-link failures continue to fail closed.

## Consequences

- Restricted Windows environments can create and replay P08 archives without
  granting hard-link privileges.
- Content-addressed objects remain no-replace and conflicting data is not silently
  overwritten.
- The fallback depends on Windows same-volume rename semantics; this is not a
  distributed-filesystem or multi-host locking guarantee.

## Verification

- `tests/test_data_archive.py`: 8 passed, including denied-link fallback,
  idempotent republish, and conflicting-content rejection.
- `tests/test_research_pipeline.py`: 19 passed.
- A fresh synthetic v1.2 research run completed native walk-forward, holdout,
  cost-stress, Monte Carlo, and adversarial checks. Its canonical report hash and
  JSON Schema validate; it remains `VALIDATION_INCOMPLETE` with
  `promotion_allowed=false` because independent-review, sandbox, and shadow gates
  remain pending.
- Ruff lint/format and `git diff --check` pass for the changed implementation and
  tests.

## Rollback

Revert this change to restore hard-link-only publication. P08 archive data created
by the fallback remains content-addressed and readable; preserve the research
store, raw/Parquet archive, and evidence exports.
