# Strategy provenance handoff

## Implemented contract

StrategyGenome binds a versioned strategy reference, BAR/TICK input kind and a bounded immutable set of named integer parameters. Parameter order is canonical; booleans, duplicates, unsupported names, invalid windows and incompatible inputs are rejected before implementation registration or native execution. Defaults are resolved explicitly into the result's genome. Changing moving-average windows changes its genome fingerprint while retaining the implementation version.

strategy_manifests.json is packaged with the distribution and seals the three trusted built-in implementations. Each manifest records its entry point, defaults and SHA-256 inventory of the strategy module and its declared local domain dependencies. File-level hashes normalize universal line endings to UTF-8/LF so Windows checkout conversion does not change identity. Changes anywhere in an inventoried file require reviewed new implementation versions for all affected strategies; manifests are not refreshed automatically at runtime.

StrategyRegistry stores canonical implementation records under versioned names using atomic, exclusive immutable writes. Re-registering the same record is idempotent; different content under the same version fails. Exact normalized source bytes are retained under content-addressed objects. Retrieval verifies version identity, manifest fingerprint, source object hashes and bounded object sizes. It never executes archived source. Preserve the registry directory and matching wheel when retaining an experiment for later replay.

run_registered_backtest checks input kind, verifies current source against the packaged manifest, constructs only an allowlisted built-in class with validated parameters, registers the implementation, runs actual Nautilus execution and rechecks source and registry identity before returning. Its result includes the resolved genome, implementation inventory and both fingerprints. No generated Python or arbitrary import path is accepted. The lower-level trusted-plugin runners remain available and do not claim this binding.

## CLI and artifacts

ai-quant-backtest now uses registered execution for bars and ticks. It accepts --fast-window and --slow-window for moving-average-cross and --registry for an explicit registry directory. The default directory is .strategy-registry beside the output artifact; it is ignored by Git. Saved reports add strategy_binding to the existing native result and raw source fingerprint. Both checked-in sample reports were regenerated and their schema includes the complete binding.

```powershell
ai-quant-backtest tests/fixtures/nautilus_replay.csv --strategy moving-average-cross --fast-window 1 --slow-window 2 --registry data-local/strategies --output data-local/backtest.json
python -m pytest -q tests/test_strategy_registry.py
python -m pip wheel --no-deps . -w dist
```

Tests cover canonical genomes, default resolution, restart retrieval, same-version conflict, invalid/unknown/incompatible construction, modified source/manifest detection, concurrent idempotent registration, binding mismatch, retained source corruption, native reproducibility and actual CLI binding. A built wheel was loaded from an isolated temporary directory and all three packaged manifests verified against its installed sources.

## Limits and rollback

This is provenance for trusted repository code. It is not an operating-system sandbox, cryptographic publisher signature or defense against a host administrator who replaces both the package and its manifests. The inventory covers declared local plugin dependencies; it does not claim to hash the entire interpreter, standard library or native engine. Full environment locking, signed promotion, sandbox building, independent review and research validation gates remain open. Binding alone grants no trading or promotion authority.

Source objects are committed before their manifest; interruption can leave unreferenced immutable objects. Objects are not automatically deleted. Missing or altered retained objects fail retrieval. Missing source artifacts can be restored only through checked registration of the same installed implementation. An old package cannot execute a changed implementation under an existing version.

Rollback preserves the registry and result artifacts. Earlier CLI schemas do not understand strategy_binding; use the recorded versioned schema for these artifacts. No paper-order database migration or broker connection is introduced.
