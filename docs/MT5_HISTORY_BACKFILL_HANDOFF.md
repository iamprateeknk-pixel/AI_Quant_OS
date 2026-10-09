# Explicit bounded demo history backfill - increment 0121

`DemoSyncController.backfill_history(SyncBackfillRequest)` processes one oldest
uncovered history window after a HISTORY_GAP halt. The request fixes action
BACKFILL_HISTORY, the observed halted revision and a caller-owned UUID4 for this
one intent. Preserve that identity; a retry must not invent another attempt.

## Admission and durable work

The controller requires enabled enrollment/scheduling, exact halted revision,
HISTORY_GAP reason, no pending capture, monotonic time and transition capacity.
It verifies gateway custody and refuses any unfinished attempt. The proposed UUID
must be absent from the verified gateway journal. Existing projection coverage
must match the controller cursor. These cross-journal inspections are snapshots,
not a distributed lease; gateway reservation still enforces idempotency and
request binding before native dispatch.

The window starts at the original initial history timestamp, or the last imported
end minus the configured overlap, clamped to that initial timestamp. Its end is
the earlier of current time and start plus one day. The operation does not clip
the beginning to recent history. It commits an audited ACTIVE intent with the
exact caller UUID and window before calling the existing pending-capture path.
That path retains fixed-worker limits, exact demo enrollment checks, gateway
reservation, freshness validation, projection audit and unknown-custody refusal.

Success imports the original bounded window, advances the cursor only to its end
and returns COMPLETED with HEALTHY controller status. That status describes one
imported observation, not closure of the full gap or native history completeness.
After the normal interval, another oversized gap halts again. An operator can
issue a new explicit command with the new halted revision and a new intent UUID.
Eventually the remaining window fits normal scheduling. Overlap is retained;
older deal versions are preserved even when later windows omit those deals.

Current account, positions, active orders and quotes remain current sequential
observations alongside historical deals. They are not historical account
snapshots. Existing DECLARED_WINDOW_ONLY history and unverified execution-time
contract-unit limitations remain. A day exceeding the native deal/output limits
is withheld, not silently truncated; finer subdivision remains pending.

## Failure and restart

Failed intent persistence dispatches nothing. A crash after intent commit leaves
the original ACTIVE UUID/window; normal controller restart handles that same
pending attempt through existing gateway/projection replay. A completed command
cannot be redelivered at its old revision to launch another capture. Reusing a
prior gateway UUID for a different backfill is refused before controller intent.
Unknown worker custody leaves the exact pending intent halted and never retries.
Existing confirmed transient-source failure/backoff rules remain in effect;
failed capture does not advance the history cursor. No automatic multi-day loop,
state reset, credential substitution, child adoption or live order is added.

## One-shot CLI

Use the existing service command, private configuration and original state with
`--backfill-history-attempt <caller UUID4> --expected-revision <halted revision>`.
This action is mutually exclusive with the existing projection and clock recovery
actions. Original-journal preflight, explicit enabled Windows/demo enrollment and
exclusive service ownership still apply. Unlike receipt/clock recovery, this
action can dispatch one fixed demo observation worker when explicitly enabled.
It starts no service timer and replaces no heartbeat. Structured output reports
the result; exit is 0 for COMPLETED, 2 otherwise. A later normal service launch
publishes new health and handles due work.

## Verification and rollback

Fixtures cover oldest-window selection, multiple backfills followed by normal
coverage, overlap and retained deals, stale/repeated/reused identities, restart
after intent commit, failed persistence, unknown custody, disabled/busy/clock/
capacity refusal, corrupt projection/controller, strict request validation,
one-shot CLI composition and schema order denial. No actual SDK, terminal,
credentials or external broker is used.

Existing schemas/tables remain unchanged; the separate backfill schema is new.
Backfill intent events include audited backfill metadata, which older readers
withhold. Stop launchers and retain all journals/custody evidence on rollback;
never edit or erase the metadata to make an older reader accept it.

Actual native history completeness/corrections, oversized-window subdivision,
native/orphan custody, checkpoint/archive migration, installed service deployment,
independent broker review and P13 live acceptance remain pending.
