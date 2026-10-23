# P07: LangGraph runtime and agent missions

## Current HOLDOUT service state (2026-10-23)

The Windows service now verifies A10 validation artifacts and A11 archived
stress reports, prepares immutable A10 validation packages, and serves their
checked metadata from service-owned stores. Package preparation sends the
checked graph, candidate, plan, TRAINING receipt and bounded TRAINING snapshot
to the service; HOLDOUT bytes are fetched only by the service and never returned
to the API. Package routes no longer open a local validation-package archive.
The service returns summary metadata and a digest, and refuses conflicting
immutable package identities through the bounded RPC error contract. API tests
exercise the framed RPC client and service handler together. The retained A10
result endpoint now also reads a checked summary through service RPC; the PAPER
API no longer constructs or reads a local candidate-validation archive.

First install now gives the empty staging directory a protected ACL for the
validator service, SYSTEM, and the confirmed operator SID. Verified copies of
the HOLDOUT, validation-package, candidate-validation, and research SQLite
archives inherit that ACL. The installer then removes the temporary operator
entry and verifies the final service/SYSTEM-only ACL through read-control
handles opened before the owner change. Stop the PAPER API and research writers
before installation so SQLite snapshots have stable sources.
Installation requires `--data-dir`, `--paper-db`, and `--research-workspace`;
the HOLDOUT and two validation archives are derived from the PAPER database
path, and the research archive is read from the workspace. Sources remain
untouched. Staging requires an empty service data directory and refuses
unrelated files. Existing complete service archives are preserved on
re-install, and partial archive sets are refused.

The 2026-10-23 workstation probe used only four explicitly synthetic empty
SQLite archives. It verified the owner and final DACL on the staged files and
directory using Windows security handles, and a normal operator-process read of
the raw HOLDOUT file returned access denied. The service registered as
`NT AUTHORITY\LocalService` but failed to start with SCM error 1053, so service
token, named-pipe RPC, and service-process access acceptance remain open. The
installed Python 3.11 runtime is under the operator's profile and its ACL does
not grant LocalService read access; this is a confirmed deployment blocker and
likely startup cause, though the exact launch failure was not captured. The
temporary test service was removed and its synthetic data was preserved. Real
source archives are absent, so migration was not attempted. The installer
regression forces ACL failure and confirms that service startup is withheld and
source archives remain byte-identical; its Windows service calls are mocked.
The service stores and reads candidate-validation records. The
operator CLI has an explicit `--service` archive mode that sends canonical
proposed-test and run reports over one bounded RPC; the service loads its own
package, checks its HOLDOUT receipt and bytes, recomputes the artifact, and writes the
audited result
to its own archive. Only the checked result reference and compact summary digest
return to the CLI. Requests larger than the 2 MiB frame limit fail closed. The
legacy local archive mode remains available for offline work. HOLDOUT ingestion
and append-only refresh now use `ai-quant-holdout-data publish` directly over
the bounded service RPC. The command pins the CSV, reads only checked TRAINING
receipt metadata, and sends raw HOLDOUT bytes to the service without routing
them through the PAPER API. The ordinary `ai-quant-research-data` command is
TRAINING-only. The focused P07 regression group passes **85 tests** after the
installer-path test was added; the complete locked Windows backend suite now
passes **3,314 tests with 47 skipped in 1,745.35 seconds** on Pydantic 2.13.5
and Core 2.46.5, including that regression. That run skipped the opt-in Docker
worker integration because the Linux engine was unavailable at the time. On
2026-10-23, the pinned local Linux engine group passed **89 tests in 762.31
seconds**, including the evolution worker, candidate windows and validation,
repair containers, strategy prefixes, and worker-enforcement probes. Docker
Desktop was stopped after the run and no test containers remained. Service installation under
LocalService, actual ACL/process-access acceptance, and end-to-end migration of
real source archives remain open. These tests verify application behavior and
mocked installer orchestration only; no OS isolation or full P07 acceptance
claim is made.

The A10 context source builder now also accepts only a validated summary, its
HOLDOUT receipt, and the package capture time, without requiring a local package
or artifact object. The API compiles A10 context from that service snapshot and
the retained-result route returns only a checked summary. The package fixture
now exercises service-owned package, validation, and HOLDOUT stores over framed
RPC. The complete suite includes those updated routes and fixtures.

