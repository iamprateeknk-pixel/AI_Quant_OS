# 0063: Checked historical research data and immutable partition labels

Date: 2026-10-04

## Decision

Add a bounded audited local catalog for operator-supplied historical bar CSV.
Retain original bytes, source identity, canonical metadata and exact TRAINING or
HOLDOUT classification atomically. Refuse inclusive cross-class time overlap for
the same instrument, with transaction serialization across connections. Verify
complete catalog/audit contents before reads or writes and refuse missing metadata.

Expose operator-only publication and metadata inspection, plus an installed local
register/list command. The agent training reader enforces current TRAINING_READ
capability and SANDBOX scope before and after reading. It returns only training
records. A08's manifest and implementation status remain unchanged until its
hypothesis mission is actually connected and verified.

## Evidence and limits

Tests first reproduced absent catalog/routes/command, silent extra CSV fields and
decimal-context-dependent identity. They then verified label conflict, actual
concurrency, malformed/duplicate/future/oversize input, raw/receipt/journal damage,
missing metadata, capacity, restart identity and permission/scope/revocation denial.
Fixed decimal precision preserves the existing dataset fingerprint convention
without changing its original implementation or sealed source closures.

Windows prevented replacing the owned running launcher during entry-point install.
The owned process was stopped, stale uninstall metadata retained outside the
runtime, installation/dependency consistency verified and the preview restarted.
Full regression evidence is recorded in BUILD_STATUS.

This is historical-data intake, not a hypothesis generator or evaluator. Metadata
labels provide a trusted application boundary; shared raw storage lacks physical
holdout isolation or an independent audit anchor. Per-read durable audit,
statistical independence, real provider validation, A08 source/context wiring and
all live/promotion gates remain open. No proposed candidate code is executed.

## Rollback

Preserve the complete state root and classified source bytes before reverting.
Older applications cannot read the new resource through their API. Rollback must
retain holdout classifications and receipts. See RESEARCH_DATA_CATALOG_HANDOFF.
