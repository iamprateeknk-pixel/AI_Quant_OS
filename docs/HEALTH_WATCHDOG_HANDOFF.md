# Local health watchdog and durable agent quarantine

This P10 increment detects failures from server-owned paper and agent sources. It
is disabled by default and provides no repair, normalization, deployment or live
order authority.

## Activate

Use the existing local API setup with a persistent `AI_QUANT_PAPER_DB` and an
operator token, then set `AI_QUANT_HEALTH_WATCHDOG_ENABLED=1`. Only `0` and `1`
are accepted. Temporal is optional: this watchdog does not automatically start or
retry a Temporal workflow. The protected recovery commands remain available when
their separate configuration is enabled.

The journal is a sibling `<paper-stem>.health.sqlite3`. Startup verifies it and
restores safety and exact manifest/version quarantine before graph use. Disabling
monitoring later does **not** clear existing journal pins or safety state. Every
operator admission additionally verifies an existing health journal; tampering
blocks commands and halts the owning kernel even with monitoring disabled.

Enabled startup scans the complete bounded graph ledger before serving requests.
The worker checks paper sources every cycle and verifies up to 64 graph records
per page, including healthy records. It rotates across at most 10,000 graph runs.
Each healthy cycle waits one second; errors back off up to sixty seconds. Actual
SQLite/verification duration adds to this interval, so this is not a hard realtime
deadline. Terminal receipts from API execution/resume are observed immediately.

Normal shutdown and startup cancellation drain in-flight state operations, including
repeated cancellation and late write failure. Failed required Temporal startup
cleans graph/worker references and never leaves a watchdog task running. Forced
process/event-loop termination cannot guarantee completing disk I/O. The API
lifespan holds a crash-released kernel owner lock for the persistent PAPER root,
so a second API process using that root is refused before serving requests. This
is local-filesystem single-owner coordination, not a distributed or multi-host lease;
other programs writing the SQLite files must not run concurrently.

## Failure taxonomy

| Observation | Action |
| --- | --- |
| AUDIT_INVALID | HALT in the current kernel; record evidence in the independent health journal |
| GRAPH_INTEGRITY_FAILED | HALT; retain a sanitized source-read failure incident |
| POSITION_MISMATCH | HALT; retain local-only, broker-only and conflicting fill identities |
| CLOCK_REGRESSION | HALT; retain the observed local time regression; no NTP/offset measurement is claimed |
| WATCHDOG_FAILURE | HALT; attempt the owning paper audit as fallback for monitor/storage failure |
| BROKER_UNCONFIRMED | NO_NEW_RISK; retain UNKNOWN/DISPATCHING reservation; never resend or reconcile automatically |
| QUOTE_STALE / QUOTE_FUTURE / QUOTE_UNTRUSTED | NO_NEW_RISK; retain the actual quote snapshot; five-second freshness matches the current paper kernel |
| AGENT_FAILED / AGENT_TIMEOUT / AGENT_INVALID / AGENT_UNCERTAIN | NO_NEW_RISK plus exact-version, exact-manifest quarantine |

Higher existing safety states are preserved. Both health and paper recovery stops
hold kernel authority through the owning audit commit, preventing a new approval
between persistent containment and the in-memory transition. Missing quotes and ordinary
CREATED/RUNNING/WAIT_FOR_DATA/UNAVAILABLE/EXPIRED/DENIED agent states do not by
themselves claim failure or quarantine. An orphaned RUNNING run becomes UNCERTAIN
through the existing explicit graph resume semantics; automatic orphan resolution
is not implemented. The watchdog does not claim missing-provider or idle-feed
health, production health coverage, agent performance scoring or clock calibration.

## Evidence and permissions