The PAPER API's A10 validation-context, A11 stress-context, and A01 0.8.0
archive-existence checks now use service RPC snapshots. A10 receives only the
validated summary, HOLDOUT receipt, and package capture time. A11 receives typed
candidate, validation, stress, and HOLDOUT-scope metadata without bars or fills.
These operations use the versioned `AIQuantHoldout.v4` pipe; clients and
service must be upgraded together. The API no longer opens the local research
report archive to compile A11 context. Package preparation and validation/stress
execution also use service RPC. The operator-side validation CLI publication
path now sends reports for service-side recomputation and audited storage.
HOLDOUT ingestion and refresh are implemented; source-archive migration,
service installation, and Windows service-token and file-ACL acceptance remain
open.

## Pack acceptance status

The source of the five acceptance checks below is the supplied P07
`ACCEPTANCE_CHECKLIST.md`. Evidence is from the current repository and the
locked Windows run recorded in `BUILD_STATUS.md`.

| Requirement | Status | Evidence |
| --- | --- | --- |
| Agents cannot call broker controls directly | Verified | `tests/test_architecture_broker_boundary.py`; broker/control imports are checked against the agent source inventory. |
| Timeouts degrade gracefully | Verified | `tests/test_graph_runtime.py::test_failures_withhold_output_and_never_repeat` covers TIMEOUT and other terminal failures. |
| Context provenance is retained | Verified | `tests/test_agent_context.py`, `tests/test_context_checkpoints.py`, and `tests/test_graph_api.py::test_graph_api_uses_archived_context_and_retains_failure_on_restart`. |
| Agent outputs are schema validated | Verified | `tests/test_graph_runtime.py::test_runtime_rejects_output_schemas_that_ignore_unknown_fields` and the typed handler/runtime tests. |
| Agent failure does not crash trading core | Verified at the local API boundary | `tests/test_regime_agent.py::test_agent_failure_is_contained_while_paper_core_remains_available` forces an A02 handler exception, then verifies a sanitized FAILED receipt, responsive system/orders routes, and NORMAL risk safety. `tests/test_graph_runtime.py` and `tests/test_graph_api.py` cover runtime failure statuses and persistence. This is application-level failure containment; it does not certify process isolation. |

Current verification: the full locked Windows backend suite completed with
**3,314 passed and 47 skipped in 1,745.35 seconds**. The run used Pydantic
2.13.5 and Pydantic Core 2.46.5. Separately, the opt-in Docker engine set passed
**89 tests in 762.31 seconds** on the pinned Linux worker image; the Docker
engine was stopped after verification. Ruff lint and format checks plus
`git diff --check` pass for the current changes. The focused
A02/graph/broker-boundary suite including API failure containment previously
passed **43 tests**.

## Implemented runtime

The repository has all 18 initial agent manifests, a versioned registry,
capability checks, bounded context compilation, source-bound evidence,
LangGraph-backed durable runs, strict output validation, and checked restart and
resume. Agent plans and results remain advisory. A01 0.6.0–0.9.0 provide
separate typed research, validation, and reviewed-construction cohorts; the
latest cohort can only use an already active operator review to create an inert
A09 candidate. Its candidate has execution and promotion disabled.

## Remaining P07 work

Pack acceptance checks do not by themselves establish the full P07 build scope.
The following work remains open:

- Convert partial shadow missions into accepted first production-grade
  versions, with mission-specific evidence and datasets for all 18 agents.
- Complete general agent model routing and independently evaluated quality,
  confidence calibration, cost and reliability metrics. Existing Jev/provider
  routes and operational run counts are not full agent model routing or
  prediction calibration.
- Independently separate TRAINING and HOLDOUT access by process identity. The
  data catalog stores the raw classes in separate SQLite files, but the local
  application identity can still open both. A08's reader avoids selecting raw
  HOLDOUT bytes; this is not an OS access-control boundary.
- Obtain independent review and acceptance evidence for the runtime, datasets,
  each mission, and any model-quality claim.

