# ADR 0106: Durable graph transition observations

Status: accepted for local paper development, 2026-10-09.

## Decision

Add observed_at to new graph audit events, atomically with original state changes.
Preserve the original receipt/checkpoint schemas and accept legacy events. Derive a
protected typed archival trace from checked original events, with stable run/context/
manifest identity and exact historical record reconstruction. Expose hashes and
metadata rather than source payloads. Withhold durations for missing or regressing
wall-clock observations; retain all original events on restart and retry.

## Reason

The frozen P07 deliverables include an agent trace schema. Current receipts alone
show status and context time, but cannot establish when execution was reserved or
when a terminal decision was recorded. Audit-bound transition times support those
operator questions without conflating context freshness with execution timing.

## Consequences

New events require a compatible reader; no SQL migration or old schema rewrite.
Clock/storage failures roll back a state change, maintaining reservation integrity.
This is local wall-clock telemetry; it does not establish external operation latency,
monotonic performance, paid cost, current eligibility or capital authority. Full
span instrumentation, export, retention and UI navigation remain separate work.
