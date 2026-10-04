# Candidate prefix invocation boundary

## Current implementation

The trusted parent prepares one bounded, inert JSON invocation for each non-warmup holdout open. `candidate_invocations.prepare_invocation(package, index)` reconstructs the entire checked package inside the parent, selects exactly the previous two closed bars, and returns only:

- Package and candidate hash references, index, instrument and current execution time.
- Exact `candidate.py` text/hash and the four retained SDK files.
- Two earlier OHLCV bars and their canonical Decimal-28 history fingerprint.
- Fixed waiting/unverified/no-promotion status.

The JSON excludes the full holdout, training CSV, graph, review, cost plan, proposed test file, current-bar prices, later bars and total holdout length. Package/candidate references are identifiers, not data-access capabilities. The first two opens require flat targets and produce no worker invocation. Each source remains an inert JSON string. No import, compilation, candidate call, proposed test, Docker start or executable source materialization occurs.

The request must be bound again with `CandidateInvocation.require_package(package)` at any future launcher boundary. Standalone model/schema validation proves neither original package identity nor source trust. It checks inventory, UTC/chronology, instrument, OHLCV, byte hashes and local history consistency; exact package comparison also rejects self-consistently altered history/source/SDK. The retained static SDK remains observed source, not an admitted worker runtime.

## Operator commands

Use the locked project environment and an existing audited validation package archive:

```powershell
python -m ai_quant_os.candidate_invocation_cli --archive PATH_TO_EXISTING_ARCHIVE --package-ref validation-package:EXACT_HASH prepare --index 2 --output request.json
```

The output is a single JSON envelope, limited to 512 KiB. Existing output files are preserved through exclusive creation. Standard output contains only reference/status/index metadata. This is preparation, not a launch command.

To assemble supplied target responses:

```powershell
python -m ai_quant_os.candidate_invocation_cli --archive PATH_TO_EXISTING_ARCHIVE --package-ref validation-package:EXACT_HASH assemble --response target-2.json --response target-3.json --output tape.json
```

Supply exactly one response for every non-warmup open. Each JSON object contains its exact `candidate-invocation:` hash and a strict integer target of -1, 0 or 1. `CandidateTargetOutput.from_wire` enforces a 2 KiB input bound, unique JSON keys, finite JSON values and an object root. Unknown fields, claimed isolation/promotion and boolean/non-unit targets are refused. The request decoder similarly rejects duplicate keys, invalid/nested JSON and its 512 KiB bound.

The parent round-trips supplied models before use, requires exact invocation coverage without duplicates/foreign results, restores open-time order and fixed flat warmup, and checks the resulting tape against the package. The assembled tape retains `OPERATOR_SUPPLIED_UNATTESTED_TARGETS` and false isolation/promotion flags. A supplied response does not prove that any code ran, who produced the target, whether proposed tests passed, or whether the source was confined. The resulting tape can feed the separate numeric native replay adapter; it is not source-verified research or acceptance evidence.

The library supports the retained 4,096-bar dataset bound. Large CLI response inventories remain subject to the operating system's argument-length limits; the library can receive the complete bounded tuple/list directly. No broad directory or whole-dataset export mode is supplied.

## Verification and remaining work

Real API-built package fixtures and actual CLI subprocesses verify exact two-bar history, absence of broader package fields, source/SDK identity, chronological/future/instrument/price forgery refusal, strict indices, model-copy revalidation, bounded/duplicate-key JSON, exact output inventory, immutable outputs and missing/corrupt archive refusal. Preparation/assembly create no Python source files. Both wire schemas describe actual values and refuse authority; causality and original artifact binding also require the runtime validators. Regression and preservation evidence is recorded in BUILD_STATUS.

The fixed local Docker Linux endpoint was rechecked and is unavailable. A confined prefix-only worker, immutable harness/image pins, actual kernel enforcement and owned container lifecycle, proposed compilation/tests, runtime/source attestation, candidate-produced decisions, statistical validation/acceptance and all production release gates remain pending. No worker has run. A future launcher must stage only the single checked invocation and trusted harness, never the parent archive, full package or any future-bar dataset. Per-decision fresh isolation and separate no-holdout proposed-test execution still need implementation and live verification.
