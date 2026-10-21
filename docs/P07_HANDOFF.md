# P07: LangGraph runtime and agent missions

## Current HOLDOUT service state (2026-10-21)

The Windows service now verifies A10 validation artifacts and A11 archived
stress reports from service-owned stores. First install stages verified copies
of the existing HOLDOUT, validation-package, candidate-validation, and research
SQLite archives, then applies the service-only ACL to the service data tree.
Stop the PAPER API and research writers before installation so SQLite snapshots
have stable sources.
Installation requires `--data-dir`, `--paper-db`, and `--research-workspace`;
the HOLDOUT and two validation archives are derived from the PAPER database
path, and the research archive is read from the workspace. Sources remain
untouched. Staging requires an empty service data directory and refuses
unrelated files. Existing complete service archives are preserved on
re-install, and partial archive sets are refused. The focused
service/RPC/API/security set passes 77 tests with Ruff and format checks clean.
Installation has not been run under
Windows service credentials or checked against actual file ACLs. The PAPER API
still prepares package/report contexts from local archives, so service-owned
copies alone do not prove the isolation boundary.

The A10 context source builder now also accepts only a validated summary, its
HOLDOUT receipt, and the package capture time, without requiring a local package
or artifact object. This is a transport-ready boundary refactor; the API still
uses its local stores, so no access or isolation claim changes. Its focused
contract test passes. The broader validation-source module retains setup errors
because its shared package fixture publishes HOLDOUT through the TRAINING-only
route; service-owned package preparation is still required.

The PAPER API's A10 validation-context, A11 stress-context, and A01 0.8.0
archive-existence checks now use service RPC snapshots. A10 receives only the
validated summary, HOLDOUT receipt, and package capture time. A11 receives typed
candidate, validation, stress, and HOLDOUT-scope metadata without bars or fills.
These new operations use the versioned `AIQuantHoldout.v3` pipe; clients and
service must be upgraded together. The API no longer opens the local research
report archive to compile A11 context. Package creation and validation execution
still use local validation stores, so HOLDOUT isolation is incomplete. Focused
context, service, RPC, and A11 API tests pass; Windows service-token and file
ACL acceptance remains untested.

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

Current verification: the latest full locked Windows suite completed with
**3,274 passed, 47 skipped, and one setup error**. The error was the research
context fixture receiving `DEGRADED` during
`test_exact_request_is_persisted_then_created_and_admitted_once`; the same test
passes when run alone twice, so the cause remains undiagnosed and the full suite
is not currently a clean pass. The focused A02/graph/broker-boundary suite
including the API failure-containment regression passes **43 tests**. Scoped
Ruff check, format check, and `git diff --check` pass. The runs used Pydantic
2.13.5 and Pydantic Core 2.46.5.

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
