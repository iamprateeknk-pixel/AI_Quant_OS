# Risk instrument selection handoff

## Summary

Risk Center now selects EURUSD, GBPUSD, USDJPY, or EURJPY using a GET form. The
selection is retained in the URL and sent to the existing instrument-scoped risk
endpoint. The strict parser withholds a response whose instrument does not match
the requested pair. Safety controls retain their existing authority and flow.

## Verification

- Focused test verifies the requested instrument is sent and validated.
- Full web suite: 176 tests passed.
- Next.js production build and TypeScript checks passed.
- Browser checks at `/risk?instrument=USDJPY` and `/risk?instrument=EURJPY` showed
  the selected pair's exposure labels and values, no console errors, and no
  horizontal overflow at 320 px.

## Limits

The selection is limited to the backend's four configured PAPER instruments.
Shared currency ceilings are shown alongside each instrument's position risk.
Accessibility review and all remaining P00-P13 gates remain open.

## Rollback

Revert ADR 0198, this handoff, Risk Center's selection and query wiring, the
instrument-aware API read, and its test together. No persistent state changes are
involved.
