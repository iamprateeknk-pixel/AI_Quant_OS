# Paper World State contract handoff

## Summary

Published the existing `WorldState` API response as a strict JSON Schema 2020-12 contract. The schema binds the current four instrument IDs, response metadata, status enums, metric representations, evidence-reference formats, and currency twin structures. Runtime dataclass parity and an actual `WorldStateEngine` API projection are covered by a focused test. No backend response, stored snapshot, or authority changed.

## Verification

- Fresh isolated archive checkout: `pytest tests/test_world_state_contract_schema.py -q --tb=short` passed (1 test).
- `Draft202012Validator.check_schema` validates the schema.
- The focused sample rejects unknown properties and timezone-naive timestamps.

## Limits

- JSON Schema does not establish cryptographic correctness of `world_state_version` or all cross-row relationships; the web parser enforces additional consistency rules.
- The schema is specific to the current four-pair PAPER universe.
- P00, P02, P12, and all remaining P00-P13 gates remain open.

## Rollback

Revert the schema, parity test, contract-version entry, ADR, and this handoff together. No runtime or persistent data changes are involved.
