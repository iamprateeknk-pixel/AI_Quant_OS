# Contract versioning status

## Current policy

Published wire contracts use an explicit JSON Schema draft and stable `$id`. A breaking change requires a new contract version, a compatibility or migration plan, producer and consumer tests, and an ADR. Existing clients remain compatible only where the request model explicitly supports it; unversioned requests must not be silently interpreted as a newer incompatible contract.

## Implemented contract

| Contract | Version | Runtime behavior | Schema |
| --- | --- | --- | --- |
| Paper `TradeIntent` request | 1.0 | Accepts legacy requests without `schema_version` as v1, accepts explicit `1.0`, rejects unknown versions, and excludes the version marker from the internal domain dataclass | `schemas/trade-intent-v1.schema.json` |
| Advisory `AgentEnvelope` | 2.0 | Strict immutable Pydantic model; graph runtime validates the manifest-bound output model before retaining results | `schemas/agent-envelope-v2.schema.json` |
| Internal `RiskDecision` | 1.0 | Schema covers the existing dataclass shape; no JSON wire endpoint or storage migration is introduced | `schemas/risk-decision-v1.schema.json` |
| Internal `OrderCommand` | 1.0 | Schema covers the existing dataclass shape; persistence encoding remains unchanged | `schemas/order-command-v1.schema.json` |
| Broker health registry | 1.0 | Protected PAPER-only observation; unavailable measurements remain null or UNASSESSED and live release is fixed false | `schemas/broker-health.schema.json` |
| Promotion decision | 1.0 | Evidence snapshot is strict and fail-closed; decision is fixed WAIT_FOR_EVIDENCE and policy approval is fixed false | `schemas/promotion-decision-v1.schema.json` |

## Remaining canonical contracts

The JSON Schemas for `RiskDecision` and `OrderCommand` describe the current internal dataclass shapes; no runtime endpoint consumes them yet. Consumers must validate the JSON Schema before mapping untrusted JSON into those types. The blueprint's `BrokerHealth` read model is now versioned for protected PAPER-only broker observations. Broker telemetry sources do not yet provide quote age, order latency, reject rate, reconciliation, spread, or account-state measurements; those fields remain explicitly unavailable and no health score is calculated. `PromotionDecision` v1 now captures per-stage evaluation status and provenance, while always returning WAIT_FOR_EVIDENCE with policy approval false. It does not evaluate evidence or authorize promotion; connected evaluation sources, independent approval, promotion execution, and rollback remain P11 work.

## Verification

`tests/test_api.py` checks the generated request schema against the published v1 JSON Schema, validates the schema itself, preserves omitted-version clients, and rejects unsupported explicit versions. `tests/test_canonical_contract_schemas.py` checks the internal dataclass schemas and AgentEnvelope v2 against their runtime types, and checks strict schema validation for unknown fields. `tests/test_broker_health_api.py` checks the protected endpoint, unassessed metrics, schema parity, and the fixed false release flag. `tests/test_promotion_contracts.py` checks evidence provenance, fail-closed approval, and schema parity. Neither contract changes database records or enables promotion.
