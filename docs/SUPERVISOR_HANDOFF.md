# A01 paper specialist supervision

A01 0.2.0 now executes in the persistent LangGraph runtime. Its `TaskPlan`
records the server-owned wake plan, exact specialist versions, deferred versions,
actual child run/context identities, output hashes, typed statuses and mandatory
escalation. The manifest remains PARTIAL and the lifecycle starts in SHADOW.
The original A01 0.1.0 remains PLANNED.

## Run locally

Start the installed local app with an operator token in the backend environment.
The default preview has commands disabled. Use the existing protected endpoints:

1. POST `/api/v1/paper/agents/context` with `agent_id: A01`,
   `agent_version: 0.2.0` and an operator-chosen `task_id`.
2. POST `/api/v1/paper/agents/execute` with a unique `run_id` and the returned
   `context_ref`. Both requests require `x-operator-token`.
3. Read the parent through `/api/v1/agents/graph-runs/{run_id}`. Its output contains
   child run IDs, which use that same protected archival endpoint.

No new endpoint or client-controlled agent-call payload is introduced. The server
installs one `TASK_ORCHESTRATE` handler. It accepts only A01 0.2.0 in PAPER,
an empty payload and the runtime-bound parent context/task identity. Direct
unbound calls, other versions/scopes and supplied task/agent overrides fail.
The existing fixed role ceilings remain authoritative.

## Wake policy and evidence

The bounded local policy chooses supported, executable, non-quarantined versions
in this order: A07 integrity, A06 events, A05 liquidity, A02 regimes, A04 currency
strength and A03 macro drivers. It records planned, unsupported and quarantined
versions as deferred. A health ERROR withholds all specialist calls. LIMITED
specialists can still use their permitted read sources; a LIMITED supervisor
cannot orchestrate. Future mission/version support requires a reviewed catalog
and implementation change.

Four source records bind a common paper-world observation to the catalog,
effective PAPER permissions, health availability/count and checked world.
Catalog versions are unique and ordered; executable versions are restricted to
the installed local missions. Permission records are recomputed from immutable
manifest tools and lifecycle, excluding sandbox tools. The checked paper-world
profile reuses the existing exact world/hash/status validator without changing
its retained source. Source limits are 65,536 bytes each and 160,000 bytes total;
the catalog is bounded to 128 entries. Oversized evidence is withheld rather than
truncated into a different plan.

Health is UNAVAILABLE when no journal exists, RESTORED_ONLY when persisted health
is restored without monitoring, MONITORED when enabled and verified, or ERROR
when the worker retains an error. This reports local journal availability; it
does not assert measured reputation, independently calibrated reliability or
incident resolution. Existing health scans inspect persisted child failures.

Each child receives a newly compiled, separately admitted context using its
existing source adapter and byte budgets. A child's observation can differ from
the planning observation and other children. The supervisor records those context
references and does not combine them into one simultaneous market measurement.
Missing data remains missing: the empty/stale-market replay retains A02
WAIT_FOR_DATA, while other missions describe their explicit evidence gaps.

## Budgets, receipts and failures

There are at most six sequential calls, no nested supervisor calls, and no
automatic retries of failed or uncertain invocations. The immutable A01 manifest
has a five-second ceiling; its actual runtime deadline is the earlier of that
ceiling and the remaining parent context validity. Each child retains its own
manifest deadline and source expiry. The reported validity is no later than the
parent or any completed child's evidence expiry.

Child IDs are derived from the exact parent context and specialist version.
Calls sharing a context are serialized locally and reuse checked original child
receipts without recompiling an already reserved child. Graph reservations and
output/audit binding persist independently of LangGraph checkpoints. Restart and
resume retain exact receipts. Cancellation can leave a child RUNNING; its next
admitted resume records UNCERTAIN and does not invoke it again. A parent timeout
or expiry retains previously created child receipts, even when no final plan is
published. Child IDs remain deterministically derivable from the retained parent
context through the trusted helper.

