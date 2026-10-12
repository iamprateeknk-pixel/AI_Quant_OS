# ADR 0169: A12 withholds strategy eligibility while evidence is incomplete

## Decision

Register A12 0.2.0 as a PARTIAL/SHADOW paper graph mission. Bind the current research candidate registry snapshot, WorldState, and explicit unavailable candidate performance and drift observations into its context. At execution, re-read the registry through a task-bound read tool and reject a changed snapshot. Return deterministic exclusions and blockers with no eligible strategies while approved/validated lifecycle, candidate keyed metrics, and drift evidence are unavailable.

## Consequences

The mission exercises the agent runtime and exposes missing evidence without granting order, lifecycle, or promotion authority. It does not rank strategies, infer validation from research outcomes, supply regime/portfolio fit, route models, or calculate quality/calibration metrics. A12 acceptance and all remaining P00–P13 release gates stay open.
