# Linux dependency runtime probe

The original 112,629,760-byte repair dependency archive was loaded through its
independently pinned import command and executed in a fresh owned local Linux
container. All 51 installed distribution versions matched the Linux lock. Core
FastAPI, Pydantic/pydantic-core, Uvicorn, HTTPX, LangGraph/checkpoint SQLite, pytest
and Hypothesis imports succeeded. Two trusted tests exercised native Pydantic and
SQLite. This is a prerequisite for the separate candidate compilation/test stage;
the probe accepts neither candidate files nor datasets.

## Repeat the check

Load the exact archive using the command and pins in
`REPAIR_IMAGE_LOADING_HANDOFF.md`. The image must already be available locally;
the integration never pulls or substitutes it.

Choose a new evidence directory. Pytest can clear an existing base directory, so
never reuse a retained run's path.

```powershell
$env:AI_QUANT_DEPENDENCY_ENGINE_TEST = '1'
data-local/locked-windows-0055/Scripts/python.exe -m pytest -q tests/test_repair_dependency_runtime.py --basetemp=data-local/dependency-runtime-another-new-run
Remove-Item Env:AI_QUANT_DEPENDENCY_ENGINE_TEST
```

Without the explicit gate, the real-engine case skips. Unit checks still cover the
strict metadata-only request, duplicate/oversized input, changed distribution
inventory, environment refusal and non-Linux host refusal.

## Execution and retained evidence

The integration writes/fsyncs a unique create intent before Docker create and a
start intent before attached start. It uses the existing fixed local transport and
repair create recipe: no pull, network/IPC disabled, private cgroups, UID/GID 65534,
no capabilities, no new privileges, read-only root/job, bounded scratch, resource
limits, disabled logs/health/restart and one exact job mount. Created metadata is
admitted with the existing common confinement predicate. Only the trusted probe
and a bounded distribution-name/version request are mounted.

Before package imports, the probe independently observes the fixed Linux/Python,
identity, environment, job/scratch inventory, privilege/filter flags, cgroup limits,
process limits and mount options. It allows only CPython's optional exact
`LC_CTYPE=C.UTF-8` addition. Writes to `/tmp` and `/job` must fail with `EROFS`, and
a connection to the reserved documentation address must fail for lack of a route.
The trusted tests run in scratch with plugin autoload/cache disabled and bounded
captured output. No proposed source or proposed test is accepted or executed.

The parent checks the exact response, successful exited state and exact immutable
container identity. Cleanup re-inspects owner/name/image before removing only that
container and retains the removal acknowledgement. Evidence remains under
`data-local/dependency-runtime-0080-verified/test_actual_linux_dependencies0`;
the tracked summary is `docs/evaluations/dependency-runtime-0080.json`.

This test harness is not a production candidate executor or restart recovery
service. Parent death/ambiguous create/start/removal requires manual investigation
using the retained intent and exact owned identity. Do not reuse a partial run or
prune shared images/containers. Independent isolation/source attestation, full
native-library compatibility, memory/PID/deadline adversarial probes, dependency
security/license acceptance and actual separate proposed compilation/tests remain
open. Existing archive authority fields stay false.
