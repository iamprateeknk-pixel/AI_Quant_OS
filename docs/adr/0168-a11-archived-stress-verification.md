# ADR 0168: A11 checks archived stress evidence

## Decision

Add A11 0.2.0 as a partial sandbox graph mission over completed schema 1.2
research reports. Bind the selected genome, validation gates, scenario library,
and original report digest in a checked context. Before producing its scorecard,
reload the report and independently run the deterministic scenario evidence
verifier through the task-bound `STRESS_RUN` handler.

## Consequences

The mission makes existing perturbation evidence reviewable through the agent
runtime without executing candidate source. Results are bounded, source-bound,
and advisory. It does not create new stress runs or satisfy the full A11
acceptance checklist. Bad-fill/data tests, leakage analysis, physical isolation,
shadow evaluation, independent review, and the remaining P00–P13 gates stay
open.
