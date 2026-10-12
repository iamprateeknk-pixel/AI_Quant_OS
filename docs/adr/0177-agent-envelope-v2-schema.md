# Publish the AgentEnvelope v2 schema

## Decision

Publish the exact JSON Schema for the current immutable `AgentEnvelope` Pydantic model as contract version 2.0 with a stable URN `$id` and JSON Schema 2020-12 declaration. A test compares the artifact to `AgentEnvelope.model_json_schema()` and verifies that unknown fields are rejected.

## Consequences

- External or future internal consumers can bind to the existing v2 mission envelope without inferring its shape from Python source.
- The earlier blueprint's envelope field list is not silently substituted for this later runtime model; the published schema matches the implementation currently used by `GraphRuntime`.
- No runtime behavior or persisted payload changes.

## Verification

- `tests/test_canonical_contract_schemas.py`: 3 passed in a fresh archive checkout with the test and schemas overlaid.
- Ruff check and format passed.

## Rollback

Revert the schema artifact, parity test, documentation update, and this ADR. No migration is required.
