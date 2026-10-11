# 0140: Owned OpenTelemetry paper observations

Date: 2026-10-11. Development, PAPER only.

Use one explicit optional SDK provider per app, with fresh local HTTP roots and
real child spans around market read, risk evaluation, order submission, replay
and pending recovery. Return generated trace identity in an additive HTTP header.
Keep deterministic domain modules and all receipt/wire schemas unchanged.

Allowlist only fixed route/method/outcome fields and internally generated order
UUIDs. Construct resources explicitly and suppress exception details/events.
Do not adopt client trace context or ambient exporter/resource configuration.

Use bounded SDK background batching and official OTLP protobuf encoding. A fixed
validated local transport omits proxies/credentials, rejects redirects, bounds
request/acknowledgement bytes and time, does not retry or log remote response text,
and records failed batches without blocking risk/order/control handling. Queue
loss is explicit; traces never replace the original durable audit journal.

Wrap all service lifespans with owned cleanup. Injected providers remain owned by
their callers. Shutdown drops queued export work; no further I/O starts after
closing. Default disabled mode starts no SDK worker. Local launcher forwards only
explicit validated backend telemetry fields.

Add the SDK/encoder observability extra to both supported hash locks while
preserving existing dependency pins and original schemas. Collector deployment,
full causal market/agent/risk/order/fill chain, diagnostics, dashboards/alerts,
sampling/throughput/drop acceptance and independent P12/P13 gates remain open.
All P00–P13 are incomplete. Revert this increment to remove instrumentation;
retain original paper journals, orders and schemas unchanged.
