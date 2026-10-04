# Owned prefix worker execution

## Scope and invocation

The operator controller adds `execute` to
`python -m ai_quant_os.strategy_container_cli`. Use the same original validation
archive/package/index, stage hash and initial ownership/create hashes as creation,
plus the independently pinned image archive/base/source inputs. The container must
already have its exact CREATE_ACK; execution does not create, pull or load an image.
Use the subcommand's `--help` for argument syntax. Execution is explicit and is not
connected to orders, agent promotion, a background scheduler or live capital.

`execute_strategy_prefix(plan, job, package, archive=..., **pins)` independently
re-inspects the immutable image/archive/engine before entering one cooperative root
custody session. The session verifies the original request and sidecars, current
audited create acknowledgement, actual ownership and fixed created-container
recipe again before start. A closed session refuses further operations.

Before one `container start --attach EXACT_ID`, it exclusively publishes/fsyncs
`JOB_NAME.start-attempt.json`. The canonical request binds owner, original create
hash/invocation, immutable container ID, fixed arguments, 45-second attachment
transport timeout and 65,536-byte combined transport output bound. No configurable
weaker limits or stdin are accepted. Existing/partial markers prevent another start
request. This protects process restart under the trusted host/cooperative writer
assumption; power-loss directory-entry durability remains unverified.
Docker documents that [start --attach connects STDOUT/STDERR](https://docs.docker.com/reference/cli/docker/container/start/).

## Exit, target and owned cleanup

The attached command must succeed with no stderr. Stdout must be one exact canonical
CandidateTargetOutput of at most 2,048 bytes, a strict unit integer and the original
invocation reference. Its existing UNATTESTED_TARGET_OUTPUT/false isolation and
promotion contract is preserved byte-for-byte. Extra text, duplicate/non-finite
JSON, foreign references, boolean targets or claimed authority are refused.

A separate owned-container inspection must report exited, strict false
running/paused/restarting/OOM/dead flags, PID and exit code zero, no error and valid
ordered nonzero UTC start/finish clocks. This selected exit metadata is bound by
its canonical hash. It is daemon-reported metadata, without independent kernel
attestation or proof of proposed source correctness.

On every attempted start path, including transport deadline/overflow/failure,
output refusal and source-input change, the parent attempts independently owned
kill/removal in a finally boundary. Killing the parent Docker command alone is not
container termination. A running owned container receives SIGKILL by its exact
inspected ID; an uncertain kill can be superseded only by independently inspected
force-removal and its exact successful acknowledgement. Foreign identity or unknown
absence refuses mutation. Removal intent/acknowledgements use the existing journal.
Docker documents [force removal's SIGKILL behavior](https://docs.docker.com/reference/cli/docker/container/rm/).

The 45-second limit bounds the attached command transport, not a certified hard
wall-clock deadline for remote daemon/container termination. Metadata/kill/removal
commands have separate bounded transport budgets. An unavailable/uncertain daemon
can leave retained custody requiring operator reconciliation; no output is released
without a confirmed owned removal. No background retries or restart of a candidate
are performed. CPU/memory/process restrictions and the unchanged worker's early
runtime guard remain independent prerequisites, without actual enforcement proof.

Only after successful target/exit validation, exact REMOVE acknowledgement and
another original-input check are target and receipt exclusively published/fsynced
outside the worker mount: `JOB_NAME.target.json` and `JOB_NAME.execution.json`.
Publication spans files/SQLite and can leave partial retained output if interrupted.
No automatic overwrite, partial repair, lease release or input deletion occurs.
The original request and all ownership/start sidecars remain.

## Receipt and restart

The strict StrategyPrefixExecutionReceipt/model/JSON Schema binds complete
ownership, original create hash, exact start-request hash, original target/hash,
selected exit metadata/hash and the current exact audited REMOVED record. Its state
is UNATTESTED_PREFIX_COMPLETED_AND_REMOVED. Execution/promotion authority and target
isolation remain false. JSON Schema checks are supplemented by model cross-field,
chronology/hash checks and custody validation.

Retain the initial receipt hash independently. The library's
`restore_strategy_prefix_execution(..., receipt_sha256=...)` rechecks the original
plan/package/job, target/start bytes, receipt canonical form/hash and current audited
removal. When the owner is REMOVED, restoration is offline: no daemon call or start
occurs. Altered targets, rehashed unrelated start bindings and wrong independent
pins refuse restoration. Local records are not independently immutable storage or
administrator-resistant provenance.

## Remaining work

The fixed Docker Linux endpoint remains unavailable. All new start/output/cleanup
observations were simulated; no proposed source or SDK was imported/compiled/run on
the host, and no actual image import/container start/kill/removal was performed.
Actual engine/mount/default compatibility, running kernel enforcement, independent
source/behavior attestation, separate no-holdout proposed compilation/tests,
statistical acceptance, physical holdout isolation, safe custody release/partial
recovery, execution-to-tape/backtest assembly, full missions and all production,
A10 acceptance and promotion gates remain open. See ADR 0075 and BUILD_STATUS.md.
