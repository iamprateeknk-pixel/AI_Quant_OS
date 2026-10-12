# ADR 0195: Version paper safety and risk read contracts

- Status: accepted for read-only authoritative state responses
- Date: 2026-10-13

## Context

Mission Control and operator workflows read the current safety state and instrument-scoped risk ceilings. These are central control observations, but the `/api/v1/system/status` and `/api/v1/risk` wire shapes had no published closed schemas.

## Decision

Publish strict JSON Schema 2020-12 contracts for both responses and validate actual endpoint output. Enumerate the four supported safety states and configured instruments, require nonnegative counts and quantities, preserve signed filled position, and require positive configured ceilings.

## Consequences

Consumers can distinguish safety state from risk quantities under fixed response contracts. The schemas do not execute or approve controls and cannot replace transactional risk admission. P00/P05/P12 acceptance remains open.
