# 0150 — Own the parent context for each paper tracing runtime

## Decision

Every `PaperTelemetry` instance keeps its own active native span in a standard
Python `ContextVar`. Each SDK span starts from an explicit empty OTel context
with only that instance's parent span, or from an empty context when there is no
parent or an explicit HTTP root. Ambient application, remote and other paper
runtime spans cannot provide parent identities or trace state.

The token is restored on normal and exceptional exit. Native async context
propagation and `asyncio.to_thread` preserve the original parent. Separate
parallel requests stay separate. A thread without explicitly copied Python
context starts a separate observation; cross-process or arbitrary thread
propagation is not inferred. A failed span start cannot manufacture a parent.

## Reason

The original implementation reset HTTP roots but used the SDK's ambient context
for internal steps. Three native SDK regressions reproduced adoption of a
foreign ambient span, adoption of another owned runtime's span, and replacement
of the original parent while a foreign span was active. This could misattribute
local trace observations even though paper trading authority was unchanged.

The implementation uses public APIs from the installed OTel SDK/API 1.45.0:
`Context`, `set_span_in_context` and `start_as_current_span`. It installs no global
provider and changes no exported attributes, span names, queue/transport behavior,
original journals, API schemas or dependency pins. Existing operation exceptions
continue to propagate with sanitized SDK error observations.

## Verification and limits

Five native tests cover foreign and nested runtimes, concurrent async requests,
propagated threads, exception restoration and later standalone operations.
The wider trusted paper/API/agent/collector/risk/HALT/session suite and package
identity are recorded in evaluation 0150. The full suite is withheld to preserve
uncertain original 0084/0088 candidate custody. Independent review, production
load, distributed causal lineage, browser acceptance and all P00–P13 full gates
remain open. No new authority or complete/durable delivery claim is introduced.

## Rollback

Revert the instance context and explicit parent construction in this increment.
No migration, collector deletion, journal rewrite, queue reset or retry is needed.
Disabling `AI_QUANT_OTEL_ENABLED` remains available for a separately started app;
an existing runtime is never replaced or restarted automatically.