Current parent permission/expiry and audit/health admission are checked before
each call. Manifest pins and quarantine are rechecked against current server
state. Context compilation failures produce NOT_STARTED_SOURCE; revoked child
access produces NOT_STARTED_PERMISSION. Once reserved, actual typed graph
failures are retained. Tool permission is also rechecked after the awaited handler
returns, withholding a result if orchestration was revoked during the call.

TaskPlan validators bind the exact selected/deferred plan, world, health and
parent identity back to source evidence, reject fabricated child identity,
completion/hash/receipt-presence claims and enforce source-bounded expiry.
Output hashes summarize actual canonical child output bytes; they do not prove
model quality or establish an external audit anchor. Local context locks and
SQLite reservations do not establish distributed orchestration or crash-proof
exactly-once execution.

Confidence remains zero, uncertainty one and escalation mandatory. A01 has no
order, credential, risk-policy, deployment, safety-recovery or promotion tool.
Actual stale-price and spread order blocks remain enforced before and after
supervision by the independent paper Risk Kernel.

## Retained development evidence

`tests/fixtures/supervisor_baseline.json` declares six scenarios: empty market,
fresh synthetic EURUSD quotes/bars, stale quotes, locally imported ECB-format
rates, a deliberately corrupted owned rate archive, and synthetic events.
Every declared status and rate/event availability measurement matched through
the actual protected ASGI API and persisted parent/child graphs. Every retained
receipt matched after app restart. The event scenario also bounds output expiry
by its three-second feed validity.

Run:

```text
python -m ai_quant_os.supervisor_evaluation tests/fixtures/supervisor_baseline.json --output <new-report-path>
```

The command preserves existing reports and exits nonzero for a declared mismatch.
The fixture is bounded to 64 KiB and 32 unique cases. Each case runs the fixed,
trusted evaluator in an owned, reaped process with a 30-second deadline and
provider/configuration environment omitted. This releases FastAPI's cached route
closures and Windows SQLite handles before removing the owned temporary root.
It is a test-process lifecycle mechanism; it supplies no candidate-code sandbox.

The retained report `docs/evaluations/supervisor-baseline.json` pins the fixture,
three named supervisor mission source files and evaluator. The current model
schema is retained in `docs/schemas/task-plan.schema.json`. API composition,
registry/runtime changes, transitive adapters and the complete environment are
not covered by those named source pins. Prior mission and ECB pins remain
preserved. A fresh checkout reproduced an evaluator/recipe line-ending mismatch;
both files now retain canonical LF bytes without changing any source/recipe line,
and regenerated report pins match the actual fresh checkout. This is development evidence with synthetic worlds/events and
disclosed local CSV imports, not a held-out quality result or a live-rate receipt.

## Remaining scope and rollback

Adaptive scheduling, resource/cost governance, model routing, reputation,
independent calibration, distributed/Temporal supervision, complete remaining
missions, A01 repair/evolution integration, promotion and every live-release gate
remain open. The wider execution-pack build is unfinished.

To roll back, stop the owned local app and preserve the complete state root,
including parent and child graph/context/audit receipts. Revert this increment.
No shared database migration is introduced. Older code lacks A01 0.2.0 and may
require a compatible reader for those receipts; the planned original version is
not an executable rollback. Existing specialist versions remain retained.

## Verification

163 focused supervisor/runtime/permission/macro/API checks passed in 111.82
seconds. Ruff passed and all 268 Python files were formatted. Both 96-package
locks reproduced; dependency consistency and previous strategy/artifact/genome/
protocol compatibility checks passed. The full regression passed with 1,285 checks and 15 skips in 980.31 seconds, with actual built-frontend integration required. A fresh index checkout verified the corrected named source/fixture/evaluator pins, all six schema-valid outputs and prior mission/ECB preservation. Staged changes and all 84 prior commits passed secret scans. Manual import review passed and all five pure domain modules remain unchanged; automatic boundary checking remains unavailable without its declaration. Details are recorded in BUILD_STATUS.
