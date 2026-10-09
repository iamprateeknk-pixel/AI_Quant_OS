# Durable demo observation admission — increment 0110

## Implemented

`DemoObservationGateway` wraps the fixed supervised observation worker with a
separate SQLite journal. Callers supply a UUID4 attempt identifier and the existing
validated demo enrollment. The journal commits a reservation, owner UUID,
preassigned worker UUID, UTC start time and request fingerprint before dispatch.
The supervisor now accepts that worker UUID and binds it to the exact child it
creates. Existing callers that omit the UUID retain the previous behavior.

Completed attempts retain the sanitized supervisor result. A repeated attempt
with the same declared connection identity returns the existing receipt without
another dispatch, including after a new Python process opens the journal. Changed
identity with the same attempt returns ATTEMPT_CONFLICT. Captured quotes are
revalidated against the enrollment, exact symbol mapping and current expiry on
replay; an expired receipt returns STALE_RESULT with no observation or refresh.
New observations require an explicit new attempt identifier.

The request fingerprint includes enrollment, SDK version, symbols and declared
connection identity. The password is excluded so credential rotation cannot
cause an existing attempt to execute again. Login, server and terminal path are
hashed in memory and are not stored literally. This fingerprint is not encryption
or an anonymity guarantee: low-entropy identity values can be guessed. No password,
raw connection request, SDK diagnostics or account names are written to the
journal. Normalized public quotes and declared venue symbol names are retained.

## Custody and restart semantics

Any RESERVED or UNCERTAIN attempt blocks new dispatch through the same journal,
across connections and gateway instances. A reserved intent does not prove that
a child started; a crash between reservation and returned receipt remains an
unknown outcome. Time passage, a new owner instance, a missing PID or an expired
quote never clear that reservation. Invalid runner replies and failed receipt
writes leave the committed reservation blocking further dispatch.

The current live owner can reconcile its unfinished attempt only by observing
EXIT_CONFIRMED for the exact preassigned worker UUID through the supervisor's
retained child handle or most recent verified exit proof. This bounded in-process
exit proof covers cleanup after a caller interruption, contains no quote payload,
and cannot prove another UUID's exit. A new gateway owner cannot adopt prior memory
or assert that an old child exited. Confirmed exit proof stays in the live owner
until its journal commit succeeds, so a transient write failure does not discard
that proof. Reconciliation records a withheld result; it does not reconstruct a
lost native observation or permit retrying the original attempt. It permits a
separate later attempt after custody has been resolved.

This implements durable blocking and completed-receipt reuse across restart.
Automatic reconciliation of uncertain children after an actual owner-process
restart still requires independent process custody evidence and remains open.
Terminal custody stays UNVERIFIED; no shared terminal or process tree is killed.
No order, portfolio, promotion, eligibility or live-capital authority is granted.

## Storage integrity and bounds

Reservations and final results are committed with their audit events in SQLite
transactions using FULL synchronization, WAL and a two-second lock timeout. Each
projection has a content hash and must match replay of the existing shared audit
chain. Replay enforces owner, worker, request and start-time immutability, legal
transitions, response shape and UTC completion ordering. Missing projections,
history or integrity metadata are withheld rather than rebuilt. An existing
empty journal is refused. Initialization creates only a new empty dedicated
database; other database layouts and versions are not adopted or migrated.

The journal is bounded to 1000 attempts, 3000 audit events, 16 KiB per public
result and bounded stored row/event payloads before replay. Capacity exhaustion
withholds new attempts and never purges old receipts. Retention/export operations
remain future work. Local hashes and the co-located audit head detect inconsistent
corruption; they are not external attestation or protection against whole-database
rollback or a coherent adversarial rewrite. Do not delete or replace a journal
to clear an unresolved attempt.

## Contract, verification and rollback

`demo-gateway-receipt.schema.json` exports the version 1.0 public receipt contract.
The result explicitly denies automatic retry, order submission and portfolio
mutation. The focused checks cover fixture capture, an actual supervised
missing-terminal response, separate-process receipt replay, cross-connection
admission, interruption/restart, expiry, identity conflict, malformed responses,
unknown/foreign child ownership, write failure, retained exit proof, corrupted or
missing storage, disabled capture, capacity and schema/runtime compatibility.
Existing observation, supervisor and broker-router checks are included.

No native SDK, broker terminal or real credentials were used. This evidence does
not prove native Exness acceptance or the P04 order-restart acceptance gate.
External account/position/order/fill synchronization, reconnect behavior, durable
order idempotency, alternative broker adapters, eligibility composition and
independent critical review remain open. All P13 live gates remain open.

There is no migration of existing stores or dependency lock change. This wrapper
is not composed into the API or trading router. Rollback must preserve any journal
with unresolved custody and the matching live supervisor; reverting code is not
permission to restart or retry that work. Older supervisor code cannot accept the
new preassigned-worker keyword and must not be combined with this wrapper.
