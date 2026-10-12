# Version internal risk and order contracts

## Decision

Publish version 1.0 JSON Schemas for the existing `RiskDecision` and `OrderCommand` dataclass shapes. Each schema has a stable URN `$id`, declares JSON Schema 2020-12, and rejects unknown properties. Tests compare the declared properties to the runtime dataclass type adapter and verify unknown-key rejection through JSON Schema and native dataclass construction.

These schemas document and freeze the present internal shape. They are not API endpoints, are not added to stored JSON, and do not change producer behavior or require a migration.

## Consequences

- Future consumers can bind to a stable v1 schema and must validate untrusted JSON before dataclass construction.
- Pydantic `TypeAdapter` currently ignores unknown dataclass keys; consumers must use the published JSON Schema or native dataclass constructor, not assume the adapter is strict.
- `BrokerHealth` and `PromotionDecision` still lack runtime models and remain open canonical contracts.

## Verification

- `tests/test_canonical_contract_schemas.py`: 2 passed in a fresh archive checkout with the test and schemas overlaid.
- Ruff check and format passed.

## Rollback

Revert both schemas, the parity test, and this ADR. No runtime data or database state changes.
