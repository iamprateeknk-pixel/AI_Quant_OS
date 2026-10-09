# Scheduled demo synchronization — increment 0113

## Implemented controller

`DemoSyncController.tick()` performs one due observation cycle using the fixed
worker, durable gateway and audited sync projection. A service timer can call it
repeatedly; calls before the durable deadline do not dispatch. Scheduling is
explicitly enabled in `SyncSchedule` and also requires an enabled enrolled demo
connection. This library slice does not start a service or connect a terminal.

A dedicated SQLite WAL/FULL journal binds the original schedule and connection
policy hash. Capture attempt UUID and exact UTC history interval commit with an
audit event before gateway dispatch. The gateway still reserves its own worker
identity before launch. Controller outcomes commit separately after projection
import. Restart recovers the persisted pending attempt, never inventing a new
identity for it. A crash after projection commit recovers through the receipt
commitment even after quote expiry, without another native call.

The first window begins at the explicit initial history timestamp. Subsequent
windows start at the last successfully imported end minus the configured overlap,
clamped to the initial start. Only a successful projection advances the cursor.
A gap requiring more than the existing one-day sync limit halts without silently
skipping history. The reported cursor is not native history completeness.

## Health and reconnect

Health is STARTING, ACTIVE, HEALTHY, DEGRADED or HALTED. It includes durable due
time, consecutive failure count, last success/cursor, pending identity and fixed
failure category. DISABLED never dispatches. HEALTHY means the cycle imported an
observation; it does not attest current broker connectivity, atomicity or account
cache freshness. Stored health can become old between timer calls.

Only a finished receipt with confirmed worker exit and CONNECTION, QUOTE or
DATA_UNAVAILABLE permits another scheduled observation. Those cycles use capped
exponential backoff, retain the original coverage cursor and initialize the
explicit connection anew through the existing policy. They do not retry an order
or rerun the failed attempt. Success resets the failure count and normal interval.
Account mismatch, unsupported/invalid data, SDK/setup errors, unknown custody,
projection failure, clock regression and capacity halt automatic dispatch.
Pending intent remains available for diagnosis when halted; there is no reset,
automatic orphan reconciliation or credential substitution.

One controller instance has a nonblocking dispatch guard; concurrent timer calls
read ACTIVE state instead of entering its worker again. The gateway prevents a
second native dispatch across multiple controller instances. Multiple independent
service owners can observe an unfinished reservation as uncertain and halt;
this is not a distributed lease/failover protocol.

## Bounds, verification and remaining work

The audit journal allows 3,000 transitions plus its genesis event, with 8 KiB
state/event bounds. Capacity reserves a halt transition before starting work.
Every operation verifies projection/audit agreement. Existing empty, corrupt,
wrong-policy and unavailable stores are withheld without reset. Schedule changes
require a separately reviewed lifecycle/migration; editing the stored binding is
not supported. The public schedule/health schema adds no order/portfolio authority.

Fixture checks cover due scheduling, disabled operation, restart and overlap,
disconnect/reconnect, capped backoff, crash recovery after projection commit,
uncertain custody, concurrent calls, wrong accounts, clock/history gaps,
corruption, schedule conflicts, failed intent writes, capacity and wire contract.
No actual SDK, terminal, credentials or broker were used.

Native demo acceptance, a running service/operational configuration, health API/UI
composition, durable orphan custody, explicit operator recovery, archive/checkpoint
migration, complete native lifecycle/history reconciliation, order routing and
idempotency, alternate brokers, eligibility and independent broker review remain
pending. Full-system and P13 live acceptance are not established.

## Rollback

The controller uses a new dedicated database. Existing gateway and projection
schemas remain unchanged. Preserve all journals, pending identities and uncertain
custody evidence on rollback. Older code does not understand controller files;
do not reset them or reuse their path for another enrollment.
