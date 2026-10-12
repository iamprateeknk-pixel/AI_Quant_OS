# Publish protected broker health observations v1

## Decision

Add a protected, read-only `/api/v1/brokers/health` endpoint that publishes a strict PAPER-only `BrokerHealthRegistry` v1.0 JSON Schema. The registry covers the five declared broker profiles and reports only connection state already exposed by the local router. Metrics without a telemetry source remain null or `UNASSESSED`; health score is withheld unless every required measurement and assessment is present. The contract fixes `live_execution_released` to false.

## Consequences

- Operators can distinguish the connected local paper simulator from unbound broker profiles without implying native connectivity.
- The schema is derived from and tested against the runtime model; the endpoint requires operator authorization and returns `Cache-Control: no-store`.
- `updated_at` is the read time, not telemetry freshness. Quote age, order latency, reject rate, reconciliation, spread and account availability still require source-backed definitions and observation pipelines.
- This is an observation contract only. It does not satisfy native broker acceptance or enable live execution; `PromotionDecision` and independent P00/P04/P11/P13 review remain open.

## Verification

- `tests/test_broker_health_api.py` and `tests/test_broker_capability_api.py`: 6 passed in a fresh archive checkout with changed files overlaid.
- Ruff lint passed. Formatting check is rerun after formatting the changed files.

## Rollback

Remove the endpoint, runtime registry, published schema, test, and contract documentation. No stored data or existing response format is changed.