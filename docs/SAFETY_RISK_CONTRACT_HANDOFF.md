# Safety and risk read contract handoff

## Summary

Published strict JSON Schema 2020-12 contracts for system safety status and instrument-scoped risk state. Tests query the actual API for system status and GBPUSD risk, validate the outputs, and reject unknown safety states and unknown properties.

## Verification

- Fresh isolated archive checkout: `pytest tests/test_risk_read_contract_schemas.py -q --tb=short` passed (2 tests).
- Ruff lint and format checks passed for the test.
- `Draft202012Validator.check_schema` validates both schemas.

## Limits

- Contracts document current read responses and add no control endpoint or authority.
- Transactional risk admission and persistent safety state remain owned by backend control code.
- P00/P05/P12 and all remaining P00-P13 acceptance gates remain open.

## Rollback

Revert the two schemas, actual-endpoint tests, contract-version entries, ADR, and this handoff together. No runtime or persistent data changes are involved.
