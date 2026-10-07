# Candidate development-window execution

An explicit training view allows the original proposed strategy to execute in
retrospective development intervals without relabeling training data as holdout.
The original validation archive, candidate bytes, SDK, plan and passed proposed-test
observation remain bound to every window.

## Window definition

For fold `k`, validation begins at `train_bars + k * test_bars` and stops at the
smaller of the declared end or dataset end. Two preceding closed bars are included
for the existing strategy interface. A partial final fold records
`complete_declared_window=false`. A nonexistent fold is refused. The data receipt
continues to identify TRAINING. Selection overlap is true and statistical
independence is unassessed.

The fixed worker receives only each decision's original source/SDK and closed
history envelope. It does not receive either complete dataset. Native replay uses
the selected window with its exact numeric tape at BASE and DECLARED_STRESS costs.

## Operator flow

First complete the source-only candidate test job described in
CANDIDATE_TEST_HANDOFF.md. Retain its independent manifest SHA-256. Add these global
options before the existing `prepare`, `run`, or recovery command in
STRATEGY_PREFIX_RUN_HANDOFF.md:

```text
--development-fold 0
--candidate-test-root <retained test job root>
--candidate-test-manifest-sha256 <independently retained SHA-256>
```

Each fold needs a new empty owned run root and its independently retained run hash.
Supplying test options without a fold, or selecting a fold without both evidence
options, is refused. Original CLI calls without these options retain holdout behavior.
Cached retrieval uses the original window/test/run pins and does not restart workers.

## Evidence and limits

The focused verification passed 20 checks in 38.66 seconds, including six proposed
tests and two development folds with two actual source decisions each, exact owned
removal/release and fresh-process report retrieval. Unit metadata is explicitly
unattested; actual integration evidence is retained separately under `data-local`.
Original worker/image/guard and confinement limits are unchanged.

These windows are development evidence. No statistical scorecard or independent
walk-forward acceptance is implied. Partial folds cannot be silently counted as
full planned intervals. Physical holdout isolation, independent source/daemon
attestation, statistical/champion acceptance and the full production build remain
required. All authority fields remain false.
