# Paper fills response contract handoff

## Summary

Published a strict JSON Schema 2020-12 contract for `GET /api/v1/fills`, including the exact response envelope and the nine projected PaperFill fields. The focused test checks runtime dataclass field parity, calls the actual API projection, validates the serialized response, and rejects unknown fields, invalid sides, and zero quantities.

## Verification

- Fresh isolated archive checkout: `pytest tests/test_paper_fill_contract_schema.py -q --tb=short` passed (1 test).
- Ruff lint and format checks passed for the new test.
- `Draft202012Validator.check_schema` validates the schema.

## Limits

- The schema documents the existing endpoint and does not add a `schema_version` field or change its response bytes.
- Cross-record linkage to retained orders and audit events is outside this item schema.
- P00/P05/P12 and all remaining P00-P13 acceptance gates remain open.

## Rollback

Revert the fill schema, projection parity test, contract-version entry, ADR, and this handoff together. No runtime or persistent data changes are involved.
