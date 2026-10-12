# ADR 0189: Strict World State UI contract

- Status: accepted for server-side read projection
- Date: 2026-10-13

## Context

Market Detail now combines a paper quote with a World State response, but the existing response parser accepted unknown fields, timezone-naive timestamps, duplicate instruments, arbitrary statuses, and malformed numeric strings. Those gaps could allow unsupported data to reach an operational view.

## Decision

Parse the exact current `PAPER` v1.0 world-state contract in its owning feature module. Require the configured four FX pairs, timezone-aware timestamps, valid fingerprint and evidence-reference formats, bounded finite metric strings, supported enums, consistent instrument/currency relationships, and coherent status values. Return only fields needed for presentation and compute expiry from `valid_until` without modifying the source observation.

## Consequences

Malformed or future-shaped responses are withheld. API contract or configured-instrument changes require an explicit parser update. The UI validates shape and internal consistency, not cryptographic commitment correctness. P02/P12 and full P00-P13 acceptance remain open.
