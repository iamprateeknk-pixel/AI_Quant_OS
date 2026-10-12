# Paper orders response contract handoff

## Summary

Published a strict JSON Schema 2020-12 contract for `GET /api/v1/orders`. The focused parity test constructs a real `PaperOrder`, calls the API `_order_view` projection, validates required and optional receipt fields, and rejects an unknown lifecycle state, non-paper route, and unknown property.

## Verification

- Fresh isolated archive checkout: `pytest tests/test_paper_order_contract_schema.py -q --tb=short` passed (1 test).
- Ruff lint and format checks passed for the new test.
- `Draft202012Validator.check_schema` validates the schema.

## Limits

- The schema describes the existing endpoint and adds no response version field or runtime validation.
- It does not resolve uncertain outcomes, validate linked fills, or authorize retries.
- P00/P05/P12 and all remaining P00-P13 acceptance gates remain open.

## Rollback

Revert the order schema, projection test, contract-version entry, ADR, and this handoff together. No runtime or persistent data changes are involved.
