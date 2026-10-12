# ADR 0193: Version the paper market snapshot response

- Status: accepted for read-only PAPER quote responses
- Date: 2026-10-13

## Context

Mission Control and Market Detail consume `GET /api/v1/markets/{instrument}`. The response includes bid/ask strings, observation time, world-state identity, trust, freshness, and source, but lacked a published schema.

## Decision

Publish a strict JSON Schema 2020-12 contract for the current simulated market snapshot and validate both fresh and stale responses from the actual API route. Preserve quote strings, require positive price representations and an aware timestamp, and pin the source to `PAPER_SIMULATED`.

## Consequences

This documents the existing API without changing quote ingestion or execution behavior. The schema checks representation but not `ask >= bid` or correctness of the freshness calculation; the runtime `MarketSnapshot` and API remain responsible for those rules. P00/P02/P12 acceptance remains open.
