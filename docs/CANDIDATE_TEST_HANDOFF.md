# Separate candidate compilation and proposed tests

This source-only job accepts an original validation package, revalidates its graph,
candidate and SDK bindings, and retains only six exact source files. Training and
holdout payloads never enter the mounted request. Proposed source and SDK code are
compiled/imported only inside the admitted Linux container.

## Use

Run from the repository checkout with the locked Python environment. Create a new
empty ordinary job root. The fixed dependency image must already be loaded using
the verified import procedure in REPAIR_IMAGE_LOADING_HANDOFF.md. No image pull,
automatic load, alternate engine or configurable relaxation is offered.

```powershell
data-local/locked-windows-0055/Scripts/python.exe -m ai_quant_os.candidate_test_cli --root data-local/my-candidate-tests prepare --validation-archive data-local/paper.validation-packages.sqlite3 --package-ref validation-package:THE_ORIGINAL_PACKAGE_HASH
```

Keep the returned `manifest_sha256` independently. Use that exact pin for:

```powershell
data-local/locked-windows-0055/Scripts/python.exe -m ai_quant_os.candidate_test_cli --root data-local/my-candidate-tests run --manifest-sha256 THE_RETAINED_MANIFEST_HASH
data-local/locked-windows-0055/Scripts/python.exe -m ai_quant_os.candidate_test_cli --root data-local/my-candidate-tests result --manifest-sha256 THE_RETAINED_MANIFEST_HASH
```

Preparation retains request, trusted worker and trusted guard bytes in `job/`;
all ownership metadata stays outside this mount. It binds native directory/file
identities, source hashes, package/candidate/request references and the immutable
image. A permanent kernel lock excludes concurrent cooperating writers.

The controller publishes/fsyncs a unique create intent before one create RPC and
a start intent before one bounded attached start. Created metadata must satisfy
the existing fixed confinement policy. The trusted worker checks Linux CPython
3.11.17, UID/GID, environment, readonly mounts, scratch inventory, cgroups,
resource limits, privilege flags and loopback-only networking before reading
proposed files. It compiles all six units and runs the exact proposed test file
with pytest 8.4.2, plugin autoload/cache disabled and bounded captured output.

The current renderer proposes six cases. Successful reports require six compiled
units, six collected/passed cases and zero failures/skips. Reports are bound to
the exact request, contain no source output, and remain
`PROPOSED_TESTS_PASSED_UNATTESTED`. Original candidate/package `NOT_RUN` fields are
historical capture metadata and are not rewritten.

## Interruption and evidence

Before publishing a result, the parent checks exact owned successful exit,
re-inspects immutable ownership, removes only that container with acknowledged
removal, and rechecks inputs. Result retrieval revalidates retained creation,
start, terminal, removal and worker evidence without a Docker action. A fresh
process can retrieve it.

An existing create intent prevents execution again. Unknown create/start/removal
outcomes retain inputs and records. For explicit owned cleanup:

```powershell
data-local/locked-windows-0055/Scripts/python.exe -m ai_quant_os.candidate_test_cli --root data-local/my-candidate-tests cleanup --manifest-sha256 THE_RETAINED_MANIFEST_HASH
```

Cleanup cannot prove absence after an unknown late create or missing removal
acknowledgement. It refuses those cases. Use a separately prepared new root after
investigating; never delete or overwrite retained intents to retry. Inputs are
retained; automatic release and journal repair are not implemented.

## Limits

This is local development evidence on a trusted parent filesystem/daemon. Proposed
source can influence its in-process pytest observations; these are not independent
behavioral/source attestation. The original image ID is
`sha256:5c86773e1c259ac6b89d6ff2bf8ceca386de0013b66c4e3b0cd1f1e91658e5b4`;
the original archive is unchanged. The metadata-only dependency probe retains its
original two-file runtime profile; the shared guard adds one fixed three-file
candidate-test profile with a pinned trusted guard hash.

`isolation_verified`, `execution_authorized` and `promotion_allowed` remain false.
Passing proposed tests does not establish alpha, independent physical holdout
isolation, statistical acceptance, agent mission completion or production/live
readiness. Installed-package distribution of the mounted harness is not provided;
the command requires this checked repository checkout and local image.

For real integration verification, use a new evidence directory with
`AI_QUANT_CANDIDATE_TEST_ENGINE_TEST=1`; the dependency probe has its separate
`AI_QUANT_DEPENDENCY_ENGINE_TEST=1` gate. See ADR 0081 and the retained evaluation
summary for observed results.
