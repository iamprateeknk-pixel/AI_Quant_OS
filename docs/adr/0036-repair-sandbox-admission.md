# 0036 — Fail-closed repair sandbox admission

Status: Accepted for read-only prerequisites; live enforcement unverified.

## Decision

Do not execute proposed repair source in an ordinary host interpreter. Require
an OS isolation boundary with no network, credentials or writable production
files, an owned immutable toolchain/verifier and bounded CPU/memory/process/file/
disk/wall-clock behavior. A missing runtime never becomes implicit host execution.

Implement the first prerequisite: fixed local Docker endpoint capability and
local immutable image inspection, plus strict checks of the actual metadata of
a future created container before it could be started. Use typed unavailable
reasons and preserve `promotion_allowed=false`. CLI inspection never opens the
health journal, creates a container, pulls an image or releases quarantine.

## Consequences

Metadata policy tests and a live unavailable-runtime probe demonstrate admission
refusal. They do not prove a working OS sandbox, candidate correctness, runtime
version compatibility or independent verification. The local Desktop engine
fails at a stale socket and graceful shutdown times out; automatic approval
review rejected force-stop/socket removal. Runtime repair and live validation
remain unresolved. Other build work can continue; no complete P10 claim follows.

The behavior worker, snapshot and suite identity checks, execution controller,
direct kernel confinement probes, bounded output/wall time and owned cleanup
must be verified before candidate execution. Independent review/certificates,
shadow/canary evidence and gated recovery/rollback remain open. No schema or
storage migration is needed for the existing health/candidate records.
