# Recovery from a clock-only demo sync halt - increment 0120

`DemoSyncController.recover_clock(SyncClockRecoveryRequest)` resumes scheduling
after an operator repairs a clock regression. The request fixes action
RESUME_CLOCK_HALT and the exact observed halted revision. This action requires a
CLOCK_REGRESSION halt with no pending capture. Pending receipt completion remains
the separate FINISH_PENDING_PROJECTION operation.

## Admission and effect

The operation takes the controller dispatch guard and audited write transaction.
Time must have caught up to the stored observation. The next original history
window must still fit the existing positive one-day bound. History is never
truncated, advanced or replaced to make recovery pass. Capacity must retain room
for subsequent capture outcome and halt transitions.

The gateway verifies its complete local journal and reports ALL_FINISHED,
CUSTODY_UNCERTAIN or STORAGE_UNAVAILABLE without reserving, launching or adopting
a worker. This is a snapshot of retained worker receipts, not terminal custody,
a native connectivity statement or a lease spanning separate journals.
Unfinished gateway intent blocks resume. The verified projection's latest window
end must match the controller cursor; an absent cursor requires no projection.
Corrupt, unavailable or mismatched projection state remains withheld.

Success appends the exact clock recovery request to an audited transition and
moves the controller to STARTING. Existing history cursor, last success time and
failure count are preserved. The due time is the later of current time and the
existing deadline, preserving any remaining backoff. It does not claim a new
observation succeeded. Recovery makes no gateway capture or projection import.
Normal later service ticks perform due work through the existing native gates.

State, revision, clock, capacity, coverage, custody and projection failures use
fixed result codes and retain the original halt. A repeated successful request
conflicts on revision. A failed controller write rolls back the resume, leaving
the audited halt intact. Replay validates the recovery request and preservation
of historical fields. Order/portfolio authority remains denied; native
attestation remains UNVERIFIED.

## One-shot command

Use the existing service entry point, private configuration and original state
directory, with `--resume-clock-halt --expected-revision <observed revision>`.
The clock action and `--recover-pending-attempt` are mutually exclusive. Existing
configuration, Windows/enabled enrollment gates, original journal preflight and
exclusive local service ownership apply. A missing/invalid revision is refused
before state access. The command prints the structured result and exits: 0 for
RESUMED, 2 for withheld/disabled outcomes. It does not start the timer or replace
the previous service heartbeat. An explicit normal launch publishes new health.

## Evidence and limits

Fixture checks cover caught-up time, retained history/due time, subsequent normal
capture, restart, repeat conflicts, initial and degraded halts, retained backoff,
pending attempts, unknown worker custody, history gaps, corrupt gateway and
projection audit, capacity, disabled/busy operation, write failure, strict
revision validation, one-shot CLI composition and public wire denial. No native
SDK, terminal, real credentials or external broker is used.

History backfill, native orphan/terminal custody recovery, checkpoint migration,
service-manager deployment, independent broker review and P13 live acceptance
remain open. Direct library callers do not participate in the CLI ownership
gate; gateway admission still checks unfinished attempts before later capture.
Local journal integrity is not externally attested.

Existing wire schemas/tables remain unchanged. A new clock recovery schema is
exported. Successful audit events carry clock_recovery metadata that older
readers refuse. Stop launchers and preserve all journals on rollback; never edit
or remove that metadata to make older code accept the store.
