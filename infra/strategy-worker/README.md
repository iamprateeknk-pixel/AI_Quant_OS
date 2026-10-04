# Single-decision strategy worker

Status: trusted source and image definition prepared; image build, Linux execution,
parent admission/lifecycle and candidate behavior remain unverified.

## Execution boundary

The standalone worker uses only Python's standard library. Its image contains the
trusted harness and the already pinned CPython 3.11.17 base, without candidate,
SDK, proposed test or dataset files. The build context excludes every file except
the Dockerfile, worker and ignore rules. No image was built or loaded in this increment.

A future trusted parent must independently verify the image/harness identity and
actual engine/container metadata before start. It must stage exactly one canonical
`CandidateInvocation` JSON file as a read-only `/job/request.json` and mount no
parent archive, complete package, training/full-holdout file or proposed test.
Exact binding to the original package is a parent responsibility; standalone wire
consistency is not original-source trust. One new container and empty scratch
space are required for each non-warmup decision.

The image's fixed command clears the environment and invokes `/opt/strategy/worker.py`
with Python `-I -B`. The harness checks its runtime before reading the request or
materializing/importing candidate source:

- Linux, CPython 3.11.17, isolated/no-bytecode flags, UID/GID 65534 and no foreign
  supplementary groups; fixed working directory, worker path and environment.
- No-new-privileges, seccomp mode 2, and zero inherited/permitted/effective/bounding/
  ambient capabilities as reported by `/proc/self/status`.
- Observed cgroup v2 memory 512 MiB, swap zero, 64 PIDs and one-CPU quota; exact
  CPU/file/open-descriptor/process resource limits.
- Read-only root and job mounts; a 64 MiB writable tmpfs scratch with
  noexec/nosuid/nodev; empty scratch and exactly one request file in the job.
- Only a loopback network interface.

These are defense-in-depth prerequisite checks. They do not independently prove
Docker admission, namespace isolation, kernel enforcement, artifact identity,
host-filesystem fencing or a behavioral certificate. The worker's mount collector
checks root/job/scratch; the parent must inspect the complete Docker mount and
namespace configuration to refuse extra host mounts, sockets or shared namespaces.

Primary references for the controls: [Docker resource limits](https://docs.docker.com/engine/containers/resource_constraints/),
[Docker container controls](https://docs.docker.com/engine/containers/run/),
[Python isolated/no-bytecode invocation](https://docs.python.org/3.11/using/cmdline.html),
[Linux cgroup v2 interfaces](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html),
[process privilege/filter fields](https://man7.org/linux/man-pages/man5/proc_pid_status.5.html)
and [mount information fields](https://man7.org/linux/man-pages/man5/proc_pid_mountinfo.5.html).

## Input and output

Input is at most 512 KiB and must retain the exact complete canonical parent wire
bytes. Duplicate keys, non-finite JSON constants, unknown fields, authority claims,
invalid source/SDK hashes, wrong inventory and future/nonchronological history are
refused. The two UTC OHLCV bars reproduce the parent's Decimal-28 history hash.
Decimal wire fields must be strings of at most 4,096 characters. The request is
opened without following symlinks and must be a single-link bounded regular file.

Only after runtime and wire checks does the worker create a fresh private scratch
runtime, materialize the four retained SDK files and exact candidate bytes, import
`candidate.HypothesisDirection`, and call `target_position` once on those two bars.
No training, current-open price or later bar is supplied. Warmup remains parent-owned
flat output; the worker accepts indices 2 through 4,095 only.

The output binds the SHA-256 of the exact invocation wire and contains only a strict
integer target -1, 0 or 1 plus the existing `UNATTESTED_TARGET_OUTPUT` status and false
isolation/promotion flags. Self-reported output is not independent source execution
or test attestation. The parent must apply its bounded response decoder and original
invocation binding; extra console output makes the response invalid. On caught failures,
the harness itself emits `PREFIX_WORKER_REFUSED` and exit 2 without echoing source,
request or exception details. Candidate console output remains untrusted; the parent
must sanitize diagnostics and must not publish or retain that output verbatim as
trusted evidence.
Candidate exits, resource kills or process failures may terminate without a valid
response; the parent must treat those as failed execution, never successful validation.

## Verified scope and remaining work

Tests execute the trusted decoder, runtime-metadata guard and output encoder, and
launch the trusted worker only to prove host-runtime refusal. Candidate text is
never imported, compiled, executed or written as Python on the host. A candidate
string containing a host-side file write remains inert during decoding. Synthetic
matching runtime metadata tests only the validator; no candidate call is reached.

Source/SDK/history forgery, malformed input, weakened privileges/resource/mount/
network controls, dirty scratch, extra job files, malformed profiles and non-unit
targets are refused. Parent API-built request bytes and returned response schema
compatibility are checked. Full/cold regression evidence belongs in BUILD_STATUS.

The fixed Docker Linux endpoint remains unavailable. Immutable built image/harness
attestation, actual Linux/native enforcement probes, owned parent create/admit/start/
deadline/kill/remove/recovery, source staging fencing, proposed compilation/tests in
a separate no-holdout job, actual candidate behavior and source attestation remain
pending. Do not supply these preparation/tests as executed or source-verified
research, A10 validation, alpha acceptance or promotion evidence.
