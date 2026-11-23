# ADR 0225: Instrument durable agent graph stages

Status: Accepted for optional PAPER telemetry

## Context

Durable graph execution had audit-checked transitions and parent/child trace
navigation, but the owned OpenTelemetry trace did not show where a graph spent
time or which fixed-capability tool stage was active. Graph inputs and outputs
can contain research data, operator identifiers, and sensitive source material;
instrumentation must not turn those payloads into telemetry.

## Decision

When the application supplies its owned telemetry runtime, instrument graph
admission, execution, implementation invocation, authorized tool calls, read-tool
source access, and finalization as internal spans. A nested graph inherits the
parent tool span through the telemetry runtime's existing owned context. Attach
only the registry-bound agent ID/version, fixed phase enums, and known tool
name. Do not add run IDs, task IDs, context or source references, provider input,
tool payloads, or graph outputs. Graph execution remains unchanged when telemetry
is disabled, and standalone runtimes default to no telemetry.

## Consequences and limits

The spans are transient OTel observations and do not change durable GraphTrace
schemas, receipts, audit records, authorization, call budgets, or recovery
semantics. They describe graph and tool-stage time, not exact source-level access
lineage, external provider billing, or durable workflow completion. Collector
availability and retention remain governed by the existing local OTel setup.

## Verification

Tests use the actual in-memory OTel SDK and a protected API graph execution to
verify span names, nested parent IDs, run-status metadata, tool/source children,
and the absence of task, context, and payload attributes. Telemetry-disabled
graph regression tests continue to cover the ordinary runtime path.
