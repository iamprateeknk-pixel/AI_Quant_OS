# Reducing a failed bounded demo history window - increment 0122

`DemoSyncController.reduce_failed_window(SyncWindowReductionRequest)` replaces
one confirmed capacity/output-limit failure with a strictly smaller oldest
history window. It does not retry the failed attempt or erase its receipt.

## Admission

The request fixes REDUCE_FAILED_HISTORY_WINDOW, exact halted revision, prior
pending UUID4, distinct caller-owned replacement UUID4, and integer window
seconds (1..86,400). Enabled enrollment/scheduling and original policy binding
are required. Admission runs under the controller dispatch guard and audited
write transaction. Only SOURCE_FAILURE with that exact pending identity qualifies.

Time must be monotonic and transition capacity available. The shorter end must
precede the failed window end and advance beyond any existing history cursor.
The original start is retained, including overlap; a window confined to overlap
is refused with WINDOW_NO_PROGRESS. Equal/larger windows use WINDOW_NOT_SMALLER.

The gateway journal must verify with every worker attempt finished, and the new
identity must be absent. The original exact-window receipt must exist, report
WITHHELD with CAPACITY or RESULT_LIMIT, carry a receipt commitment and confirm
worker exit. Missing/uncertain, account, clock, SDK, unmapped/invalid data and
cleanup failures do not qualify. Projection/cursor agreement must still hold.
Cross-journal checks are snapshots, not a distributed lease or terminal custody
attestation. Gateway reservation/request binding remains the dispatch guard.

## Durable replacement

Before dispatch, the controller appends an audited ACTIVE replacement intent
containing the request and original failed receipt commitment. It preserves the
oldest start, last imported cursor, last success, failure count and due deadline,
while binding the new UUID and shorter end. The old attempt and full receipt
remain in the gateway and original controller audit history.

One capture uses the existing fixed-worker/pending-completion path. Successful
import advances only to the smaller window end; subsequent coverage follows
existing scheduling/backfill rules. A crash after replacement intent preserves
that new UUID/window for normal restart, with no second identity invented.
Successful repeated commands conflict on their old revision and do not capture
again. Failed intent persistence leaves the original pending identity untouched.
Unknown custody during a new capture remains halted; no adoption or reset occurs.

CAPACITY can also describe position/order bounds, and RESULT_LIMIT can reflect
total output. A smaller history window may therefore still be withheld. There
is no claim that a denial proves the history row count was its sole cause, and
no automatic subdivision loop. Every further reduction requires another explicit
command and verified eligible receipt, within existing journal/worker bounds.
Current account/quote context remains observational and sequential, not a
historical account snapshot or native history-completeness attestation.

## One-shot CLI

Use the existing private configuration and original state directory with:

```text
--reduce-history-attempt <new caller UUID4>
--prior-failed-attempt <original pending UUID4>
--history-window-seconds <strictly smaller duration>
--expected-revision <observed halted revision>
```

This action is mutually exclusive with other recovery actions. Missing/misplaced
reduction arguments are refused before state access. Original-journal preflight,
explicit enabled Windows/demo enrollment and exclusive service ownership apply.
The action can perform one fixed demo observation; it starts no timer and
replaces no heartbeat. It reports COMPLETED/WITHHELD/DISABLED with denied order
and portfolio authority, prior receipt commitment when eligible, and controller
health. Exit is 0 for COMPLETED, 2 otherwise.

## Verification and rollback

Checks use a real normalized 17-deal fixture capacity failure followed by a
six-deal one-hour import. They also cover original receipt retention, repeat and
stale/wrong identities, reused UUIDs, confirmed account denial, unknown custody,
clock/capacity/busy/disabled admission, failed intent writes, restart, strict
duration/identity validation, CLI composition and public schema order denial.
The overlap-only case failed before its refusal was added. No actual SDK,
terminal, real credentials or external broker is used.

Existing wire schemas and tables remain unchanged. New window_reduction audit
metadata is rejected by older readers; stop launchers and preserve all journals
on rollback. Never delete the failed receipt or edit audit metadata to make an
older reader accept it. Native completeness/corrections, custody resolution,
checkpoint migration, installed services, independent broker review and P13
live acceptance remain pending.
