# Original-source candidate statistical scorecards

The scorecard consumes the original validation package, a source-bound passed
proposed-test observation, the full holdout run report and every ordered development
window report. Missing, duplicated, reordered, foreign or substituted intervals
are refused. Partial final windows remain present and cannot satisfy complete
walk-forward or stability screens. Each BASE/stress replay is checked against the
original numeric tape, closed history, fills, cost model and quote-currency ledger.
Worker content, quantity and initial balance must agree across runs. Portable
metadata may retain different original archive locations; immutable image/source
hashes must match. Worker completion identities cannot be reused across intervals.

## Gates

Every scorecard retains these fifteen gates:

1. Proposed tests: source-bound, observed proposed cases; unattested.
2. Out of sample: declared holdout net-P&L screen.
3. Walk forward: retrospective development P&L screen; incomplete intervals pending.
4. Stability: declared nonnegative-window fraction; incomplete intervals pending.
5. Cost stress: all original native declared-stress P&Ls nonnegative.
6. Activity: declared minimum native orders in every interval.
7. Monte Carlo: original closed cash flows, exact declared seed/trials and policy.
8. Independent holdout: pending physical/statistical isolation evidence.
9. Source attestation: pending independent source/daemon evidence.
10. Adversarial: pending proposed-source adversarial validation.
11. Reality gap: pending broker, authentic market and liquidity evidence.
12. Financing: pending actual financing costs.
13. Account conversion: pending account-currency conversion.
14. Independent review: pending critical review.
15. Shadow: pending observed shadow performance.

The first numeric screens are descriptive under the operator-declared synthetic
model. Training selection overlap remains true and independence is unassessed.
No accepted or promoted outcome is available. A measured failure yields REJECTED
with its reasons; otherwise missing evidence yields VALIDATION_INCOMPLETE.

Monte Carlo uses circular completed-trade blocks and the existing explicit
equal-size reversal/half-fee policy. Open positions are not force-closed. Samples
below the declared minimum remain pending. The exact requested trial count is
retained; a request beyond the admitted 2,000-trial/500,000-sampled-trade engine
capacity remains explicitly pending and is never lowered. Supporting larger
plans requires a separately admitted computation path.

## Retain and verify

Use `python -m ai_quant_os.candidate_validation_cli` with global options:

```text
--validation-archive <existing original archive>
--package-ref <original validation-package reference>
--test-result <canonical proposed-test result path> <independent SHA-256>
--holdout-report <canonical original run report path> <independent SHA-256>
--development-report <canonical fold-0 report path> <independent SHA-256>
--development-report <canonical fold-1 report path> <independent SHA-256>
```

Repeat the development option in exact declared order through the final interval.
These are portable, independently byte-pinned metadata, not local execution grants.
An optional `--policy PATH SHA256` supplies the canonical serialized typed policy.
Its currency must match native instrument quote P&L; the default is USD. JPY
instruments require an explicit JPY policy. All thresholds are retained in the
scorecard and confer no capital-control permission.

Append `evaluate --output PATH` to exclusively publish/fsync the canonical scorecard.
Retain its SHA-256 independently. Publication refuses existing files. Append
`verify --scorecard PATH SHA256` to reconstruct and compare all source/data/replay/
policy/simulation bindings in a fresh process. Verification uses the scorecard's
original policy and refuses a policy override. No worker, broker or proposed source
is invoked by either command. Inputs are bounded individually to 128 MiB and
collectively to 256 MiB. Failed/partial publication requires investigation; no
automatic overwrite or guessed recovery occurs.

## Remaining full scope

The local statistical producer does not implement the A10 mission envelope,
health/budget/graph integration, independent certification, larger computation
plans or production acceptance. Failed compilation/test prerequisites have their
separate retained candidate-test status; they cannot fabricate completed native
run evidence. Full failure archival and mission integration, physical holdout
isolation, source/adversarial/champion gates and every production release gate
remain required. The complete P00–P13 build remains in progress.
