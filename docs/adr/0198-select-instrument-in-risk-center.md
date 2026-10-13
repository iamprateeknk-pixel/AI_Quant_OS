# ADR 0198: Select the instrument in Risk Center

- Status: accepted for read-only PAPER risk presentation
- Date: 2026-10-13

## Context

The backend risk read already accepts a configured instrument, but Risk Center
always requested EURUSD. Operators could not inspect the instrument-scoped risk
state for the other configured paper pairs.

## Decision

Add a GET form for the four configured PAPER instruments. Carry the selection in
the URL and pass it to the existing `/api/v1/risk?instrument=...` read. The
strict browser parser checks that the response instrument exactly matches the
selection. Invalid or repeated selection parameters use the documented EURUSD
default. Safety controls and risk authority remain unchanged.

## Consequences

Risk Center can display exact per-instrument ceilings, positions, and pending
quantities while retaining shared currency ceilings. The interface does not
change risk limits or authorize orders. Browser accessibility and remaining
P00-P13 acceptance gates remain open.
