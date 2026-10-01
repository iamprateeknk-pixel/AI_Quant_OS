# Temporal paper uncertainty triage

P10 now has an initial durable runbook for an uncertain **server-owned PAPER order**.
An opt-in HTTP application runtime now starts the worker with the same execution
gateway, RiskKernel, state store and control lock as operator controls. Default
configuration remains disabled.

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

Also run `tests/test_recovery_api.py` and `tests/test_temporal_recovery_api.py` for
protected command/configuration checks and actual application startup, restart,
shared-kernel mismatch containment and unreachable-service startup refusal.
The dedicated Windows/Linux CI job downloads checksum-pinned binaries and treats
missing executables as failures. Ordinary installations without the optional SDK
or executable skip only the real-service tests. Hosted CI results are unverified.
Local tests use the full native dev service, not a mocked workflow environment.
The SDK documents this SQLite-backed local service in
[WorkflowEnvironment](https://python.temporal.io/temporalio.testing.WorkflowEnvironment.html).

## Run the owner-bound application

Start the extracted native service with a persistent SQLite file, for example:

```powershell
temporal server start-dev --ip 127.0.0.1 --port 7233 --db-filename D:/AI_Quant_OS/data-local/temporal.sqlite3 --headless
$env:AI_QUANT_TEMPORAL_RECOVERY_ENABLED = "1"
$env:AI_QUANT_TEMPORAL_TARGET = "127.0.0.1:7233"
```

Set `AI_QUANT_PAPER_DB` and `AI_QUANT_OPERATOR_TOKEN` using the existing local
application setup, install the `recovery` extra, and start the API. Enabled startup
requires persistent storage, an operator token, a literal 127.0.0.1 address and a
valid port. Remote/unauthenticated Temporal deployment is not supported by this
local configuration. Missing dependencies or unavailable required service refuse
startup. Connection/worker runtime failures escalate the owning kernel to HALT and
attempt audited persistent containment; normal shutdown drains worker activities,
including repeated cancellation while a state-write thread is running. Forced
process/event-loop termination cannot guarantee completing disk I/O.

Every new route requires `X-Operator-Token` and valid audit integrity:

| Command | Behavior |
| --- | --- |
| `GET /api/v1/paper/recovery` | Disabled or ready owner runtime; never promotion authority |
| `POST /api/v1/paper/recovery` | Strict `incident_id` and `order_id`; contain before RPC dispatch; 202 for submitted workflow |
| `GET /api/v1/paper/recovery/{incident_id}` | Server-owned admission plus actual Temporal state/result |
| `POST /api/v1/paper/recovery/{incident_id}/retry` | No verdict payload; retry only while the workflow is waiting |

Active/completed duplicate starts return 409. Unconfirmed start RPC returns 503
while containment remains; retry the same incident identity to resolve ambiguity.
Containment/attempt receipts survive app restart, and workflow IDs and task queues
are scoped to the canonical paper file path. Preserve that path when restoring;
moving a database requires a reviewed workflow identity migration. Run a single
application owner process per paper file. Multiple app processes/hosts and leases
remain unsupported.

The separate `/control/recover-paper` still requires both distinct operator and
recovery tokens, verified positions and resolved orders. It now additionally
requires every recorded triage incident's latest attempt to be REVIEW_REQUIRED.
Failed, unresolved or not-yet-run triage blocks normalization even if its target
order was independently acknowledged. This initial manual gate is not full P10
independent repair certification or shadow/canary approval. HALT cannot normalize.

## Library integration contract

Construct `PaperRecoveryService(execution, risk, state)` using the **same instances**
as the owning application. Register `PaperRecoveryWorkflow` and
`RecoveryActivities(service).registered()` on a dedicated Temporal Worker queue.
Use `start_paper_recovery(client, incident_id, order_id, task_queue=...)` from a
trusted, authenticated operator composition root. Reusing a running or completed
incident ID is refused. The starter validates shape; the activity validates actual
ownership. The `retry` signal accepts no client verdict or evidence.

The application implements this binding. Do not run an independent worker against a live application's SQLite file: its
separate in-memory RiskKernel cannot stop that application. Production wiring must
bind the owner and worker together, authenticate Temporal transport/commands, and
restrict workflow/activity access. The local HTTP routes grant no agent permission.

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

Remaining P10 work: full operator sessions, watchdog and broader taxonomy,
durable agent quarantine, candidate repair branches, independent verification,
shadow/canary gates, gated recovery state machine, rollback automation, production
service configuration, retention/monitoring and independent review. P11 evolution
and P13 live release gates remain unfinished.
