# ADR 0226: Expose bounded volatile agent execution metrics

Status: Accepted for optional local PAPER observability

## Context

The protected metrics read exposed HTTP duration and failure observations, but
durable PAPER graph runs had no aggregate operational count or duration series.
The metrics must describe execution behavior without becoming a strategy-quality,
model-accuracy, capital-risk, or promotion signal.

## Decision

When the owned optional OTel metrics runtime is enabled, record a cumulative
histogram and counter for terminal agent graph attempts. Use fixed A01–A18 agent
IDs and terminal status enums only. Measure monotonic elapsed time through a
terminal admission decision or from durable RUNNING admission through its
terminal receipt. Count recovered UNCERTAIN transitions without adding a duration
observation.
The protected local scrape includes these instruments alongside existing HTTP
metrics. Validate label inventories, exact histogram buckets, exemplar absence,
and histogram/counter count agreement before returning a scrape.

## Consequences and limits

The measurements are process-local, volatile operational observations. They omit
task/run IDs, agent versions, inputs, contexts, source references, tool/output
payloads, provider identities, and trading P&L. They do not measure model quality,
reliability over an independently accepted sample, strategy performance, or
promotion eligibility. They do not add a database, network exporter, public
metrics port, or authority. Existing collector and scraper installation,
credential lifecycle, retention, dashboards, justified alerts, and independent
acceptance remain separate work.

## Verification

Tests verify actual OTel metric data, exact cumulative histogram buckets and sums,
finite cardinality, rejection of unknown identity/status values, protected API
scrapes, fixed accounting metadata, and telemetry-disabled behavior. A real graph
API execution contributes both parent and child agent samples without exposing
request/task/context identifiers.
