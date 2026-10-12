# Agent output schema admission

## Decision

Require every registered `GraphRuntime` implementation output model to configure Pydantic with `extra="forbid"`. The runtime already checks the manifest-bound output type, serializes the returned model, validates the serialized value again, and withholds output unless validation succeeds. Rejecting permissive models at runtime construction closes the remaining path where an output model could silently discard undeclared fields.

## Consequences

- Invalid implementations fail closed before graph execution.
- Existing output models must declare strict extra-field handling before registration.
- This validates the runtime's output model contract; it does not establish independent semantic correctness, calibration, or authority for agent results.
- No persisted data or wire contract changes.

## Verification

- `tests/test_graph_runtime.py` and `tests/test_graph_api.py`: 22 passed in a fresh archive checkout with the changed runtime and tests overlaid.
- Ruff check and format passed.

## Rollback

Revert the runtime guard, regression test, and this ADR together. No migration is required.
