# Completion receipts and native numeric replay

## Implemented flow

The entry point is `python -m ai_quant_os.strategy_execution_tape_cli`.
It reads an existing original validation package archive and inert JSON metadata.
It imports no proposed candidate, SDK or tests and invokes no container command.

1. Collect each successful, removed `StrategyPrefixExecutionReceipt` from the prior
   owned execution flow. Preserve the original independent image and worker pins.
   If using local execution evidence, first restore each receipt with its original
   plan, package, job and independent receipt hash using the custody controls.
2. Save a JSON array of those receipts and retain its SHA-256 independently.
   `assemble` checks the original complete decision inventory, exact invocation
   identities, source/image pins, successful exit/output/removal bindings and unique
   owner/job/container custody. The first two warmup targets remain zero.
3. Preserve the resulting collection file and its independent SHA-256. `replay`
   compares both byte and image/source pins, reconstructs the tape against the
   original package, runs the trusted native numeric adapter and verifies the ledger.

For `assemble`, pass `--archive`, `--package-ref`, `--receipts`,
`--receipts-sha256`, `--image-id`, `--worker-sha256` and `--output`.
For `replay`, use `--collection` and `--collection-sha256` instead of the two
receipt arguments. Optional replay parameters are `--quantity` (default 1),
`--initial-balance` (default 100000 quote-currency units) and `--cost-scenario`
(`BASE` or `DECLARED_STRESS`, default `BASE`). Retain independently chosen pins;
computing a fresh hash from a mutable file at recovery time does not establish trust.

Inputs and outputs are bounded to 128 MiB; inventory is bounded to 4,094 receipts
for the original maximum 4,096 bars. JSON rejects duplicate fields and non-finite
values. Input files must be ordinary, bounded, unchanged single-link files.
Outputs use exclusive creation and fsync; existing outputs are refused. Partial
output after an I/O failure is retained and must not be automatically overwritten.

## Evidence and limits

Retained schemas are `docs/schemas/strategy-execution-tape.schema.json` and
`docs/schemas/strategy-execution-replay.schema.json`. Collection references use
`strategy-execution-tape:`; native report references use `strategy-execution-replay:`.
Collections are explicitly operator-pinned, unattested completion metadata.
Successful metadata binding is not daemon/kernel/source attestation. False execution
and promotion authority remains explicit in both models and schemas.

Portable Windows/POSIX metadata is accepted without accessing the named directories.
Windows drive/case/separator and dot aliases cannot reuse one job in the collection.
Actual local journals still reject foreign roots, and custody still requires the
original staged job. No local ownership sidecars or inputs are released by this flow.

The nested replay uses NautilusTrader 1.221.0, synthetic next-open quotes, declared
costs, quote-currency fees, collateral admission and final-close marking. It replays
only numeric targets; its `candidate_code_executed=false` describes that replay
component. It makes no claim about source behavior, financing, account-currency
conversion, independent native realized P&L or alpha. Candidate source/test execution,
independent attestation, statistical acceptance and promotion remain open.

## Verified increment 0076

The full locked Windows regression passed **1,790 tests with 15 optional skips in
647.55 seconds**, with the local web gate required and retained exit zero. Source
files stayed unchanged during that run. The affected custody/execution/collection
suite passed **120 tests in 61.92 seconds**. The new 15 cases cover real native
numeric replay, declared stress, complete coverage, foreign/reused identities,
portable metadata, unchanged local-root refusal, schema authority denial, rehashed
target/P&L refusal and both actual operator flows. Completion metadata in those cases
is synthetic; no candidate was run. Lint and formatting passed for 355 Python files.

The fresh staged-index checkout passed all **15 new cases in 9.57 seconds**, with
retained exit zero. Seven source/test/schema files match staged/fresh content after
Git newline normalization. Eleven prior schemas match HEAD and their models; worker,
archive identity, staging, journal, policy and prior execution/lifecycle sources remain
unchanged. Both dependency locks and installed consistency passed. Structure mapping
found zero import cycles/unreferenced files; automatic layer enforcement remains
undeclared. The local UI/API returned 200; the fixed Docker Linux named pipe is absent.
See docs/BUILD_STATUS.md and ADR 0076 for remaining scope.
