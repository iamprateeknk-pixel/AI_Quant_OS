# Repair sandbox admission

## Current local evidence (2026-10-14)

Docker Desktop is running on the fixed Linux endpoint. The pinned image returns
`PREREQUISITES_PRESENT`; live dependency and fixed adversarial probes passed, and
the repair create/admission/removal path passed on a synthetic source fixture.
See `docs/evaluations/repair-runtime-live-0160.json`. Candidate/verifier execution,
independent behavioral verification, durable execution recovery and promotion
remain disabled.

## Delivered

`DockerConfinement` is a fail-closed **prerequisite and created-container metadata
checker** for the pending independent repair verifier. It neither creates a
container nor starts candidate code. `ai-quant-repair sandbox-status` reads the
fixed local Docker endpoint and an explicitly pinned, already-local Linux image.
There is no registry pull, remote Docker context, broker access, health-journal
mutation, candidate release or quarantine recovery.

The engine must advertise Linux, cgroup v2, memory/swap/process/CPU enforcement
and its built-in seccomp profile. The created-container checker additionally
requires the exact image, verifier command and ownership token; UID/GID 65534;
read-only root and one exact read-only job mount; no network or shared host
IPC/PID/UTS/cgroup namespace; no capabilities, extra devices or privilege
escalation; disabled container logging; 512 MiB memory with no additional swap;
one CPU and 64 processes; CPU/file/descriptor/process ulimits; and a 64 MiB
scratch tmpfs with `noexec,nosuid,nodev`. The verifier command clears its
environment and uses scratch-local home/temp paths. These controls follow
[Docker's container controls](https://docs.docker.com/reference/cli/docker/container/run/)
and [resource constraints](https://docs.docker.com/engine/containers/resource_constraints/).

Engine advertisements and metadata are **not evidence that a live candidate ran
under those controls**. An available probe means only image/engine prerequisites
are present. The suite source pin is a declaration until independently checked
against the actual trusted verifier program; output always includes
`suite_verification_required=true`, `container_admission_required=true` and
`promotion_allowed=false`. The full execution controller, bounded output and
wall-clock enforcement, direct in-container kernel probes, safe job snapshot
construction, owned cleanup, behavioral worker and independent certificates are
not implemented in this increment.

## Inspect prerequisites

```powershell
.venv/Scripts/ai-quant-repair.exe --repository D:/AI_Quant_OS sandbox-status --image-id <local-sha256-image-id> --suite-sha256 <owned-verifier-program-sha256>
```

Use an actual trusted local image ID and suite source hash. Tags and malformed
pins are rejected. The image is never downloaded. This command does not require
a health journal or open one if provided. Existing prepare/inspect commands still
require their actual archived journal.

Current local evidence: the installed command returned `ENGINE_UNAVAILABLE`.
Docker Desktop startup failed while renaming the stale
`sailor-ingest.sock` reparse point. Graceful shutdown also reached its deadline
with newly started Desktop/backend processes still present. Automatic approval
review rejected the combined attempt to stop those processes and remove that
socket, reporting only "blocked by policy". No successful socket cleanup or
container execution is claimed. Do not treat the unavailable runtime as a
reason to run proposed repair Python directly on the host.

## Verification and remaining work

The tests use hand-declared metadata to exercise the admission policy: missing
kernel capabilities, absent seccomp, weakened memory/swap/CPU/process/file
controls, changed image/owner/command/user, extra writable mounts and host
namespaces are denied. An actual read-only local probe and installed CLI check
confirm unavailable/missing-image results cannot grant readiness or promotion.
These tests do not prove live Linux isolation or candidate behavioral correctness.

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_repair_sandbox.py tests/test_repair_cli.py tests/test_repair_workspace.py
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests scripts
.venv/Scripts/python.exe -m ruff format --check src tests scripts
```

There are no database migrations, candidate receipt changes, worker activation
flags or changed capital-control behavior. Rollback removes this read-only
diagnostic/admission integration; retained candidates and health incidents are
unchanged. Docker image/suite pins require independent owner review. Windows
mount translations are constrained to known representations of the exact job
path but remain unverified against a live Desktop engine. Rootless endpoints and
non-v2 cgroups are not admitted by this initial fixed policy.

Next: restore a usable local isolated runtime, prepare an owned pinned offline
toolchain and verifier program, run the actual behavioral worker, validate live
kernel confinement and timeout/output/cleanup failure paths, then retain candidate
source/version-bound verification evidence. Shadow/canary, critical/agent
incident resolution, Temporal repair stages and gated recovery/rollback remain
required. Quote incident acknowledgement is available separately through the
watchdog.

Parent-side Docker reads now use a streaming combined-output cap with command
termination/reaping instead of collecting unbounded output before checking size.
See `REPAIR_TRANSPORT_HANDOFF.md`; this does not terminate a container or provide
live sandbox evidence.
