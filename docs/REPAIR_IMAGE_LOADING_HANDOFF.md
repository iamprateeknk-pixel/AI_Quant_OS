# P10 pinned image import handoff

The trusted parent can import the dependency-only archive after verifying its
independent archive/manifest/config pins and server base profile. This command is
an intentional local image-store mutation. It is not wired into startup, API
admission, candidate execution or automatic recovery.

## Command

Use independently reviewed pins and the trusted server recipe directory:

```powershell
.venv/Scripts/python.exe -m ai_quant_os.repair_image_loading `
  data-local/repair-worker/oci-linux-cp311-03/image.oci.tar `
  --recipes infra/repair-worker `
  --archive-sha256 1bf8ec03c42a6a645cb33e347f04fbe5363d8cf0b0fb945bb3ed51ac230ecffe `
  --manifest-digest sha256:5c86773e1c259ac6b89d6ff2bf8ceca386de0013b66c4e3b0cd1f1e91658e5b4 `
  --config-digest sha256:c35bc16127ed4d5fa942414c9d3d13796709348baba4a8b9873aa7142e5776f1
```

This machine's actual invocation refused at engine metadata access; a fresh
readonly probe reports `ENGINE_UNAVAILABLE`. No real image load occurred. The
existing service/socket remediation remains untouched.

## Boundary

1. Read the ordinary single-link archive once into bounded immutable bytes, then
   run the existing byte verifier. No daemon command occurs for invalid pins,
   missing/unsafe files or invalid archive/base metadata. The file path is never
   passed to Docker; later replacement cannot replace loaded bytes.
2. Read Linux/cgroup/resource/seccomp prerequisites and require the containerd
   store advertised by `DriverStatus`. Require server API 1.49 or later in major
   version 1 before load. Missing, malformed or unsupported evidence refuses it.
3. Send the exact bytes through stdin to one `image load --quiet --platform
   linux/amd64` request. Read deadlines are ten seconds; load has sixty seconds.
   Each command shares a 64 KiB stdout/stderr cap and minimal environment with the
   fixed local endpoint. Ambient Docker context, remote host and credential
   variables are not inherited. No shell is used.
4. After zero-exit load, inspect the exact **manifest digest**, with Linux/amd64
   selection, and require returned `Id`, OS and architecture to match. Load
   output text is never identity evidence. Config digest remains a separate
   artifact pin, not this store's daemon image ID.
5. Only then return `IMAGE_LOAD_INSPECTED` and `loaded_image_id`. All
   `image_verified`, `execution_verified` and `promotion_allowed` flags remain
   false. No approved image/job pin is migrated and no container is created,
   started or removed.

Docker documents [stdin loading](https://docs.docker.com/reference/cli/docker/image/load/),
[platform inspection/API 1.49](https://docs.docker.com/reference/cli/docker/image/inspect/),
and the [containerd store check](https://docs.docker.com/engine/storage/containerd/).
Its current [containerd image inspector](https://github.com/moby/moby/blob/master/daemon/containerd/image_inspect.go)
sets `Id` from the selected manifest descriptor. A fixture first reproduced
refusal when config digest was assumed to be the store's ID; the importer now
uses the independent manifest pin. Actual Docker compatibility is unverified.

## Failure and remaining gates

Timeout, stream failure, nonzero load, unavailable inspection or mismatched ID
after sending load reports uncertainty. The archive remains; there is no retry,
image deletion, shared-store pruning or cleanup inference. Killing the owned CLI
does not cancel daemon work. Parent death and ambiguous completion need independent
exact-ID inspection; a durable import journal is not implemented.

The stdin writer runs alongside both output readers, closes input for EOF, refuses
incomplete delivery and joins after owned process termination. Input is immutable
and at most 512 MiB; trusted subprocess tests exercise binary preservation,
bidirectional pipe pressure, blocked-writer deadline/reaping and early stdin close.
Old no-input transport remains supported. Bounds are per command, not an overall
workflow deadline or a sandbox for descendants/candidate code.

Daemon/host/toolchain trust and archive verification limitations still apply.
Advertisements and identity inspection do not prove dependency/native correctness,
UID, readonly/network/resource/deadline enforcement, security/license/provenance
acceptance or candidate behavior. Runtime gates, independent certificates,
container pin integration, Temporal repair/shadow/canary/closure/rollback and
broader build requirements remain open.

Rollback disables/reverts this command. Preserve archives and ownership records;
images may be shared, so no automatic removal is specified.
