# Validation Scientist advisory graphs

A10 version 0.2.0 is registered as PARTIAL and SHADOW under the original role
ceiling. Version 0.1.0 and the other 18 agent identities remain unchanged. This
implementation summarizes previously checked archival validation. Original-source
execution and numeric evaluation remain the admitted worker/validator workflow;
`VALIDATION_RUN` retrieves and checks a retained observation. It does not initiate
worker execution, issue an acceptance certificate or promote a candidate.

Archive access and the base application work without the optional native trading
engine. Checked numeric publication loads that dependency only when requested.

## Operator workflow

Publish a checked failure or scorecard with the existing validation command into
the application's sibling `<paper-db-stem>.candidate-validations.sqlite3` archive.
The original package must be present in the sibling validation-package archive.
Use the protected routes:

| Route | Input or result |
| --- | --- |
| POST `/api/v1/sandbox/validation-scientist/context` | `task_id`, `artifact_ref`; returns a retained context checkpoint |
| POST `/api/v1/sandbox/validation-scientist/execute` | `run_id`, `context_ref`; returns an advisory graph receipt |
| POST `/api/v1/sandbox/validation-scientist/graph-runs/{run_id}/resume` | Returns the original durable receipt without repeating completed execution |
| GET `/api/v1/sandbox/validation-scientist/results/{artifact_ref}` | Historical checked artifact; operator authentication required |

Context capture rechecks the archive and original package. All three required
inputs are compact HOLDOUT metadata: strategy_candidate, immutable_datasets and
execution_models. No source code, SDK, dataset rows, fills or large receipt arrays
are exposed to the agent. Training and holdout receipt references retain lineage.
Hypothesis generation and strategy building cannot fetch these inputs even through
otherwise authorized read-tool aliases. Context limits are 16 KiB per input and
64 KiB overall, with a maximum five-second lifetime. Future package captures are
refused. Freshness describes the current archive inspection; it does not make old
market observations current.

The task-bound tool requires exact A10/version/SANDBOX identity, an empty payload,
the original checkpoint and current role/lifecycle admission. It reads the verified
archive through the existing owned thread helper, rechecks admission after that
work, and compares the full compact summary with the context. The agent checks the
tool's artifact reference and summary hash before producing its typed v2 envelope.
Output revalidation binds all evidence, times, reasons and original observations.

## Outcomes and health

All fifteen gates are retained. A failed prerequisite has no trading statistics;
its first gate fails and downstream gates remain pending. Scorecard summaries
retain checked numeric metrics, currency and requested Monte Carlo seed/trials.
Outcome and reasons must agree with the gates. Independent acceptance gates remain
pending. Confidence is zero, uncertainty is one, escalation is required and every
isolation/promotion authority field remains false.

A rejected candidate or failed prerequisite is a successfully completed advisory
agent task. It does not by itself create an agent-health incident. An invalid
archive or output withholds the graph output. Actual agent exceptions and deadlines
produce FAILED/TIMEOUT receipts. Enabled health observation records those incidents,
quarantines the exact agent version and follows the existing deterministic safety
policy. Neither this agent nor its tool can change that policy or recover trading.

Long-running validation orchestration, uncertain worker failure records,
larger Monte Carlo plans, independent source/adversarial acceptance, physical and
statistical isolation, champion/shadow acceptance and production release gates
remain required for the full execution pack.

## Verification

The repaired complete regression passed 1,978 tests with 15 optional integrations
skipped and all six existing local web/Linux gates enabled. All 34 focused checks
also passed from a fresh staged checkout. The actual worker scorecard produced a
durable A10 COMPLETED receipt, whose original context and summary bindings were
revalidated. Tested source, tests and schemas match the staged index and fresh
checkout. Prior wire schemas and worker controls were preserved; staged secret
scanning found no leaks. The first run's five failures and their repairs are
retained in `docs/evaluations/validation-scientist-0086.json`.
