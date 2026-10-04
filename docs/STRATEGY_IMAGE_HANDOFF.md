# Static strategy worker archive

## State and evidence

The archive is prepared locally; Docker load/start and candidate execution remain
unverified. `infra/strategy-worker/archive_manifest.json` retains its identity as
`OCI_ARCHIVE_ONLY`, with every execution/promotion flag false. Read-only inspection
returns `ARCHIVE_HASHES_VERIFIED`; this establishes byte integrity only.

Two independent assemblies produced byte-identical 48,291,840-byte archives:

- `data-local/strategy-worker/oci-linux-cp311-01/image.oci.tar`
- `data-local/strategy-worker/oci-linux-cp311-02/image.oci.tar`

Archive SHA-256:
`d233bc9c8a2093d18c4392a913db5aaf18d3d27ae5838af58d339653e268f375`.
Manifest digest:
`sha256:1e0b8a64de1536ba581f7ffaead12574996f48e7385054c62f5a60826ef95e48`.
Config digest:
`sha256:415c3433e8ec2d3ac89463032e6bcad71e25f58b03632f1fe516b02c100b5cf7`.
The archive copies the exact committed ADR 0069 worker, without compiling or
importing it, and the four previously cached compressed CPython base layers.
Neither candidate source, SDK, test files nor datasets are included.

## Operator interface

Use the existing locked environment from the checkout root. Assembly requires a
new output directory under an existing ordinary parent:

```powershell
python -m ai_quant_os.strategy_image_cli assemble `
  --worker infra/strategy-worker/worker.py `
  --base-metadata infra/repair-worker `
  --base-blobs data-local/repair-worker/base-blobs `
  --output data-local/strategy-worker/another-new-output `
  --worker-sha256 c94e1dc2a34c31a76bd42413b16dc4b906cec123e107393156d61ef0f3cc09ee `
  --base-manifest-digest sha256:31feac62e3aa66d2cdddb4e1f80f839d8f14ad02b98b77e7eee90df6dc65f0c5

python -m ai_quant_os.strategy_image_cli verify `
  --archive data-local/strategy-worker/oci-linux-cp311-01/image.oci.tar `
  --base-metadata infra/repair-worker `
  --worker-sha256 c94e1dc2a34c31a76bd42413b16dc4b906cec123e107393156d61ef0f3cc09ee `
  --base-manifest-digest sha256:31feac62e3aa66d2cdddb4e1f80f839d8f14ad02b98b77e7eee90df6dc65f0c5 `
  --archive-sha256 d233bc9c8a2093d18c4392a913db5aaf18d3d27ae5838af58d339653e268f375 `
  --manifest-digest sha256:1e0b8a64de1536ba581f7ffaead12574996f48e7385054c62f5a60826ef95e48 `
  --config-digest sha256:415c3433e8ec2d3ac89463032e6bcad71e25f58b03632f1fe516b02c100b5cf7
```

Supply reviewed independent pins; never treat an archive's own receipt as approval
of a substituted archive. Hash identity is not a signature or independent security
review. Verification reads the archive and metadata without modifying them. No
command here invokes Docker, a registry, a provider or candidate code.

## Content policy

Inputs use the existing bounded, single-link ordinary-file reader. Metadata rejects
duplicate keys and non-finite constants. Base platform, Python version, manifest,
config, descriptors and compressed/expanded layer digests are checked. The supported
base runtime has only `Env` and `Cmd`, unique environment variable names, the pinned
Python version and `python3` command; history must match its layer inventory.

The appended layer contains exactly `opt`, `opt/strategy`, the mode-0444 worker and
`scratch`, with deterministic ownership/timestamps. Runtime config fixes UID/GID
65534, scratch working directory, an environment-clearing entrypoint and Python
`-I -B`. Complete config reconstruction compares canonical JSON bytes so booleans
cannot be replaced by integers. Archive inspection rejects substituted worker bytes,
changed modes/config/history, duplicate/unreferenced members, links and nonzero
trailing content. No tar content is extracted to host directories.

Bounds are 128 MiB per archive, 64 MiB per compressed base layer and 256 MiB total
expanded base bytes. These checks do not establish that arbitrary approved base
bytes are safe executable files; the pinned base still requires review and runtime
inspection. A failed write can retain a partial output directory; inspect it and use
a fresh output name. Existing output directories are never overwritten.

This is static OCI assembly, with no claim of Dockerfile/BuildKit byte equivalence.
The format follows the [OCI image layout](https://github.com/opencontainers/image-spec/blob/v1.1.1/image-layout.md),
[configuration](https://github.com/opencontainers/image-spec/blob/v1.1.1/config.md)
and [layer](https://github.com/opencontainers/image-spec/blob/v1.1.1/layer.md) specifications.

## Remaining execution boundary

The fixed Docker Linux endpoint was unavailable at the last check. No image has
been loaded or started. The next parent implementation must admit the actual
engine, loaded image and complete container metadata against independent pins;
enforce the worker's runtime controls; stage one exact request with host-path
fencing; own fresh per-decision create/start/deadline/kill/remove/recovery; and bound
and sanitize all untrusted output. Actual kernel enforcement, candidate behavior,
separate no-holdout compilation/tests, independent source attestation, statistical
validation, A10 acceptance and every production release gate remain open.

See `CANDIDATE_PREFIX_HANDOFF.md`, the worker README and ADRs 0068–0070.
