# 0032 — Actual Temporal paper uncertainty triage

Status: Accepted for local paper diagnostics; production/recovery approval pending.

## Decision

Use optional pinned Temporal SDK 1.34.0 for durable orchestration. Activities bind
to existing paper gateway, RiskKernel and audited state-store instances. Domain
policy does not import Temporal. The workflow uses deterministic SDK time and
history, bounded signals, activity retries and immutable per-attempt receipts.

The first runbook performs containment and broker lookup only. Matching evidence
ends at REVIEW_REQUIRED with promotion denied and risk stopped. No workflow method
normalizes safety or grants review/deployment authority. Duplicate incident IDs are
refused even after completion. Broker uncertainty remains unresolved until an
explicit new lookup; no timer or activity retry resends an order.

## Evidence and consequences

Actual CLI 1.9.1 / server 1.32.0 SQLite service tests cover worker/server restart,
paper object reconstruction, persisted signals/results, duplicate starts, failed
containment and twenty unsuccessful attempts. Windows and Linux CI gates install
checksum-pinned CLI archives; their hosted execution remains unverified.

This initial library boundary is intentionally narrower than P10. Owner-bound
application wiring, watchdog/quarantine, repair branches, independent verification,
shadow/canary recovery, production service controls and rollback remain required.
Persistence failure halts the current kernel but cannot guarantee writing HALT to
a failed or corrupt disk. There is no HALT-to-NORMAL or automatic restart path.

Rollback preserves paper state and Temporal history. Open workflows require replay
compatible code or reviewed versioning/migration; never delete history to rerun an
uncertain incident under the same identifier.
