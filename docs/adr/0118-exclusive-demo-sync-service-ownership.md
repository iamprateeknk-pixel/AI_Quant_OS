# 0118 — Exclusive local demo sync service ownership

Status: implemented locally; native and independent acceptance pending.

## Decision

Acquire a zero-wait SQLite writer transaction in fixed `service-lock.sqlite`
inside the CLI ExitStack before any gateway/projection/controller construction
or heartbeat publication. Release it after those owned stores close. Retain the
file. Fail with fixed `SERVICE_ALREADY_RUNNING` or `SERVICE_LOCK_UNAVAILABLE`
codes before shared sync work. Use existing standard-library SQLite, without
PID adoption, stale-lease expiry, file removal or process-tree cleanup.

## Reason

Gateway reservation guards native capture attempts, but two foreground services
could still compete over controller scheduling and publish contradictory health.
The CLI needs one owner of its entire local state lifecycle.

## Limits

Only cooperating CLI launches using the same unchanged local file participate.
This is not native terminal custody, durable owner attestation, a network lease,
or isolation from filesystem tampering. Direct library callers retain existing
controller/gateway defenses. All order and portfolio authority remains denied.
No public schema or dependency changes. Native/service-manager/recovery/release
acceptance remains open. Rollback requires stopping concurrent launchers first.
