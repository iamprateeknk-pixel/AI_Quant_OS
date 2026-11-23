# 0151 — Route agent model calls through the durable graph runtime

## Decision

GraphRuntime will use the existing AI Cost Governor router for executions that
are explicitly allow-listed by `(agent_id, agent_version)`. Providers remain
disabled unless an application injects an `AgentModelRouter` with registered
provider adapters. The allow-list is empty by default. Other graph runs keep
the existing deterministic path and have no routing receipt.

The provider adapter receives the governed task, admitted tier, and the current
typed `AgentInvocation`. Its output remains subject to the existing graph
timeout, context-currentness, output-schema validation, size bound, and
escalation requirements. Provider exceptions fall back to the deterministic
implementation and are recorded as such. Model calls cannot grant tool
permissions or capital authority.

The durable graph record gains an optional routing receipt. Existing records
without routing remain byte-for-byte canonical and retain GraphTrace schema
1.0. Traces with routing use additive schema 1.1; clients continue accepting
1.0 and validate 1.1 routing metadata. The receipt records tier, provider
selection/attempt, governor decision, fallback reason, and actual token usage
when known. It never records prompts, context payloads, or credentials.

## Reason

`AgentModelRouter` already provides budget admission, deterministic fallback,
and explicit provider registration, but GraphRuntime did not call it. As a
result, graph runs could not use governed model routing and saved traces could
not show whether an agent used a provider or fell back. This decision connects
the existing router without enabling ambient providers or changing current
agent behavior by default.

## Compatibility and verification

The routing object is optional and omitted from serialized legacy records when
absent. A routed record is versioned and included in the same integrity-protected
graph record hash and transition audit chain. The trace API adds an optional
routing object only for schema 1.1. The Mission Control parser accepts both
trace versions and renders provider/tier/fallback/token metadata without
exposing model prompts or agent context.

Tests will cover provider success and token accounting, schema rejection,
provider failure fallback, durable restart/readback, legacy record
compatibility, and client parsing/rendering. Providers and model routing remain
off by default. Real provider quality, mission calibration, operating costs,
and independent P07 acceptance still require external evaluation evidence.

## Rollback

Stop supplying the router and allow-list to GraphRuntime; deterministic
execution remains the default. Existing records remain readable. To remove the
optional receipt in a future release, first preserve the 1.0/1.1 reader and
define an explicit archival migration; do not rewrite existing audit records.
