# Supervised MT5 demo observation worker — increment 0109

## Implemented boundary

`ai_quant_os.mt5_demo_worker.supervise_demo` runs the existing demo observation
policy in one fresh Python child. The command is fixed to the current trusted
Python runtime, isolated Python startup (`-I`), and this worker's resolved file.
It accepts no command override, SDK source path, candidate package, order,
strategy or portfolio action. Windows launches the child without a visible
console. The trading router and API do not import or compose this worker.

Disabled enrollment returns immediately without launching a process. Enabled
enrollment is encoded to at most 16 KiB of stdin; credentials are not command
arguments or environment variables. The child rejects unknown operations,
duplicate JSON fields, invalid enrollments and oversized input before native
SDK import. Only Windows with an existing declared terminal executable reaches
the fixed `MetaTrader5` package import. The existing policy checks the declared
SDK version before terminal initialization and checks exact demo account identity
before and after observing quotes. The package must already be installed in a
trusted operator-managed runtime; this increment does not install or attest it.

The child redirects native stdout and stderr diagnostics to the null device,
then emits one typed public reply through a separately retained report descriptor.
This suppresses incidental native diagnostics, not a security sandbox against
malicious native code. The parent validates the report's state, denied authority,
enrollment, SDK version, exact ordered symbol map, timestamps and freshness.
Expired snapshots are withheld after IPC rather than accepted because they were
fresh inside the child. There is no automatic reconnect or retry.

## Supervision and custody

The supervisor retains at most 64 KiB of combined pipe traffic, counting discarded
stderr too. Reader and writer threads use bounded chunks; stdin handles partial
writes. A monotonic ten-second execution budget includes monitored child startup
and pipe completion. When that budget or the output limit is exceeded, the parent
withholds observations and attempts termination using only the exact Python child
handle, with up to two seconds to confirm exit and short thread joins. OS process
creation itself is synchronous; this does not promise cancellation of a stalled
OS spawn call. SDK call cancellation is effected by stopping the child, rather
than relying on post-call clock checks.

One in-process lock prevents simultaneous captures. If child exit or pipe closure
cannot be confirmed, the supervisor retains the exact child and pipe handles under
an opaque worker UUID, returns UNKNOWN custody and refuses another launch.
`inspect_uncertain_worker` polls those same handles without sending another
request. Verified exit and completed pipe threads release that in-process block.
Startup/pipe exceptions and caller interruptions also attempt owned-child cleanup;
exceptions never project private diagnostics into a public result.

Ownership of the MT5 terminal remains UNVERIFIED for every response. The parent
never kills a shared terminal, searches for matching process names, or kills a
process tree. SDK shutdown disconnects the client and cannot prove termination
of a terminal that initialization may have launched. Unknown custody is retained
in memory only; durable custody and restart reconciliation remain required before
this is composed into the long-lived gateway. Do not restart a supervisor to
clear uncertainty or automatically retry a timed-out request.

## Verification

The focused checks cover the actual worker subprocess with disabled, invalid and
missing-terminal inputs; a successful public quote payload through a real pipe
from a controlled trusted fixture child; stdout/stderr overflow; timeout; malformed
and duplicate replies; enrollment/symbol/expiry binding; denied authority;
unknown-child admission blocking; busy admission; pipe startup failure and
oversized enrollment. Existing observation and broker routing checks are included.
The native successful case is a fixture SDK, not an Exness acceptance result.
No broker terminal, native SDK or real credentials were used. Evidence records
fresh staged source hashes, verification output and unchanged prior schemas.

## Remaining scope

P04 still requires durable gateway custody across restart, account/position/order/
fill synchronization, reconnect policy, durable order idempotency, normalized
external fills, IBKR/Zerodha adapters, capability/eligibility composition and an
actual demo environment acceptance. Native dependency/terminal provenance and
independent critical review also remain open. All P13 live-capital gates remain
open. This worker never grants order, portfolio or live authority.

No persistence migration or dependency lock changes were made. Rollback means
removing this standalone worker before any future gateway depends on it; do not
erase retained uncertain handles or use rollback as a reason to retry a request.
