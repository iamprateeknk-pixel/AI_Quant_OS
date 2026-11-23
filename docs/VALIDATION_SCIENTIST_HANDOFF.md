# Validation Scientist advisory graphs

A10 version 0.2.0 is registered as PARTIAL and SHADOW under the original role
ceiling. Version 0.1.0 and the other 18 agent identities remain unchanged. This
implementation summarizes previously checked archival validation. Original-source
execution and numeric evaluation remain the admitted worker/validator workflow;
`VALIDATION_RUN` retrieves and checks a retained observation. It does not initiate
worker execution, issue an acceptance certificate or promote a candidate.

Archive access and the base application work without the optional native trading
engine. Checked numeric publication loads that dependency only when requested.
The PAPER API does not construct or read a candidate-validation archive; A10
validation checks, context snapshots, and retained-result summaries go through
the HOLDOUT service. The service owns the package, candidate-validation, and
HOLDOUT stores.

## Operator workflow

An A10 result is visible only after it is present in the service-owned
candidate-validation archive and bound to a service-owned validation package.
The installer stages existing archives, and the operator CLI can publish new
validation results through service RPC. Service installation, archive refresh
operations, and Windows identity/ACL acceptance remain open. Use the protected
routes for results retained by the service:

| Route | Input or result |
| --- | --- |
| POST `/api/v1/sandbox/validation-scientist/context` | `task_id`, `artifact_ref`; returns a retained context checkpoint |
| POST `/api/v1/sandbox/validation-scientist/execute` | `run_id`, `context_ref`; returns an advisory graph receipt |
| POST `/api/v1/sandbox/validation-scientist/graph-runs/{run_id}/resume` | Returns the original durable receipt without repeating completed execution |
| GET `/api/v1/sandbox/validation-scientist/results/{artifact_ref}` | Checked summary only; operator authentication required |

The GET response returns the service-verified `ValidationSummary`, not the full
scorecard or prerequisite artifact. It contains the checked gate and aggregate
statistics needed by the operator while keeping artifact storage in the service.

For post-install publication, use `ai_quant_os.candidate_validation_cli` with
`--service` and `archive-failure` or `archive-evaluation`. Supply the original
test-result file and SHA-256 pin; scorecard publication also requires the
holdout report, every declared development report, and their pins. Do not pass a
local validation archive or result archive in service mode. The service fetches
the package and HOLDOUT evidence from its own stores, recomputes the failure or
scorecard, and retains the result in its audited candidate-validation archive.
It returns only the published reference and checked summary digest. A request
whose encoded evidence exceeds the 2 MiB RPC frame limit is refused.

Context capture rechecks the service archive and original package. All three
required inputs are compact HOLDOUT metadata: strategy_candidate,
immutable_datasets and execution_models. No source code, SDK, dataset rows,
fills or large receipt arrays are exposed to the agent. Training and holdout
receipt references retain lineage.
Hypothesis generation and strategy building cannot fetch these inputs even through
otherwise authorized read-tool aliases. Context limits are 16 KiB per input and
64 KiB overall, with a maximum five-second lifetime. Future package captures are
refused. Freshness describes the current archive inspection; it does not make old
market observations current.

The task-bound tool requires exact A10/version/SANDBOX identity, an empty payload,
the original checkpoint and current role/lifecycle admission. It sends the
artifact and context bindings to the service, which rechecks the archived package,
validation record, and service-owned HOLDOUT bytes. The API compares the returned
aggregate with the context summary and rechecks admission. The agent checks the
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

Local original-source Temporal orchestration is described in
VALIDATION_JOB_HANDOFF.md. Early worker failure classifications,
larger Monte Carlo plans, independent source/adversarial acceptance, physical and
statistical isolation, champion/shadow acceptance and production release gates
remain required for the full execution pack.

## Verification

Historical staged verification passed 1,978 tests with 15 optional integrations
skipped and all six existing local web/Linux gates enabled. All 34 focused checks
also passed from a fresh staged checkout. The actual worker scorecard produced a
durable A10 COMPLETED receipt, whose original context and summary bindings were
revalidated. The current full locked backend result is recorded in
`docs/BUILD_STATUS.md`. Prior wire schemas and worker controls were preserved;
staged secret scanning found no leaks. The first run's five failures and their
repairs are retained in `docs/evaluations/validation-scientist-0086.json`.
