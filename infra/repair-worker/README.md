# Offline repair worker dependency inputs

This directory records a pinned CPython 3.11.17 Linux x86-64 base, wheel inputs
and a dependency-only build recipe. The original static OCI archive has now loaded
on the local Linux engine and passed the dependency-only runtime probe in ADR 0080:
51 distribution versions, core imports, trusted native pytest checks and selected
read-only/network behaviors were observed. Independent execution/isolation
certification and actual proposed tests remain pending. The earlier preparation
evidence below records its original scope; see
`docs/DEPENDENCY_RUNTIME_HANDOFF.md` for the current runtime check.

- `requirements.in`: eight direct versions matching the current verified host
  runtime and test tools. This profile covers core advisory A02/A07 imports and
  test tooling; optional data, Nautilus and Temporal extras are not included.
- `requirements.lock`: 51 exact target package versions, each with its selected
  Linux/universal wheel SHA-256. Install only offline with hash enforcement.
- `wheel_manifest.json`: all 52 downloaded archives, names/versions/bytes/tags,
  local installed versions and PyPI release references. Each archive matched its
  release's published size and SHA-256. One Windows-marker-only universal wheel
  (`colorama`) is retained in the download inventory and excluded from the Linux lock.

Wheel bytes are ignored local artifacts at
`data-local/repair-worker/wheels-linux-cp311`. A separate successful offline pip
download with `--no-index --no-deps --require-hashes` reproduced the 51 locked inputs
in `data-local/repair-worker/verified-offline-linux-cp311`. No wheel was installed
or imported during preparation. The inventory totals 16,942,265 bytes.

Dependency closure was checked from bounded wheel METADATA against Linux x86-64,
CPython 3.11.9 marker values and all requested root/version/extras constraints.
This is metadata consistency, not binary import/runtime compatibility. Transitive
versions were freshly resolved; they can differ from the Windows host versions
recorded in the inventory. These downloads do not change the application's
installed environment or satisfy the repository-wide dependency locking gate.

The official Python `3.11.17-slim-bookworm` tag was resolved through the public
Docker registry. `base_image.json` pins its selected Linux/amd64 manifest digest;
the retained index, manifest and config bytes reproduce their declared hashes,
platform selection, layer inventory and Python version. Base layers are now cached
and their compressed/expanded hashes verified; they have not been run. A matching manifest/config hash is not a security review or
signed provenance certificate. Dependency closure was also rechecked against
CPython 3.11.17 with the same 51 selected wheels; no target package was imported.

`Dockerfile` has two stages using that same immutable base. Dependency installation
uses only the locked local wheels, `--no-index`, `--no-deps`, `--require-hashes`,
`--only-binary=:all:` and `--no-compile`, with the installation step's network
disabled. Only installed packages are copied into the final stage. The inherited
base Python/pip/setuptools/wheel toolchain is pinned by the base image; it is not
included in the 51-package application lock. The recipe contains no application,
candidate or verifier source. Its default non-root user does not replace parent
confinement admission or kernel enforcement checks. Base retrieval may require
network access; this is an offline dependency installation recipe, not evidence
of a completed disconnected image build.

Prepare a new context from the reviewed inputs:

```powershell
.venv/Scripts/python.exe scripts/prepare_repair_image.py `
  --recipes infra/repair-worker `
  --wheels data-local/repair-worker/verified-offline-linux-cp311 `
  --output data-local/repair-worker/context-linux-cp311-02
```

The output must not already exist, and its parent must exist. The preparer reads
bounded ordinary single-link files, verifies base and wheel/lock bindings before
creating output, and copies exactly selected wheels plus the recipe/lock/ignore
file. A receipt records original input and copied-file hashes; it states
`BUILD_CONTEXT_ONLY` with all execution/promotion flags false. A failed copy keeps
its partial directory for inspection. No automatic deletion/reuse occurs. Treat
the recipe and source metadata as trusted operator inputs; this tool is not a
Dockerfile policy analyzer, signed attestation system or protection against a
hostile administrator mutating directory ancestry/output during preparation.

An actual context was prepared at the ignored local path
`data-local/repair-worker/context-linux-cp311-01`, with 51 wheels and no project
source or private files. The `.dockerignore` further excludes the receipt from
the build context. Inspect its copied bytes before a future local-only Linux/amd64
BuildKit build; no build/load/push is performed by the preparation command.

Two independent static assemblies produced the same local OCI archive with the
base layers and all 51 distributions. The assembler uses the pinned PyPA installer
as a data-copy tool, creates Linux launchers/RECORD paths and compiles/imports no
target source. It does not execute this Dockerfile. The ignored binary and tracked
`archive_manifest.json` are structurally verified artifacts, not runtime admission
or behavioral evidence. See [the archive handoff](../../docs/REPAIR_IMAGE_ARCHIVE_HANDOFF.md)
for the assembly command, exact hashes, OCI validation and limits. Build-tool wheel
hashes are in `build-tools.lock`; the tool is included in the development extra.
Generated paths now sort by POSIX names to avoid Windows case-folded ordering;
the current recorded archive is in `oci-linux-cp311-03`, with a matching fresh `04`
copy. Original `01`/`02` bytes remain retained. The readonly parent check requires
explicit independent pins and validates blob/base/rootfs/runtime metadata while
keeping all execution authority false. See
[the archive check handoff](../../docs/REPAIR_IMAGE_VERIFICATION_HANDOFF.md).

An explicit parent import command now verifies those immutable bytes, requires
the local containerd store/API 1.49, loads once through bounded stdin and inspects
the exact manifest ID with platform selection. It retains all false authority
flags and never starts a worker or migrates approved pins. Real import is pending
because this engine is unavailable. See
[the import handoff](../../docs/REPAIR_IMAGE_LOADING_HANDOFF.md).

Dockerfile-based builds, actual Linux pip installation/import/regression runs,
dependency/security/license review, native enforcement probes and an admitted
immutable worker image ID remain pending. The local Docker probe reports
`ENGINE_UNAVAILABLE`. The wheel manifest states
`image_verified=false`, `execution_verified=false`, `promotion_allowed=false`.
PyPI hashes establish matching published artifacts, not independent security
approval or signed behavioral evidence. Preserve the lock/inventory and wheel
bytes; these Linux wheels are laid out only in the ignored image staging root,
never in the Windows project import environment.

See [the image input handoff](../../docs/REPAIR_IMAGE_INPUTS_HANDOFF.md).

## Current local execution verification

The fixed Linux engine is now available. The original dependency archive was
loaded and its metadata-only runtime probe passed (ADR 0080). Separate
`candidate_test_worker.py` now compiles the original six candidate/test/SDK units
and runs the six proposed cases through the same immutable dependency image.
It shares a pinned guard with a fixed three-file role; the dependency probe's
two-file role and original image bytes remain unchanged. See
[the candidate test handoff](../../docs/CANDIDATE_TEST_HANDOFF.md).
Observed results grant no independent certification or promotion authority.
