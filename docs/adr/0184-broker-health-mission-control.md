# ADR 0184: Broker health Mission Control read view

- Status: accepted for local paper observation
- Date: 2026-10-12

## Context

The backend exposes a protected BrokerHealth v1.0 registry, but the Brokers section previously presented declared capabilities and demo synchronization without the separate per-profile health contract. The existing protected-read helper keeps the operator session server-side and performs uncached fixed-path reads.

## Decision

Read `/api/v1/brokers/health` in parallel with the existing Brokers-section reads and render a dedicated health table. Validate the fixed five-profile paper catalog and contract enums before rendering. Preserve unknown connection and unassessed telemetry values; do not derive scores or claim execution readiness from missing measurements. Keep the table read-only.

## Consequences

The UI now exposes the backend contract while preserving its evidence limits. No backend, schema, broker access, order authority or live release behavior changes. Browser visual review remains outstanding, as do P04/P12 acceptance and all P00-P13 release gates.