## Earlier implementation notes (historical)

The notes below record intermediate states from the implementation timeline.
Statements about unimplemented handlers, local API archives, or old test
failures are historical; the current service state and verification above
supersede them.

No promotion or live capital authority follows from this handoff. ADR 0222
specifies a Windows `LocalService` process with a unique service SID,
service-owned HOLDOUT directory, and bounded ACL-protected local RPC returning
summaries only. The service host, installer, and A10/A11 domain handlers are
implemented but remain uninstalled. Startup requires the HOLDOUT store and the
validation package, candidate validation, and research archives staged under
the protected service data directory. Test it at the actual process and
file-permission boundary before describing it as isolated. Until then, keep
`independent_holdout_storage` and `isolation_verified` false. P07 and the
P00–P13 release remain incomplete.

The initial HOLDOUT RPC message boundary is implemented in
`src/ai_quant_os/holdout_validator_rpc.py`: fixed A10/A11 operation bindings,
strict request and response schemas, a 64 KiB framed JSON limit, aggregate-only
result fields, and request/task/context/operation correlation. Service and
client transport now use this contract. The PAPER API routes both A10 and A11
tool calls through isolated RPC adapters, but service-owned stores and domain
handlers remain unimplemented, so no isolation claim changes.

The Windows ACL provisioner in `src/ai_quant_os/holdout_service_security.py`
builds a protected DACL containing only the per-service SID and SYSTEM, sets
the service SID as owner, and reads back the owner and exact ACE set before it
returns. It also builds a pipe DACL limited to the service SID, confirmed
operator SID, and SYSTEM. The operator SID is read from the current process
token, not supplied as an arbitrary configuration value. The provisioner also
checks every existing database, WAL/SHM sidecar, and backup ACL, and refuses a
tree whose children do not already match. First installation explicitly adopts
only the four verified files in a previously empty staged service directory by
applying and then reading back the exact service/SYSTEM DACL; re-installation
uses strict verification without ACL adoption. This flow has not been run on
Windows against real archives; API access and cross-identity Windows tests
remain open.

The Windows pipe client now performs one overlapped exchange under a single
deadline, checks the live server process token for the expected service SID
before writing, rejects oversized frames, and never retries. After receiving a
bounded response, it sends a fixed receipt acknowledgment; the server waits for
that acknowledgment before disconnecting so Windows cannot discard unread
response bytes. A Windows
integration test starts a fake pipe under the interactive account and confirms
the client rejects it before sending request bytes. The focused RPC/ACL/service
set passes 26 tests, including repeated live pipe transfers. This verifies
spoof rejection for a same-user process and bounded delivery acknowledgment;
successful service-token RPC and service-owned data access still require
running the installer and testing the installed service.

Server-side `process_rpc_frame` now drops malformed frames and only serializes
validated, identity-bound results. Permission, timeout, and internal failures
map to fixed codes; exception text and request contents are never included.
The service host mounts this handler, but its current domain handler returns
`SERVICE_UNAVAILABLE` until store ownership and A10/A11 operations are wired.

The RPC response contract also carries A11's bounded per-scenario totals:
scenario name and dataset fingerprint, exact decimal PnL, quote currency, and
native order count. It refuses scenario count mismatches and keeps the response
free of bars, fills, and arbitrary extra fields. Validation and stress response
shapes are checked against their operation. The service still has no domain
handler. API tool execution now uses the isolated A10 and A11 adapters with no
local archive fallback; context compilation and package/report preparation
still read local archives, and the service therefore cannot yet complete either
operation.

