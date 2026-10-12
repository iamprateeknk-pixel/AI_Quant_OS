# ADR 0170: A13 withholds allocation weights while evidence is incomplete

## Decision

Register A13 0.2.0 as a PARTIAL/SHADOW paper graph mission. Bind the disconnected upstream strategy set, current aggregate portfolio snapshot, configured risk limits and safety state, unavailable validated covariance, and current single-instrument exposure. Return an empty allocation proposal with explicit blockers, zero confidence, and no execution or promotion authority.

## Consequences

The mission exposes current evidence gaps without duplicating or weakening the deterministic risk kernel. It does not connect A12 graph outputs, calculate covariance, measure complete cross-instrument exposure, calibrate risk budgets, or authorize portfolio weights. Independent mission acceptance and all remaining P00–P13 gates stay open.
