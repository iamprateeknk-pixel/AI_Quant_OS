# ADR 0100: Share routine call quotas with declared context fetches

Date: 2026-10-09

## Decision

Inject the existing owner-defined CallBudget protocol into ContextCompiler and all
API compiler construction paths. Reserve each declared read after permission checks
and before its fetch, using ToolInvocation identity and a hash-bound input/class
descriptor. Recheck permission after asynchronous admission. Preserve the existing
context schema and degradation states.

## Consequences

Graph tool and declared context read attempts share role capacity across versions,
periods and restarts. Missing/denied inputs do not consume calls; admitted failed
or interrupted attempts remain counted. Overall timeout/cancellation can withhold
fetching while an already-running admission thread still commits. Independent
paper safety controls remain available. A10 denial returns its existing admission
error before comparing a required complete validation summary.

Snapshot preparation before compilation, standalone unconfigured callers and
legacy provider calls remain outside the boundary. Token/monetary budgets, recovery
spending, retention/migration and independent acceptance remain unfinished.
P00-P13 is not complete.