`HoldoutEvidenceStore` is a read-only, integrity-checking view over the separate
HOLDOUT SQLite file. It verifies the complete audit chain, every HOLDOUT receipt
against its raw bytes, event bindings, tombstone absence, and database bounds.
The LocalService host now opens the file from its protected data directory and
holds the read-only connection for its lifetime. The focused service/store/API
group passes 70 tests and the service-store tests cover corrupted raw bytes and
write refusal. The main API now creates its research catalog in TRAINING-only
mode. It never opens the sibling HOLDOUT database, exposes TRAINING metadata and
rows only, and rejects HOLDOUT publication with a schema error. The focused
catalog, research API, hypothesis API, and service-store set passes 45 tests,
including a guard that fails if the training-only catalog opens the HOLDOUT
path. This is an application access reduction; the existing HOLDOUT file and
validation-package copies have not been migrated or protected by the service
ACL. A10/A11 service handlers, file migration, and process/file ACL acceptance
remain open, so isolation stays false. A verified staging helper
now snapshots a legacy HOLDOUT SQLite file into an existing destination folder,
checks the audit chain and raw-byte identities before and after backup, creates
the destination atomically without overwriting, and keeps the source intact.
Tests cover exact contents, idempotent identical staging, corrupt sources,
conflicting destinations, and hard-link refusal. This helper has not been run
on operator data, does not set ACLs, and is not yet wired into service
installation.
Training-only startup now detects legacy HOLDOUT rows from receipt metadata
before selecting any raw bytes and gives an offline-migration error. Run
`python -m ai_quant_os.holdout_validator_service migrate-legacy-catalog --catalog <absolute-path>`
while the PAPER API is stopped to split those rows and retain their receipts.
The command reports migration only; it does not install the service, migrate
the resulting HOLDOUT file into the service directory, or establish ACL
isolation. Tests cover the migration command, successful subsequent
TRAINING-only startup, and rejection before corrupt legacy HOLDOUT bytes are
read.

The RPC client now exposes the service's three fixed failure categories through
`HoldoutRpcError.error_code` while keeping exception text limited to that
allowlisted code. Callers can distinguish request refusal, service deadline,
and service unavailability without receiving raw exception details. The
focused RPC, service, and read-only store tests pass 24 cases. The API now routes
A10/A11 through this client; the service operations still need implementation.
The exact evidence-reference field is a wire-contract change, so the pipe name
is now versioned as `AIQuantHoldout.v2`. The installer and client must ship the
same protocol version; the client does not retry the new request against v1.

An `IsolatedValidationRun` adapter now builds a strict A10 request from the
compiled validation summary, calls the RPC client in a worker thread, and checks
the correlated response and aggregate against the exact summary. The RPC
request and response now bind the exact archive evidence reference as well as
request, task, and context identities. A synthetic context test passes. The API
registers this adapter for `VALIDATION_RUN`; it no longer falls back to the
legacy in-process `ValidationRun` at tool execution. The production client uses
the installed Windows service identity and returns a fixed unavailable failure
when Windows or the service identity is unavailable. The service still has no
A10 domain handler, so calls remain unavailable until package/result ownership
and service verification are implemented. Context compilation and validation
package preparation still use local validation stores, so this change does not
establish HOLDOUT isolation. Existing API-backed validation package fixtures
also still attempt HOLDOUT publication through the TRAINING-only PAPER route;
package preparation and ownership must move to the service before those flows
can pass end to end. An A11 `IsolatedStressRun` adapter now binds report,
TRAINING/HOLDOUT scopes, task, and context, and checks the bounded scenario
aggregates against the compiled context. The API registers both isolated tool
adapters with no local-store fallback at execution. The adversarial, RPC, and
API group passes 61 tests; Ruff and formatting pass. The A11 changes still need
independent review. The
package-dependent validation-source suite still has nine setup errors from that
fixture mismatch.

The service now mounts `HoldoutRequestHandler`. A10 verifies artifact and
package lineage, exact dataset references, the service-owned HOLDOUT receipt,
and byte-for-byte snapshot identity before returning the compact validation
digest/outcome. A11 verifies the archived report digest and dataset-scope
references, binds adversarial source bars to exactly one matching service-owned
HOLDOUT receipt and raw CSV, rechecks native adversarial evidence, and returns
bounded scenario totals. A10 scope-mismatch and missing A11 receipt coverage
reject before returning summaries. The focused service/RPC/API group passes 58
tests with Ruff and `git diff --check` clean.
Service archive staging/migration is not wired into installation, so normal
operator data is not yet available to the service. Package preparation and
report context compilation still read local archives; the service has not been
installed or exercised under its Windows service identity and ACL. Isolation,
P07 completion, and P00–P13 release acceptance therefore remain open.