Incidents retain immutable first-observation time, canonical source evidence and
computed content identities. Exact agent pins come from verified graph receipts.
The separate journal uses transactional audit chaining, record hashes, canonical
identity and audit binding, with a count/head check that detects deleted records.
Concurrent duplicate observations adopt the original timestamp without adding
another incident. Ordinary artifact-body rehashing cannot change the audit binding.
These local hashes are integrity checks, not protection against an actor controlling
all journal files and recomputing the complete chain.

There are at most 1,000 incidents, 16 KiB of source evidence per incident and 32 KiB
of serialized record. Capacity/storage failure halts; it never deletes evidence to
make room. A failed write cannot guarantee recording HALT on that same failed disk.
The fallback attempts the paper audit, and current-process HALT is unconditional.
Corrupt journal restore refuses startup. Preserve both files for forensic repair.

Pinned quarantine denies context admission and tool access. The ordinary registry
lifecycle setter cannot turn a pinned version ACTIVE, LIMITED or SHADOW. Other
versions retain their own lifecycle. No agent gains control, credential, broker or
deployment tools. The existing manual recovery command refuses while any health
incident remains open. An operator may close only a quote incident after the
watchdog observes a fresh trusted quote for that instrument. Closure appends an
audited transition and does not lower risk state; a later matching failure
reopens the incident. Agent incidents cannot be closed, and closure never clears
quarantine. Safety recovery remains a separate gated paper workflow.

## Protected commands

All routes require `X-Operator-Token` and valid owning audit/journal integrity.

| Route | Result |
| --- | --- |
| `GET /api/v1/paper/health` | Disabled/monitoring/restored-only state, incident count and pinned versions |
| `POST /api/v1/paper/health/scan` | Current server-source scan; accepts no health verdict or agent identity |
| `GET /api/v1/paper/health/incidents` | Metadata for bounded retained incidents |
| `GET /api/v1/paper/health/incidents/{incident_id}` | Complete first-observation evidence |
| `POST /api/v1/paper/health/incidents/{incident_id}/close` | Close a quote incident after a fresh trusted quote; requires `Idempotency-Key` and a reason |

New-incident logs contain only content identity, taxonomy code and actual safety
state; source prices, model payloads, credentials and raw error messages are not
logged. Status and evidence remain diagnostic; promotion is always denied.

## Incident runbooks

**Uncertain paper order:** Keep NO_NEW_RISK and the reservation. Inspect the
retained order identity and original broker/order ledgers. If Temporal recovery is
enabled, submit that server-owned order to the existing protected recovery route.
Retry only an explicit waiting workflow. Never resend to resolve uncertainty.

**Failed agent:** Preserve graph context/receipt, journal and exact manifest.
The version remains quarantined across restart and monitor disablement. Produce a
separate candidate implementation/version and complete independent verification
and shadow/canary gates before any future release mechanism. Do not edit the
lifecycle dictionary, delete incidents or reuse a version to bypass the latch.

**Ledger/position failure:** Keep HALT. Back up paper, graph and health SQLite
files before investigation. Compare retained fill identities and audit/head
evidence. Restore from verified backups through a reviewed forensic process;
blindly rewriting hashes or clearing receipts is not recovery. The current system
has no HALT-to-NORMAL path or automated forensic restoration.

## Verification and remaining work

Run the health store/watchdog/API tests and the existing agent registry/graph tests.
Real Temporal API tests also cover startup failure with and without the watchdog.
The retained checks use actual SQLite stores, actual LangGraph execution/failure,
actual simulator acknowledgements and broker fills, restart restoration, bounded
multi-page scans, tamper/deletion/capacity refusal and cancellation fault injection.
The agent failure is an injected test mission, not a credentialed model call or
evidence that all eighteen missions have been implemented.

Full failure coverage, service heartbeats, running/orphan detection, monitoring
budgets/latency guarantees, retention, durable multi-process leases, candidate
repair branches, independent verification, shadow/canary release, rollback
automation and production review remain. Quote closure is implemented; critical
incident resolution remains gated. P11/P13 are unfinished.
