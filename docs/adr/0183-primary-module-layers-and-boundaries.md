# Assign primary layers and enforce module dependency rules

## Decision

Assign all 323 inventoried Python modules to one primary layer: domain, control, API, agents, strategies, persistence, integrations, or application. Enforce five current import boundaries: domain purity, control isolation from API/agent/strategy code, agent/strategy isolation from control code, persistence isolation from API code, and integration isolation from API code.

## Consequences

- Layer membership and boundary rules are machine-readable in `.clean/architecture.json` and tested with the existing pytest suite.
- Agent and strategy sources cannot directly import deterministic control modules. Unresolved dynamic imports in restricted sources fail closed.
- The API remains the composition boundary; application and API layers are not fully constrained against each other or every other dependency.
- These static checks do not prove transitive runtime call-graph isolation, all API authorization, or environment/network boundaries. P00 architecture acceptance and independent review remain open.

## Verification

- Architecture boundary suite: 10 passed in an isolated archive checkout.
- Guard probe: injected `import ai_quant_os.paper_recovery` into a strategy agent in a fresh archive checkout; `test_declared_layer_import_rules_hold_for_current_sources` failed and reported the source path and forbidden target.
- Full source baseline remained green; no probe change was copied into the workspace.

## Rollback

Revert the layer mapping, import rules, tests, documentation, and ADR together. No runtime behavior changes.