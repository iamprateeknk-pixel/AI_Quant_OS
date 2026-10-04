# Removed prefix input release

## Operator flow

The explicit entry point is `python -m ai_quant_os.strategy_prefix_release_cli`.
It imports no proposed candidate, SDK or tests and issues no daemon command during
release. It supports successful completed prefixes with the original audited
container removal; failed, running, uncertain or unproved jobs remain retained.

Common arguments before the subcommand are `--validation-archive`, `--package-ref`,
`--root` and `--job-name`. The archive must already contain the exact original package.

1. `prepare` additionally requires `--index`, `--stage-sha256`, `--create-sha256`,
   `--ownership-sha256` and `--receipt-sha256`. These are the original independently
   retained stage/plan/completion hashes. It restores the old custody controls,
   requires current audited REMOVED, captures all eight exact files and publishes
   `JOB_NAME.release.json` exclusively. The printed `release_sha256` must be retained
   independently before the next step. Preparation removes nothing and never adopts
   an existing recovery archive.
2. `release` requires `--release-sha256`. It reconstructs bindings directly from
   that pinned archive and the original package, so an interrupted deletion does
   not require loading a now-missing staged job. It rechecks native parent identity,
   current audited removal, exact surviving file identities/bytes and scoped
   inventory before deletion. Missing inputs are refused until an exact prior
   release attempt exists.

The recovery bundle retains the request plus stage, ownership, create recipe,
create attempt, start attempt, target and execution receipt. Original request
source/SDK/history identity and full create/start bytes are rechecked against the
package and preserved recipe helpers. Files must be ordinary bounded single-link
files. The bundle is bounded to 2 MiB; per-file limits remain the original limits.
Syntactic schema validation does not replace the original-package/root/audit checks.

## Exact deletion and recovery

The permanent root lock is held while the archive is re-fsynced and an exact
`JOB_NAME.release-attempt.json` is exclusively published/fsynced. Only then may the
known request be unlinked, its now-empty directory removed, and the seven exact
sidecars unlinked. There is no recursive deletion or neighbour/image cleanup.
Changed or unknown files are refused and retained. Existing attempt/final records
must match the original independent archive and are re-fsynced on retry.

Successful release retains `JOB_NAME.release.json`, the attempt, deterministic
`JOB_NAME.released.json`, ownership history and `.strategy-owner.lock`. The recovered
bundle's receipt can be supplied to the prior complete-inventory numeric replay
flow after original-package binding. Reappeared files after final release are
refused and never automatically deleted. This frees the staged-directory slot;
it does not erase recovery/audit custody or change their retention capacities.

Filesystem failures can leave a partial archive/attempt/deletion/final observation.
Retain the original independent archive hash and original package. Retry only the
same explicit `release` operation after resolving the I/O condition. Missing or
changed roots, archives, audit history, file identities or attempt records cannot
be inferred or automatically repaired. The old journal constructor may initialize
an empty file if its journal is missing; no deletion is admitted without the old
removal history. Directory-entry power-loss durability and malicious host writers
are unverified; Windows read-only mode is not a hardened ACL or physical isolation.

## Verification

All **27 new checks passed in 20.58 seconds**. A fresh staged-index checkout passed
the same 27 in **23.37 seconds**, with retained exit zero. Checks cover exact offline
idempotence, capacity recovery without neighbouring deletion, original pins,
changed/unknown files, original request/recipe reconstruction, false-authority
schemas, actual prepare/release CLI use, missing inputs/attempt/history, changed
roots and file identities, integer/boolean identity refusal, competing root custody,
partial request/directory/sidecar deletion and archive/attempt/final fsync failures.
All daemon evidence is simulated; no proposed source was executed.

Thirteen prior wire schemas match their models and HEAD (the two 0076 schemas after
Git newline normalization). Seven new source/test/schema files match the staged
and fresh-checkout content after Git newline normalization. Worker/archive identity,
staging, journal, policy, ownership, lifecycle/execution and completion replay
sources remain unchanged. Both 96-package dependency locks and installed consistency
passed. Lint/format passed for 360 Python files; mapping found no import cycles or
unreferenced files. Automatic layer enforcement remains undeclared.

The full locked Windows regression passed **1,817 tests with 15 optional skips in
677.59 seconds**, with retained log/exit zero and the local web gate required. Source
stayed unchanged during the run. All 100 prior commits and staged changes passed
secret scanning. The local UI and system-status API returned 200; the fixed Docker
Linux endpoint remains absent. Actual engine/kernel compatibility, source attestation, separate
proposed compilation/tests, full-prefix orchestration, statistical acceptance,
promotion and all production gates remain open. See ADR 0077.
