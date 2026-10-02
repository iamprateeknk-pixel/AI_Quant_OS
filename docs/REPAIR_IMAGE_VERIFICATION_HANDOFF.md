# P10 read-only parent archive check

`repair_image_verification` reads the exact OCI producer's plain tar artifact and
verifies independently supplied archive, manifest and config hashes plus the
server's pinned official Python base profile. It never launches a process, invokes
Docker, writes a file, extracts image files to disk, imports image packages or
executes candidate code/a behavioral verification suite. It is an artifact
transport/inventory check before future image handoff.

## Run

Use reviewed pins from independent trusted configuration, not hashes learned from
an untrusted received archive. The current recorded artifact can be checked with:

```powershell
.venv/Scripts/python.exe -m ai_quant_os.repair_image_verification `
  data-local/repair-worker/oci-linux-cp311-03/image.oci.tar `
  --recipes infra/repair-worker `
  --archive-sha256 1bf8ec03c42a6a645cb33e347f04fbe5363d8cf0b0fb945bb3ed51ac230ecffe `
  --manifest-digest sha256:5c86773e1c259ac6b89d6ff2bf8ceca386de0013b66c4e3b0cd1f1e91658e5b4 `
  --config-digest sha256:c35bc16127ed4d5fa942414c9d3d13796709348baba4a8b9873aa7142e5776f1
```

The actual command passed on the 112,629,760-byte five-layer archive. It emits
`ARCHIVE_HASHES_VERIFIED`, `archive_integrity_verified=true`, and
`image_verified=false`, `execution_verified=false`, `promotion_allowed=false`.
The config digest remains provisional; this summary must not automatically set a
worker pin or clear an incident/quarantine. Local config/receipt hashes are not
signatures, independent security acceptance or behavioral certificates.

## Checks and bounds

- Whole archive: bounded regular single-link file, no reparse final file, at most
  512 MiB; identity/size checked while reading, SHA matched before parsing.
- Container: plain tar only, at most 32 ordinary file entries, no links, duplicate
  paths or PAX metadata; only `oci-layout`, `index.json` and lowercase SHA-256 blob
  paths. Member bytes are bounded at 256 MiB; nonzero trailing bytes are refused.
  Image files are inspected in memory and never extracted to a host path.
- JSON: objects, at most 1 MiB each, with duplicate fields refused; exact layout
  version, one Linux/amd64 descriptor, typed manifest/index versions and explicit
  manifest/config hash bindings. This is the fixed producer's narrow format check,
  not a general OCI loader or replacement for the official schema inspection.
- Blobs: all names/hash agreement, descriptor type/size, complete referenced
  inventory with no extra orphan blob; pinned base layer descriptors and retained
  base/config metadata; 1..16 base layers plus one uncompressed dependency layer.
- Rootfs: exact base `diff_ids`, all compressed/expanded layer hashes, expansion
  at most 256 MiB per gzip base layer.
- Runtime: Linux/amd64, user 65534:65534, fixed interpreter/entrypoint/Cmd/workdir,
  unchanged base environment except disabled bytecode writes, and archive-only
  labels with execution/promotion false. Recomputed test pins for root user,
  shell entrypoint, changed environment, promotion=true or wrong `diff_id` remain
  rejected. These are metadata checks, not kernel probes.

The caller's pins, server recipe roots, Python and host are trusted. A hostile
host administrator, directory ancestry races, independent package/security review,
total process memory/wall/resource enforcement and signed toolchain attestation
are outside this readonly boundary. Pins supplied by the same untrusted party as
the archive provide no independent authenticity. No Docker availability or
runtime/native correctness is inferred from passing.

## Current evidence and next gates

A real Windows fixture first failed canonical order because Windows path sorting
folded case. The assembler now sorts relative POSIX strings. Two new Windows-host
archives matched exactly; independent OCI 1.1.1 layout/index/manifest/config schema
inspection passed, with 2,563 generated entries in canonical order. Original
archives and their Git-recorded hashes remain retained. Cross-host/OS equality
still needs actual execution evidence.

Docker is unavailable; service/socket remediation remains untouched. Linux
imports/regressions, Docker load and immutable ID admission, UID/kernel/network/
resource/deadline probes, security/license/provenance review, independently reviewed
runner/behavioral suite, source-bound certificates, Temporal repair/shadow/canary/
closure/rollback and the broader execution-pack release gates are unfinished.
