# Paper market snapshot contract handoff

## Summary

Published a strict JSON Schema 2020-12 contract for `GET /api/v1/markets/{instrument}`. The test sends an operator-authorized simulated quote through the API and validates the actual fresh and stale response projections, including source, trust, timezone-aware observation time, positive bid/ask strings, and exact response properties.

## Verification

- Fresh isolated archive checkout: `pytest tests/test_paper_market_contract_schema.py -q --tb=short` passed (1 test).
- Ruff lint and format checks passed for the new test.
- `Draft202012Validator.check_schema` validates the schema.

## Limits

- The schema documents the existing response and adds no runtime or persisted fields.
- Price ordering and freshness calculation remain runtime invariants; the schema cannot prove them.
- P00/P02/P12 and all remaining P00-P13 acceptance gates remain open.

## Rollback

Revert the quote schema, actual-route test, contract-version entry, ADR, and this handoff together. No runtime or persistent data changes are involved.
