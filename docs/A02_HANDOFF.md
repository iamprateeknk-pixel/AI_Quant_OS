# A02 Market Regime Agent handoff

## Working implementation

A02 0.2.0 now runs as a trusted installed handler in the local graph supervisor. Registering its manifest reports PARTIAL/SHADOW. The other 17 missions and original 18 version-0.1.0 manifests remain PLANNED.

Files: agent_envelope.py defines the shared v2 mission envelope; regime_features.py owns pure deterministic calculations; regime_agent.py validates evidence and emits RegimeDistribution; regime_evaluation.py replays the actual graph. paper_context_sources.py binds history hashes and explicit session gaps. graph_runtime.py validates the returned mission envelope against admitted context.

A02_MISSION_DESCRIPTOR.json records the exact permission-manifest reference, implementation, owner role, deterministic route, context-only memory policy, startup lifecycle and evaluation suite. It documents installed behavior; it is not a client-editable activation or tool-grant mechanism.

With AI_QUANT_PAPER_DB configured, publish fresh paper quotes and at least three canonical bars, then POST `/api/v1/paper/agents/context` with agent_id A02, agent_version 0.2.0 and task_id. Submit its context_ref and a unique run_id to `/api/v1/paper/agents/execute`. Both require X-Operator-Token. A context lacking price/volatility data yields WAIT_FOR_DATA. Successful execution produces advisory window analysis with confidence zero and mandatory escalation.

## Verification

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q tests/test_regime_agent.py tests/test_graph_runtime.py
python -m ai_quant_os.regime_evaluation tests/fixtures/regime_baseline.json --output docs/evaluations/a02-synthetic-baseline.json
python -m pytest -q
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
```

The saved synthetic development baseline checks 15 window labels across five actual graph runs. It is not a holdout or calibration report. Tests cover live application wiring, source/expiry/version references, missing instruments and windows, disagreement, source-history mismatch, typed output rejection and restart retrieval. Runtime tests cover permission denial, provider/handler failure, deadlines and cancellation without repeat execution.

## Scope still open

Event/crisis classifiers and trusted calendars, statistical calibration, configurable model gateway, benchmarked provider escalation, mission health/cost telemetry, independent shadow evaluation and a working promotion rollback target remain open. No promotion is approved. See ADR 0019 and the full BUILD_STATUS.md.
