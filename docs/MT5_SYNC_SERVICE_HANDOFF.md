# Foreground demo sync service — increment 0114

## Run path

`ai-quant-demo-sync` is a package entry point for the existing fixed-worker,
durable-gateway, projection and scheduler stack. The equivalent module command
is `python -m ai_quant_os.mt5_sync_service`. It runs in the foreground, performs
bounded timer ticks and publishes one replaceable `health.json` beside dedicated
`gateway.sqlite`, `projection.sqlite` and `controller.sqlite` journals.

The runner dispatches only through `DemoSyncController`. A stopped event prevents
new ticks; SIGINT/SIGTERM set that event and allow the current bounded worker call
to finish. Shutdown closes only this service's SQLite handles. Pending journal
intent and uncertain custody remain retained. There is no process-tree cleanup,
terminal shutdown outside the worker, automatic restart or store reset.

Example disabled smoke invocation:

```text
python -m ai_quant_os.mt5_sync_service --config data-local/demo-sync.json --state-dir data-local/demo-sync-state
```

The private configuration has `connection` with every existing demo enrollment
field except `password`, plus `schedule` with `enabled`, explicit UTC
`initial_history_start`, interval/overlap/backoff settings. Optional `max_ticks`
is 1..10,000 (default 10,000); `timer_seconds` is 1..60 (default 1). Ticks include
not-due checks; the tick limit is not a number of native captures. No password may
appear in the configuration. Enabled connection credentials come from the fixed
`AI_QUANT_MT5_PASSWORD` environment variable. Disabled connections can be
validated without a password. Private enrollment fields remain local operator
configuration and must stay outside source control/public health output.

Enabled operation requires both schedule/connection enabled, Windows and the
explicit `--enable-demo-service` launch flag. This flag enables observations only;
it does not confer live trading or order authority. No SDK is installed by this
command. Existing terminal and exact SDK enrollment checks remain in the fixed
worker. The service password is removed from the child environment and sent only
through the existing bounded private stdin protocol. Other platform environment
variables remain inherited; this is not an OS/native sandbox or general secret
isolation boundary.

## Health publication

Each bounded public record carries run UUID, fixed entry point, tick count,
service state, publication/expiry timestamps and the controller health model.
There are no raw credentials, terminal paths, balances or deal payloads. The
public schema fixes order/portfolio authority false and native attestation
UNVERIFIED. The record answers whether the loop is running/stopped/halted,
when the last cycle succeeded and why automatic work stopped.

Publication writes a unique owned temporary file, flushes/fsyncs it, then replaces
the final file atomically. A failed replacement leaves the prior complete file
and removes only that temporary file. Windows directory power-loss durability is
not attested. First publication must succeed before capture; later publication
failure stops further ticks. Persistent storage failure can leave the previous
record until its expiry.

`read_health` caps input at 16 KiB, rejects malformed/duplicate/extra/future records
and returns AVAILABLE, STALE or UNAVAILABLE without private diagnostic text.
The fixed 30-second publication window describes this local service heartbeat;
it does not prove native connectivity, account freshness or process ownership.
A timer longer than 30 seconds can intentionally produce stale intervals. Stale
records retain historical detail. Local file integrity is not externally attested.
Health API/dashboard composition still requires its existing owner boundary.

## Verification and remaining acceptance

Checks include configuration validation/bounds/duplicates, environment password
handling, launch gate, real disabled CLI invocation, timer behavior, stop before
capture, controller halt, initial publication failure, atomic replacement failure,
expired/future/malformed health, schema denial and worker environment exclusion.
Existing controller/projection/sync/gateway/worker/paper-router checks also run.
Only injected native fixtures and a disabled subprocess were used; no SDK,
terminal, real credentials, broker or live capital was used.

This provides a runnable foreground service, not an installed Windows service.
Actual demo acceptance, operational enrollment, service manager deployment,
health API/UI integration, operator halted recovery, orphan custody, full native
lifecycle/history reconciliation, order routing/idempotency, alternate brokers,
independent review and P13 live acceptance remain pending.

Rollback retains all journals and custody evidence. The entry-point addition
changes no dependencies or lock pins. Remove the service launch configuration
without deleting its state. Previous code does not consume service health files.
