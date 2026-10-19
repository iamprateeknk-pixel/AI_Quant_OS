# ADR 0208: Bind protected agent sources to immutable dataset catalogs

## Status

Accepted for partial PAPER/SHADOW implementation on 2026-10-19.

## Context

The context compiler previously relied on each server source binding's `resource_class` label. That label did not identify the immutable dataset receipt or resource snapshot that justified TRAINING/HOLDOUT access. A context could therefore retain a class label without proving which protected resource it represented.

## Decision

Add a frozen, content-addressed `DatasetResourceCatalog` and require every protected `SourceBinding` to name one or more catalogued refs whose class matches the binding. The compiler checks the agent's tool and TRAINING_READ/HOLDOUT_READ permissions before catalog validation, checks again around budget reservation and fetch, and denies a protected binding with absent, duplicate, unknown, or mismatched refs before calling its fetcher.

Catalog entries may be constructed from validated `ResearchDataReceipt` values or by a trusted adapter from canonical immutable resource material. The context's evidence source identity commits the catalog ref, protected class, requested resource refs, and original source identity through a content hash. A08 and A10 use receipt-backed catalogs; A11 binds the checked holdout scope in its verified archived report. A09's operator-reviewed hypothesis is a derived bounded summary, so it is GENERAL and does not expose the underlying training dataset.

## Consequences

- Missing or mismatched protected-resource proof fails closed before source fetch.
- Context evidence remains schema version 1.0; the protected-resource binding is committed through its existing source identity and content ref.
- The catalog provides provenance and access classification at the application boundary. It does not physically isolate TRAINING from HOLDOUT bytes or provide independent evaluation acceptance.
- Additional trusted source adapters and all mission output/evaluation gates remain open. No promotion or live execution authority is added.

## Verification

See `docs/evaluations/agent-resource-catalog-0208.json`. The focused context, budget, A08/A09/A10/A11 source, and API suites passed 71 tests on Windows.
