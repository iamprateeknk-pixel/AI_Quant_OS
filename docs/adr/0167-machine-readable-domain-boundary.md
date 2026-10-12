# ADR 0167: Enforce the declared deterministic domain boundary

## Decision

Represent the five deterministic modules already named in `.clean/architecture.md`
as a machine-readable domain slice and enforce its forbidden import prefixes in
the architecture test. The check includes literal dynamic import forms so an alias
does not silently bypass the rule.

## Consequences

The previously manual boundary review for these modules now has a repeatable local
and CI check. It does not classify all package modules, prove an indirect runtime
call graph, or detect dynamically constructed import names. Those limits remain
visible in the handoff and evaluation. P00–P13 acceptance remains incomplete.
