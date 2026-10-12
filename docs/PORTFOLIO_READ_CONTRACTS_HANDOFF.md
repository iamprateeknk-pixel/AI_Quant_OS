# Portfolio read contracts handoff

## Summary

Published strict JSON Schema 2020-12 contracts for paper instrument positions and currency exposure. Tests call both actual API endpoints from a fresh application and validate the full serialized response, including unavailable mark/FX values. The schemas pin PAPER mode, four instrument/currency relationships, decimal-string fields, and explicit nullable valuation/exposure.

## Verification

- Fresh isolated archive checkout: `pytest tests/test_portfolio_read_contract_schemas.py -q --tb=short` passed (2 tests).
- Ruff lint and format checks passed for the test.
- `Draft202012Validator.check_schema` validates both schemas.

## Limits

- These schemas document existing output and add no quote, FX, or valuation sources.
- Cross-field valuation semantics remain enforced by the API and domain code; schemas preserve null states but cannot establish market truth.
- P00/P05/P12 and all remaining P00-P13 acceptance gates remain open.

## Rollback

Revert the two read schemas, actual-endpoint tests, contract-version entries, ADR, and this handoff together. No runtime or persistent data changes are involved.
