# ADR 0186: Validate paper market snapshots before display

- Status: accepted for read-only presentation
- Date: 2026-10-13

## Context

The shared market read accepted arbitrary freshness strings, timestamps without a timezone, mismatched instrument identities and malformed prices. Mission Control and Market Detail use this result as an operational observation.

## Decision

Parse market responses through a dedicated projection that requires the requested instrument identity, positive finite bid/ask strings, timezone-aware parseable observation times, bounded world-state version text, the backend's `FRESH`/`STALE` states, and the `PAPER_SIMULATED` source. Return only public fields and preserve the source price strings.

## Consequences

Malformed snapshots are withheld by both consumers; stale snapshots remain visible with their stale label. The parser does not calculate or upgrade freshness. It adds no order or portfolio authority. P02 and P12 acceptance remain open.
