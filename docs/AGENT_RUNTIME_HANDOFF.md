# Agent runtime handoff

## Current state

P07 is in progress. All 18 named agents have immutable initial manifests and fixed capability ceilings. A02 and A07 have separately registered 0.2.0 PARTIAL/SHADOW missions for multi-window regimes and local feed integrity. Original 0.1.0 manifests and the other 16 missions remain PLANNED. Complete sources, calibration, continuous workers, incident delivery and promotion are still open. See A02_HANDOFF.md and A07_HANDOFF.md.

The registry and tool SDK are in agent_registry, agent_catalog and agent_permissions. The read API is `/api/v1/agents/registry`. A bounded compiler assembles paper evidence through `/api/v1/paper/agents/context`, with explicit missing inputs and source-bound validity. Full context/manifest checkpoints commit atomically with audit and support operator-authenticated retrieval at `/api/v1/agents/contexts/{context_ref}`. The local LangGraph supervisor and execution ledger support checked resume and uncertain invocation retention. Installed shadow missions: A02 and A07 0.2.0. External tool handlers: none. Complete missions, model routing, lifecycle persistence, Temporal workers, distributed execution, quality metrics, physical dataset isolation and promotion gates remain unfinished.

With AI_QUANT_PAPER_DB set, startup opens a sibling `.agents.sqlite3` file. Execute an existing archived paper context through operator-authenticated POST `/api/v1/paper/agents/execute` with run_id and context_ref. GET `/api/v1/agents/graph-runs/{run_id}` reads historical receipts without changing state. POST `/api/v1/paper/agents/graph-runs/{run_id}/resume` checks current evidence and permissions before any uninvoked call. A RUNNING reservation becomes UNCERTAIN and is never automatically replayed. COMPLETED means a trusted installed handler returned its typed advisory schema; it grants no order authority. Absent handlers return UNAVAILABLE and incomplete contexts return WAIT_FOR_DATA.

## Verification commands

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q tests/test_agent_registry.py
python -m pytest -q tests/test_agent_context.py
python -m pytest -q tests/test_context_checkpoints.py
python -m pytest -q tests/test_graph_runtime.py tests/test_graph_api.py
python -m pytest -q
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
```

The focused tests cover catalog completeness, all-role capital-control denials, lifecycle/scope policy, holdout capability separation, immutability, handler timeout, payload bounds and sanitized failures. Fixture handlers are not installed in the application.

## Migration and rollback

The graph increment adds pinned LangGraph and SQLite checkpoint dependencies. Install the package into the project virtual environment with `python -m pip install -e ".[dev,data]"`. The local verification environment reuses existing system libraries without changing their installations. Roll back code without deleting either SQLite file, advisory runs, contexts, graph reservations, snapshots or audit evidence. Full manifests are retained within checkpoints; the active registry and lifecycle still reset on restart. Persist activation/lifecycle checkpoints before production promotion.

## Next implementation

1. Add immutable dataset/resource catalogs and trusted source adapters to the bounded context compiler. Preserve source provenance and physically isolate evaluation data from generators; current dataset classes are trusted server declarations.
2. Extend the local supervisor with model routing, persistent lifecycle and quality events; implement Temporal workers and distributed leases before distributed deployment.
3. Implement the 18 agent missions with their specific evidence/output contracts and acceptance datasets. Missing sources must produce unavailable evidence, never fabricated market/macro results.
4. Add quality metrics and independently reviewed evaluation/promotion. Credentials, broker submission, risk policy and recovery remain outside model authority.

See ADR 0015 through ADR 0018 for trust boundaries and current limitations.
