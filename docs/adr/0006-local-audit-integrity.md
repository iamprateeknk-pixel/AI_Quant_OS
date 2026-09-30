# ADR 0006: Verify the local paper audit history

## Decision

Retain the existing audit event table and add a SHA-256 chain with a sequence number, event key, exact stored JSON, previous hash and version-specific domain marker. A separate head retains the committed event count and digest. The owner appends the event, chain row and head in the same SQLite transaction as control/recovery receipts and safety changes.

Startup verifies the complete chain before returning the application. Reads verify a consistent transaction snapshot. Each append verifies the existing chain inside a BEGIN IMMEDIATE transaction, which serializes independent SQLite connections. Reusing an event key with a changed payload is rejected. Startup receipt reconstruction fills only absent events and preserves original descriptions.

The first upgrade baselines existing events in their original row order. `legacy_event_count` discloses that these events have no evidence of integrity before migration. Subsequent startup must verify the existing metadata and does not regenerate a broken chain.

`GET /api/v1/audit/integrity` returns schema 1.0, validity, committed count, head digest, legacy count and an issue code. A running API rejects authenticated write commands with 503 when this verification fails; the audit history endpoint also returns 503. Mission Control displays the local verification state.

## Evidence

Tests alter event contents, delete first/last events, break sequence numbers, delete chain entries, change the head and inject untracked events. They also verify duplicate handling, changed-payload rejection, migration, restart, two concurrent writer connections and rollback of safety/receipt/event/chain updates when a head update fails. An isolated browser fixture displays both verified and failed history states.

## Limits and operations

This is a local integrity mechanism, not an authenticated or immutable audit service. An administrator who rewrites the entire database and recomputes hashes, removes all metadata to force a legacy baseline, or restores a consistent older database can evade this check. Independent protected checkpoints, immutable remote storage, actor identities and production authorization remain required.

On failure, preserve the database and obtain a known-good copy with independent evidence; do not remove metadata or rebaseline the history to clear the error. Quotes and order dispatch must not be resumed until the incident has been investigated. The report's head hash can be compared with a separately retained checkpoint, but no independent checkpoint storage is deployed yet.

Rollback: preserve the SQLite database before reverting. Older application code cannot maintain the new chain. If older code writes audit events, this version refuses startup when restored; use a verified pre-rollback backup or an explicit reviewed migration. No deployed environment exists.
