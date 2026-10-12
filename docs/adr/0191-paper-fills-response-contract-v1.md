# ADR 0191: Version the paper fills response contract

- Status: accepted for read-only API documentation
- Date: 2026-10-13

## Context

`GET /api/v1/fills` is consumed by Orders and Mission Control but its exact response shape was not published as a canonical schema. The runtime projection converts quantities, prices, fees, and observation times into strings and restricts fills to paper sources.

## Decision

Publish a strict JSON Schema 2020-12 contract for the `{"fills": [...]}` response. Test its item properties against the `PaperFill` dataclass and validate output from the API's `_fill_view` projection. Preserve exact numeric strings, require positive quantities/prices, nonnegative fees, aware timestamps, fixed FX instruments, and declared PAPER sources.

## Consequences

This makes the existing v1 response reviewable without changing endpoint bytes or persistence. Future fields or source/instrument changes require an explicit contract update. The schema grants no order or broker authority; P00/P05/P12 acceptance remains open.
