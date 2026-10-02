# Offline repair worker dependency inputs

This directory records wheel inputs for a future CPython 3.11 Linux x86-64 worker.
It does not contain a built/admitted image or an execution certificate.

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

Base-image/runtime pinning, a reviewed build context and image builder, actual
offline Linux installation/import/regression runs, dependency/security/license
review, native enforcement probes and an admitted immutable image ID remain
pending. The local Docker probe reports `ENGINE_UNAVAILABLE`. The manifest states
`image_verified=false`, `execution_verified=false`, `promotion_allowed=false`.
PyPI hashes establish matching published artifacts, not independent security
approval or signed behavioral evidence. Preserve the lock/inventory and wheel
bytes; do not install these Linux native wheels into the Windows project environment.
