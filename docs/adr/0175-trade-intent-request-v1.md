# TradeIntent request contract v1

## Decision

Add an explicit optional `schema_version` to the paper intent request. Requests that omit it retain their previous behavior and are interpreted as version 1.0. Explicit versions other than 1.0 fail validation. The marker is excluded from `model_dump()` so the internal `TradeIntent` domain dataclass and stored order contracts remain unchanged.

Publish the exact validation schema with a stable URN `$id` and JSON Schema 2020-12 declaration. Tests compare the artifact to the runtime request model and validate it with the JSON Schema implementation.

## Consequences

- Existing clients that omit `schema_version` remain compatible.
- Producers may pin the request version and consumers reject unsupported versions.
- Other canonical contracts remain unversioned or unimplemented; see `docs/CONTRACT_VERSIONING.md`.
- No database migration or response contract change is made.

## Verification

- `tests/test_api.py`: 26 passed in a fresh archive checkout with changed files overlaid.
- Ruff check and format passed.

## Rollback

Revert the request model field, its compatibility/schema tests, the schema artifact, and this ADR together. No persistent state migration is required.
