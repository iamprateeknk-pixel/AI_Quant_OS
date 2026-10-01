# Agent runtime handoff

## Current state

P07 is in progress. All 18 named agents now have immutable initial manifests and fixed capability ceilings. Their implementations remain PLANNED; the catalog alone cannot perform their missions. Fast/deep advisory classification runs persist with audit binding, but they are not yet the full A02 graph implementation.

The registry and tool SDK are in agent_registry, agent_catalog and agent_permissions. The read API is `/api/v1/agents/registry`. A bounded compiler assembles paper evidence through `/api/v1/paper/agents/context`, with explicit missing inputs and source-bound validity. Full context/manifest checkpoints now commit atomically with audit and support operator-authenticated retrieval at `/api/v1/agents/contexts/{context_ref}`. Available production agent tool handlers: none. Graph supervisor, all 18 missions, execution checkpoints, quality metrics, physical dataset isolation and promotion gates remain unfinished.

## Verification commands

```powershell
python -m pytest -q tests/test_agent_registry.py
python -m pytest -q tests/test_agent_context.py
python -m pytest -q tests/test_context_checkpoints.py
python -m pytest -q
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
```

The focused tests cover catalog completeness, all-role capital-control denials, lifecycle/scope policy, holdout capability separation, immutability, handler timeout, payload bounds and sanitized failures. Fixture handlers are not installed in the application.

## Migration and rollback

The context checkpoint increment adds context_checkpoints at startup and introduces no dependency. Roll back code without deleting advisory runs, context checkpoints, snapshots or audit evidence. Full manifests are retained within checkpoints; the active registry and lifecycle still reset on restart. Persist activation/lifecycle checkpoints before production promotion.

## Next implementation

1. Add immutable dataset/resource catalogs and trusted source adapters to the bounded context compiler. Preserve source provenance and physically isolate evaluation data from generators; current dataset classes are trusted server declarations.
2. Implement a real LangGraph supervisor and agent SDK with task-specific capabilities, bounded calls and typed failure events; persist graph checkpoints and invocation traces.
3. Implement the 18 agent missions with their specific evidence/output contracts and acceptance datasets. Missing sources must produce unavailable evidence, never fabricated market/macro results.
4. Add quality metrics and independently reviewed evaluation/promotion. Credentials, broker submission, risk policy and recovery remain outside model authority.

See ADR 0015, ADR 0016 and ADR 0017 for trust boundaries and current limitations.
