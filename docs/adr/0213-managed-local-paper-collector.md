# 0213 — Supervise the opted-in local PAPER Collector

## Decision

Keep OTel disabled by default. When an operator explicitly enables both
`AI_QUANT_OTEL_ENABLED=1` and `AI_QUANT_OTEL_COLLECTOR_MANAGED=1`, the local
application supervisor verifies the pinned Windows Collector, renders and
validates a loopback-only configuration, then starts the Collector before the
API. It retains rotating trace files and points the existing protected trace
reader at that file. The UI never receives Collector configuration.

The endpoint must remain literal IPv4 loopback HTTP and use the exact OTLP
`/v1/traces` path enforced by `LocalOtlpExporter`. The Collector receives only
an allowlisted system environment and the trace-file path. It does not receive
operator or provider credentials. The existing queue and export behavior remain
unchanged: no retry, reset, or inferred acknowledgement is added.

The local supervisor owns the Collector, API, and UI process trees. Startup order
is Collector, API, UI; shutdown runs in reverse so the Collector remains available
during the API's bounded shutdown. The existing queue may still retain
unconfirmed spans if its deadline expires. A port conflict, hash mismatch or
configuration refusal stops startup without killing an unrelated listener.
Manual Collector management remains available with the managed flag left off.

## Evidence and limits

The opt-in Windows integration starts the actual pinned Collector with the real
built Mission Control and PAPER API, makes a real status request, and confirms
that its returned trace ID appears in the retained Collector file. It verifies
that the three owned processes stop and that the Collector port is released.
Focused environment tests confirm the managed mode requires OTel and gives only
the backend the local trace-file path. Native Collector export, outage, restart,
file rotation configuration and protected trace reads are covered by the
existing Collector acceptance.

This Collector receiver is unauthenticated and trusts local loopback processes.
It can accept forged spans or consume local resources. Retained JSON traces are
not an audit journal or tamper-proof evidence; a same-account process can read
or modify them. The managed mode currently requires the pinned Windows binary.
Linux, service-manager installation, load/rotation acceptance, remote
authorization, metrics/log correlation, independent security review, and all
P00–P13 release gates remain open.
