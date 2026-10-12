# Contract versioning status

## Current policy

Published wire contracts use an explicit JSON Schema draft and stable `$id`. A breaking change requires a new contract version, a compatibility or migration plan, producer and consumer tests, and an ADR. Existing clients remain compatible only where the request model explicitly supports it; unversioned requests must not be silently interpreted as a newer incompatible contract.

## Implemented contract

| Contract | Version | Runtime behavior | Schema |
| --- | --- | --- | --- |
| Paper `TradeIntent` request | 1.0 | Accepts legacy requests without `schema_version` as v1, accepts explicit `1.0`, rejects unknown versions, and excludes the version marker from the internal domain dataclass | `schemas/trade-intent-v1.schema.json` |
| Advisory `AgentEnvelope` | 2.0 | Strict immutable Pydantic model; graph runtime validates the manifest-bound output model before retaining results | `agent_envelope.py` |

## Remaining canonical contracts

`RiskDecision` and `OrderCommand` still use internal dataclasses without a published versioned wire schema. The blueprint's `BrokerHealth` and `PromotionDecision` contracts do not yet have corresponding runtime models. They remain required P00/P04/P11 work and are not implied by the TradeIntent v1 schema.

## Verification

`tests/test_api.py` checks the generated request schema against the published v1 JSON Schema, validates the schema itself, preserves omitted-version clients, and rejects unsupported explicit versions. No database migration or response format change is introduced by this increment.
