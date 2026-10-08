# Durable original-source validation jobs

The local operator controller retains a byte-pinned job, an audited stage journal,
original worker custody and a checked result archive. A separate optional Temporal
adapter dispatches one stage at a time. A10 continues to read archived observations.
Every job, stage and workflow result has `promotion_allowed=false`.

## Preparing and running

Use `python -m ai_quant_os.candidate_validation_job_cli --help` in the locked
environment. Global arguments are `--validation-archive`, `--package-ref` and
`--root`. The package archive must already contain the original package. The root
must be a new ordinary empty directory; preparation returns `job_sha256`.

| Command | Additional inputs | Effect |
| --- | --- | --- |
| `prepare` | Original image archive, base metadata and all six image/worker pins; optional quantity, balance and canonical policy JSON/SHA | Stages original source-only proposed tests and immutable job declaration |
| `status` | Independent `--job-sha256` | Checks root, declaration, source-only request and audited journal |
| `step` | Job SHA and `--record-archive` | Executes exactly one next stage, or verifies retained final publication |
| `recover` | Job SHA, result archive and independent `--result-sha256`; `--stage-manifest-sha256` if its acknowledgement is missing | Checks original retained custody and acknowledges an observed result |
| `workflow` | Job SHA, result archive, explicit `--run-id`; optional `--temporal-endpoint` | Runs the remaining stages on an already running local Temporal server |

Image inputs use the existing strategy image admission. Proposed source, tests and
SDK execute only in the fixed admitted Linux workers. Native replay consumes checked
numeric observations. The source-only test stage runs first. Failed compilation or
proposed tests skip numeric stages and publish the original prerequisite failure.
Otherwise HOLDOUT precedes every ordered development window and checked scorecard
publication. No folds or Monte Carlo trial counts are reduced.

Temporal is optional for `prepare`, `status`, `step` and `recover`. The workflow
command requires the locked recovery extra and a prestarted server at literal
`127.0.0.1:port` or `localhost:port`. The command registers a worker for the exact
job/run task queue, with one concurrent activity, and waits for its result. Local
Temporal server database/history and job/result archives must be retained together.
This is explicit operator dispatch; there is no new HTTP source-execution route.

## Uncertainty and cancellation

A durable RESERVED/PREPARED entry precedes each dispatch. An exception retains
UNCERTAIN and the original pins. The controller refuses automatic repetition;
Temporal activities have maximum attempts one. A lost result acknowledgement can
be reconciled only with independently supplied pins and original retained worker
results. Recovery never starts a worker or creates an absent publication. Missing
or corrupted metadata cannot be replaced by reinitializing the root.

An unfinished numeric stage first requires the existing owned-container and prefix
reconciliation procedures. Resolve the retained child run under its original
manifest and ownership pins, then supply that complete report's independent hash
to this job's `recover` command. The outer journal cannot turn an unfinished child
prefix into a completed report. An unresolved outcome remains retained uncertainty.

Cancellation requests a cooperative stop. The owning thread retains the kernel
root lease until the current owned prefix reaches completion and cleanup. Safe
checkpoints occur before another prefix or native replay is reserved. A typed
PAUSED stage resumes only the remaining prefixes using the same admitted manifest.
Restart with a new explicit Temporal run ID. Each job permits sixteen cooperative
pauses across all stages; exhausting that budget retains UNCERTAIN. This controller
does not infer safe cleanup from a failed, lost or unconfirmed worker call.

Activities heartbeat every five seconds, expire after sixty seconds without a
heartbeat and have a seven-day start-to-close limit. Workflow execution is bounded
to fourteen days and the original stage count. Duplicate workflow identities are
refused. Worker loss, invalid activity metadata or stage failure produces
WAIT_FOR_PINNED_RECONCILIATION without a fabricated result. Resolved jobs can use a
new run ID; the original uncertain Temporal history remains retained.

## Remaining scope

The journal observes local stage uncertainty; it is not independent source/daemon
attestation or an agent acceptance certificate. Early worker failure classifications,
larger declared computation plans, physical/statistical holdout isolation,
independent adversarial/reality-gap/champion/shadow acceptance, complete agent
missions and production release gates remain required. The P00–P13 build remains
incomplete. Verification evidence is retained in
`docs/evaluations/validation-job-0087.json`.

## Verification

The full regression passed 2,003 tests with 15 optional integrations skipped,
enabling all six existing local web/Linux gates and required Temporal checks. The
actual original-source job recovered a lost test acknowledgement without test
restart, completed holdout and both declared development folds, and published a
checked scorecard through four remaining Temporal activities with attempts one.
The original job, final stage, workflow result and archive were revalidated.

Final focused verification and a fresh staged checkout each passed 42 checks,
with the opt-in actual-worker case covered by the full suite. Cancellation,
repeated pauses, the sixteen-pause limit, server restart, duplicate starts, malformed
reply/stage refusal, uncertain preflight and fresh-process commands were checked.
All 27 prior wire schemas and 311 unchanged production/infrastructure/schema
artifacts match the previous commit. Formatting, lint, structure and staged secret
checks passed; tested source, tests and schemas match the index and fresh checkout.
