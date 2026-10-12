# ADR 0190: Version the paper World State response

- Status: accepted for the current PAPER read response
- Date: 2026-10-13

## Context

The API emits a `schema_version` for World State, but no published wire schema pinned the response shape. Consumers therefore had no canonical artifact for the fixed four-pair instrument universe, freshness/status enums, currency coverage, or evidence-reference structure.

## Decision

Publish JSON Schema 2020-12 at `docs/schemas/paper-world-state-v1.schema.json` with a stable contract ID and closed property sets. Test property parity against the runtime dataclasses and validate the actual `WorldStateEngine` API projection. Keep cross-field relationships in runtime parser tests where JSON Schema cannot express them clearly.

## Consequences

The artifact makes the existing response contract reviewable and versioned without changing backend output or persistence. Changes to instrument universe or wire fields require an explicit schema/version update. This does not verify the world-state fingerprint cryptographically or establish any trading authority; P00/P02/P12 acceptance remains open.
