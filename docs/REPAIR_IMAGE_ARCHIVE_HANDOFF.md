# P10 static dependency image archive

## Actual artifact

Two independent Windows-host assemblies with canonical POSIX member ordering produced identical 112,629,760-byte OCI
archives. The first is retained locally at
`data-local/repair-worker/oci-linux-cp311-03/image.oci.tar`; a second is in
`oci-linux-cp311-04`. `infra/repair-worker/archive_manifest.json` retains the input
pins and output digests without embedding the binary.

- Archive SHA-256: `1bf8ec03c42a6a645cb33e347f04fbe5363d8cf0b0fb945bb3ed51ac230ecffe`
- Manifest: `sha256:5c86773e1c259ac6b89d6ff2bf8ceca386de0013b66c4e3b0cd1f1e91658e5b4`
- Config: `sha256:c35bc16127ed4d5fa942414c9d3d13796709348baba4a8b9873aa7142e5776f1`
- Dependency layer: `sha256:1e26d74e8f35e9d89b0679bc719205ad25a9fd9b142c6333aaca01f0e212673f`

The earlier `oci-linux-cp311-01`/`02` artifacts remain retained with their original
hashes and ADR 0049 source. A real fixture reproduced Windows path sorting placing
lowercase names before uppercase names; the builder now sorts relative POSIX
strings explicitly. This changes dependency-layer/config/manifest/archive hashes,
not package contents. No admitted image or retained container pin was migrated.
Actual cross-host/OS reproduction remains unverified.

The four pinned official Python base blobs total 48,247,712 compressed bytes and
were fetched into `data-local/repair-worker/base-blobs`. Their declared byte sizes,
compressed digests and the config's expanded layer `diff_ids` matched. Base layers
were decompressed for hash verification without extraction or execution.

The fifth layer contains all 51 selected distributions, 15 generated Linux Python
launchers and 2,563 directory/file entries. Every generated tar entry has fixed
UID/GID 0, timestamp 0 and directory/launcher mode 0755 or other file mode 0644.
No project, candidate or verifier source, bytecode, private environment file or
credential is copied. Dependency Python/native code is stored as image bytes.
The image config declares user 65534:65534, `/scratch`, `/usr/bin/env`, the fixed
Python path and disabled bytecode writes. Those fields are not kernel confinement.

Independent artifact inspection validated layout, index, manifest and config
against the official OCI Image Specification 1.1.1 JSON schemas, all blob sizes
and hashes, every rootfs `diff_id`, Linux shebangs, distribution/entry counts and
absence of project source/bytecode in the generated layer. No image was loaded,
pushed or run. Structural validity does not prove runtime/native compatibility,
Docker import support, security acceptance or behavior. The artifact and labels
state `OCI_ARCHIVE_ONLY` with image/execution/promotion flags false.

## Build method

Install the development tools or the bounded hash-pinned tool wheel in
`infra/repair-worker/build-tools.lock`. The actual host tool `installer==0.7.0`
was downloaded as a wheel and matched its PyPI-published size and SHA-256 before
installation. It is a build tool, excluded from the 51 target dependencies.
The installer is the only new host package; Linux dependencies are laid out under
an ignored image staging root and never added to host import paths.

```powershell
.venv/Scripts/python.exe -m ai_quant_os.repair_image_archive `
  --recipes infra/repair-worker `
  --wheels data-local/repair-worker/verified-offline-linux-cp311 `
  --base-blobs data-local/repair-worker/base-blobs `
  --output data-local/repair-worker/oci-linux-cp311-05
```

The output must be new. The assembler first prepares the same verified allowlisted
context, rechecks metadata/wheel pins and base hashes, validates archive names,
member/size limits and internal RECORD hashes, and uses PyPA installer's data-copy
API with explicit Linux destinations/interpreter/launchers and no bytecode
optimization levels. Wheel entry point names are checked separately. It does not
execute Dockerfile instructions, installation hooks, target imports, generated
launchers, compilers, candidate source or a verification suite. The Dockerfile hash
is retained as an input reference; this archive is produced by the fixed static
assembler, not by that Dockerfile. Linux cross-scheme RECORD paths are normalized
to forward slashes after the host-independent launcher generation.

The assembler uses [PyPA's destination API](https://installer.pypa.io/en/stable/api/destinations/)
and the [OCI layout](https://github.com/opencontainers/image-spec/blob/v1.1.1/image-layout.md),
[manifest](https://github.com/opencontainers/image-spec/blob/v1.1.1/manifest.md) and
[config](https://github.com/opencontainers/image-spec/blob/v1.1.1/config.md) formats.
No daemon or network operation is present in the assembly path; base/tool downloads
are separate trusted preparation steps.

## Limits and unfinished gates

Existing ordinary input bounds remain. Base inventory: 1..16 gzip layers, each
compressed blob at most 64 MiB, aggregate compressed bytes at most 128 MiB and
expanded bytes at most 256 MiB per base layer. Wheels: at most 10,000 total entries,
256 MiB declared expanded content and 16 MiB per member, with validated RECORD
hashes. Installed file bytes are also bounded at 256 MiB. Unsafe original ZIP paths
are checked before Windows filename normalization; absolute/traversal/drive,
backslash, reserved-device, duplicate/case-collision, symlink and encrypted inputs
are refused. Partial output is retained; the final inventory receipt is written
after archive publication. Publication is not a power-loss transaction or signed
attestation. The cached roots, recipe, installer and host are trusted operator
inputs; hostile host administrators and directory/output races are outside this
build boundary. This data-copy tool is not an untrusted-source executor.

Use the read-only parent archive checker before a future handoff; it requires
independently supplied archive/manifest/config pins and rechecks the server base
profile. It does not execute a behavioral suite or approve an image. See
`REPAIR_IMAGE_VERIFICATION_HANDOFF.md` and ADR 0050.

Docker remains `ENGINE_UNAVAILABLE`, and service/socket remediation is untouched.
Linux runtime/import/regression evidence, Docker load/admission, actual UID and
kernel/resource/deadline probes, security/license/provenance review, complete
builder/toolchain attestation, cross-host/OS reproducibility, independently reviewed
verifier/runner and source-bound behavioral certificates are pending. The assembled
config digest is provisional and must not automatically replace an approved
worker pin. Temporal repair/shadow/canary/closure/rollback and the broader execution
pack remain unfinished. Rollback can disable the builder while preserving its
ignored artifacts and input manifests; it does not change quarantines or jobs.
