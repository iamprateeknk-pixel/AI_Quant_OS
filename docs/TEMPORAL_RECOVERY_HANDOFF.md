# Temporal paper uncertainty triage

P10 now has an initial durable runbook for an uncertain **server-owned PAPER order**.
This is a library integration boundary; the HTTP application does not yet start a
Temporal worker or expose recovery workflow commands.

## Verified behavior

1. Validate bounded incident/order identifiers and find the actual order in the
   bound gateway. Refuse missing or non-PAPER orders.
2. Persist containment in the existing audited paper ledger and preserve the
   greater of durable and in-memory safety state. Never lower HALT or FLATTEN_ONLY.
3. Look up the broker acknowledgement through the existing reconciliation method.
   Never dispatch, resend, import fills, flatten, or change an agent implementation.
4. Compare actual local and simulated broker fills. A mismatch persists HALT.
5. Retain an immutable, audited result for each attempt. Activity redelivery
   returns that receipt; a new explicit retry requests a new lookup.
6. Return REVIEW_REQUIRED only for a confirmed terminal/acknowledged order with
   matching positions. This means **triage is ready for review**, not safety recovery,
   independent approval, deployment or promotion. Trading remains stopped.
7. If unresolved, wait on a durable retry signal. Bursts coalesce. Stop after twenty
   attempts or seven workflow days, retaining the reservation and safe state.

Temporal workflow history retains activity results, signals and the seven-day
deadline. The real-service test shuts down the worker and SQLite-backed Temporal
server, reconstructs every paper application object from disk, restarts the server,
sends a signal before worker startup, then completes attempt two without another
broker order. Two further real-service cases verify failed containment and the
twenty-attempt bound. SDK sandboxing remains enabled.

## Local verification

Install the package with its `dev,recovery` extras. SDK version is 1.34.0. Use the
[official CLI 1.9.1 release](https://github.com/temporalio/cli/releases/tag/v1.9.1).
The checked Windows amd64 ZIP SHA-256 is
`babb65844835045c91fb98930fe10b81de28db80c8c609180473d2c0b18c7589`.
Set `AI_QUANT_TEMPORAL_CLI` to the extracted executable and
`AI_QUANT_REQUIRE_TEMPORAL=1`, then run:

```powershell
python -m pytest -q tests/test_paper_recovery.py tests/test_temporal_recovery.py
```

The dedicated Windows/Linux CI job downloads checksum-pinned binaries and treats
missing executables as failures. Ordinary installations without the optional SDK
or executable skip only the real-service tests. Hosted CI results are unverified.
Local tests use the full native dev service, not a mocked workflow environment.
The SDK documents this SQLite-backed local service in
[WorkflowEnvironment](https://python.temporal.io/temporalio.testing.WorkflowEnvironment.html).

## Integration contract

Construct `PaperRecoveryService(execution, risk, state)` using the **same instances**
as the owning application. Register `PaperRecoveryWorkflow` and
`RecoveryActivities(service).registered()` on a dedicated Temporal Worker queue.
Use `start_paper_recovery(client, incident_id, order_id, task_queue=...)` from a
trusted, authenticated operator composition root. Reusing a running or completed
incident ID is refused. The starter validates shape; the activity validates actual
ownership. The `retry` signal accepts no client verdict or evidence.

Do not run an independent worker against a live application's SQLite file: its
separate in-memory RiskKernel cannot stop that application. Production wiring must
bind the owner and worker together, authenticate Temporal transport/commands, and
restrict workflow/activity access. This increment creates no external command
endpoint and grants no agent permission.

## Failure and rollback limits

Expected broker unavailable/ambiguous outcomes remain WAITING_RETRY. Failed audit
reads, writes and unexpected reconciliation errors halt the current kernel and
propagate. A failed disk write is **not** claimed to persist HALT; existing durable
NO_NEW_RISK containment still prevents normal startup when its earlier write
succeeded. Corrupt audit chains refuse reads/startup; forensic restoration and an
independent incident journal remain open. Cached receipts describe the state at
their original attempt, not a new health certificate.

Back up both paper SQLite and Temporal SQLite/history before rollback. Keep the
same workflow definition for open runs; changing replay decisions requires reviewed
Temporal versioning/migration. Disable new workflow starts and retain stopped
paper state rather than deleting incident history.

Remaining P10 work: application/operator wiring, watchdog and broader taxonomy,
durable agent quarantine, candidate repair branches, independent verification,
shadow/canary gates, gated recovery state machine, rollback automation, production
service configuration, retention/monitoring and independent review. P11 evolution
and P13 live release gates remain unfinished.
