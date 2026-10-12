# A11 adversarial validator handoff

## Delivered

A11 0.2.0 is registered as a partial shadow mission. Its protected sandbox route
accepts an experiment ID, compiles a short lived context from one immutable P08
research archive, and executes the result through the durable agent graph. The
agent binds the selected genome and source implementation fingerprint, validation
gate inventory, declared perturbation cases, original holdout fingerprint, and
the report digest. Its `STRESS_RUN` handler reloads the report, rechecks the exact
context projections, and reruns the deterministic native evidence verifier before
returning a typed receipt.

Routes:

- `POST /api/v1/sandbox/adversarial-validator/context`
- `POST /api/v1/sandbox/adversarial-validator/execute`
- `POST /api/v1/sandbox/adversarial-validator/graph-runs/{run_id}/resume`

The output reports `FAILURE_FOUND`, `NO_FAILURE_FOUND`, or `EVIDENCE_PENDING`.
Confidence remains zero, uncertainty remains one, escalation is always required,
and promotion authority is false. Completed graph receipts remain advisory.

## Verification

- 170 focused agent, API, registry, context, call-budget, and graph tests passed
  in a fresh archive checkout with changed source files overlaid.
- Ruff lint passed for the changed Python files.
- The final formatter check is recorded in evaluation 0168.

## Limits

This mission revalidates archived native scenario results. It does not run newly
generated candidate code, create a fresh stress experiment, prove physical holdout
isolation, check bad-fill or data-provider behavior, or perform independent
leakage review. A `NO_FAILURE_FOUND` result is not an approval or a promotion
gate. Candidate execution, full A11 acceptance, shadow evaluation, independent
review, and all P00–P13 release gates remain open.

## Rollback

Revert the A11 agent, service, API routes, registration, tests, and handoff
together. Preserve all existing research archives and graph receipts.
