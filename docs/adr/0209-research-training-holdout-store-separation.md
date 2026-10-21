# ADR 0209: Store TRAINING and HOLDOUT CSV bytes separately

## Status

Accepted for the local advisory research catalog on 2026-10-19.

## Context

The research catalog stored TRAINING and HOLDOUT CSV bytes in one SQLite database. That made it possible for the normal training reader to load holdout data while it verified and filtered the shared catalog, even though the returned agent context omitted holdout rows.

## Decision

Keep TRAINING rows in the configured catalog file and write HOLDOUT rows to a sibling `<stem>.holdout<suffix>` SQLite file. Each file has its own receipts, raw bytes, and hash-chained audit events. All catalog operations acquire the training database write lock before the holdout database lock, serializing cross-class overlap checks and writes across catalog instances.

The A08 TRAINING reader verifies and returns TRAINING bytes only. It checks HOLDOUT receipt and audit metadata without selecting the HOLDOUT `raw_csv` column. Operator validation preparation remains the supported operation that loads both exact snapshots. Metadata and audit mismatches fail closed.

On startup, a legacy mixed catalog is verified before migration. HOLDOUT rows are copied to the sibling store first; the original TRAINING-store audit events remain intact and receipt tombstones record the moved rows. A retry after interruption verifies any already-copied bytes and completes the move idempotently. Conflicts stop startup without deleting the source bytes.

## Consequences

- At-rest raw bytes are kept in distinct SQLite files, and the generator read path does not load HOLDOUT CSV bytes.
- Both files are still owned by the same local application identity and process. This is not operating-system isolation or a separate validation service; separate identities, access controls, backups, and independent acceptance remain open.
- The CLI/API keep their existing catalog interface; persistent deployments add the HOLDOUT sibling file. Backups and restores must include both files.
- Rolling back to a binary that predates this change while the sibling file contains data would hide those holdouts. Preserve both files; restore through a verified migration before using an older binary. No file deletion is part of rollback.

## Verification

The focused catalog, research API/CLI, generation, validation, and package tests pass 72 tests. They verify separated raw bytes, verified legacy migration and restart after a committed holdout copy, cross-instance publication serialization, and that the TRAINING reader does not select corrupted HOLDOUT bytes. The complete Windows suite passes 3,145 tests with 42 skipped. Ruff and formatting checks pass. `check_boundaries.py` reports no machine-declared package layers in `.clean/architecture.json`; the scoped structure map reports no findings for `research_evidence_catalog.py`.
