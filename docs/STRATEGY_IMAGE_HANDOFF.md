# Static strategy worker archive

## State and evidence

Runtime update (ADR 0079, 2026-10-05): the original independently pinned archive
has now been loaded and inspected on the actual local Linux engine, and its worker
completed ten synthetic decisions through the existing owned execution/release
path. See `STRATEGY_ENGINE_HANDOFF.md` for reproduction and verification scope.
The archive identity artifact remains the original static assembly record, and all
authority fields remain false. The following paragraphs record ADR 0070's original
assembly evidence.

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

## Fixed-engine handoff (ADR 0071)

`python -m ai_quant_os.strategy_image_loading inspect` accepts the same archive,
base metadata and five independent pins as the static verification command above.
It verifies a single immutable archive snapshot, then reads the fixed local engine
and existing image. `load` performs the same checks and sends that exact snapshot
once with `image load --quiet --platform linux/amd64` before inspecting the image.
Neither command creates, starts or removes a container. The endpoint/environment
and bounded transport are shared with the existing public repair transport;
alternate remote endpoints and inherited Docker credentials are excluded.

Example read-only invocation:

```powershell
python -m ai_quant_os.strategy_image_loading inspect `
  --archive data-local/strategy-worker/oci-linux-cp311-01/image.oci.tar `
  --base-metadata infra/repair-worker `
  --worker-sha256 c94e1dc2a34c31a76bd42413b16dc4b906cec123e107393156d61ef0f3cc09ee `
  --base-manifest-digest sha256:31feac62e3aa66d2cdddb4e1f80f839d8f14ad02b98b77e7eee90df6dc65f0c5 `
  --archive-sha256 d233bc9c8a2093d18c4392a913db5aaf18d3d27ae5838af58d339653e268f375 `
  --manifest-digest sha256:1e0b8a64de1536ba581f7ffaead12574996f48e7385054c62f5a60826ef95e48 `
  --config-digest sha256:415c3433e8ec2d3ac89463032e6bcad71e25f58b03632f1fe516b02c100b5cf7
```

Engine admission requires Linux/cgroup v2, advertised memory/swap/PID/CPU limits
and built-in seccomp, a bounded engine identity, the containerd image store and
API 1.49 or later. Loaded-image inspection checks the manifest ID and descriptor,
exact descriptor byte size, linux/amd64 platform, all expanded layer hashes and the
archive's fixed runtime config. Only enumerated inert defaults are accepted for
extra legacy Docker config fields; executable hooks, volumes, unknown settings
and boolean/integer substitutions are refused. These metadata checks are
prerequisites, without proof of kernel enforcement or runtime behavior.

The typed receipt retains the verified archive, engine ID/API version and hashes
of inspected engine/image metadata. `IMAGE_LOAD_INSPECTED` means a load was
acknowledged and inspection matched; `IMAGE_PRESENT_INSPECTED` is read-only
presence evidence. Both retain false execution/promotion flags, and the nested
archive still has false image/execution/promotion authority. Receipt construction
or metadata hashes are not independent certificates. Cross-field model checks
must accompany the retained `strategy-image-handoff.schema.json`.

A timeout or failure after requesting load is uncertain: retain the archive,
inspect its exact pinned identity independently, and never retry or delete shared
images automatically. Load stdout is not identity evidence. CLI errors omit raw
daemon output and exception details. In this increment, the actual read-only probe
against the pinned archive exited 2 because the fixed engine was unavailable;
no load occurred. Simulated daemon tests do not establish live handoff compatibility.

Primary references: [platform-selective image load](https://docs.docker.com/reference/cli/docker/image/load/),
[image inspection](https://docs.docker.com/reference/cli/docker/image/inspect/),
[Engine API version history](https://docs.docker.com/reference/api/engine/version-history/)
and [Moby's containerd image inspection implementation](https://github.com/moby/moby/blob/master/daemon/containerd/image_inspect.go).
The selected containerd inspection path binds the requested platform's manifest
descriptor and exposes config/rootfs fields; actual engine compatibility remains
subject to the live inspection checks.
