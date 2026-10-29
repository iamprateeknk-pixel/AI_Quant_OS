# One-command local application startup

`ai-quant-local` runs the trusted installed paper API and the already built Mission
Control frontend under one foreground supervisor. It binds both to 127.0.0.1,
checks their readiness, monitors process exits and stops its owned processes on
normal shutdown or startup failure. It never discovers or stops unrelated services.

## Setup and use

From the checkout, install `.[dev]` into the virtual environment, run `npm ci` and
`npm run build` in `apps/web`, then:

```powershell
.\.venv\Scripts\ai-quant-local --state data-local/local-app
```

The module form is `python -m ai_quant_os.local_cli`. READY gives the API and
dashboard URLs (default 8000/3000). Ctrl+C or SIGTERM requests shutdown. A bounded
installed-command check is:

```powershell
.\.venv\Scripts\ai-quant-local --state data-local/local-smoke --api-port 8124 --web-port 3124 --run-seconds 1
```

`--run-seconds` accepts 0.1â€“60 seconds after readiness. `--checkout` identifies the
repository root containing `apps/web`; the API comes from the active Python installation. Keep those
versions aligned. `--state` retains `paper.sqlite3` and its sibling agent/health
archives. It does not migrate an existing differently named database. Back up the
complete directory and its SQLite/WAL/SHM files together.

The command uses the existing built UI. Rebuild after source changes. No package
installation or rebuild happens inside startup. Missing Node/dependencies/BUILD_ID,
invalid ports, occupied ports and frontend `.env` variants refuse before state
creation. Both assigned ports must be distinct. Port preflight does not reserve
the sockets; another service taking a port during startup causes refusal/exit.

## Environment and authority

Only a narrow system environment is copied to children. A privately configured
`AI_QUANT_OPERATOR_TOKEN` is passed to the backend alone; it is never generated,
printed, stored by the launcher or sent to the browser. Without it, write commands
return 503. Risk Center now supports expiring backend-owned operator sessions and
re-authenticated paper safety actions. The launcher sets the exact dashboard
origin automatically, including custom ports. See `OPERATOR_CONTROL_HANDOFF.md`.

Jev/OpenAI keys, Python/Node injection options, proxy settings and arbitrary
application settings are omitted. Jev/review, daily publication, Temporal recovery
and health polling are explicitly disabled. The existing API still restores stored
safety and quarantine pins; disabling polling cannot clear them. The fixed frontend
API URL points to the selected local backend. Next telemetry is disabled at startup.
Frontend `.env` variants are refused because Next would otherwise load them itself.
This relies on a trusted host, checkout, dependencies and build; it is not a browser,
credential or candidate-code security sandbox.

OpenTelemetry stays disabled by default. To have `ai-quant-local` start the pinned
local Windows Collector before the API and keep it available during bounded API
shutdown, explicitly set `AI_QUANT_OTEL_ENABLED=1` and
`AI_QUANT_OTEL_COLLECTOR_MANAGED=1`. The launcher verifies the Collector hash,
validates a generated loopback config and stores rotated traces and the generated
config under `<state>/observability`. It passes the trace-file path to the API for
the existing protected trace reader; Collector settings and credentials are not
forwarded to the frontend. `AI_QUANT_OTEL_COLLECTOR_MANAGED=1` requires telemetry
to be enabled. Leave the managed flag at `0` when an operator already runs the
explicitly configured local Collector separately.

## Ownership and readiness

- The local supervisor acquires a nonblocking kernel lock before importing/opening
  the application. It holds `.local-api-owner.lock` throughout its lifetime, using
  the shared root-ownership implementation. The existing repair namespace remains
  `.repair-owner.lock`. Files are never unlinked to infer/revoke ownership. Kernel
  release allows a successor after the supervisor stops. This outer lock coordinates
  local supervisors; the API lifespan lock below also covers direct Uvicorn starts.
  Trusted directory ancestry/local filesystem assumptions from the kernel lock
  remain. Other programs writing the same SQLite files must not run concurrently.
- The FastAPI lifespan also holds `.paper-api-owner.lock` for the canonical PAPER
  root. Direct Uvicorn launches and additional ASGI workers that reach application
  lifespan cannot open a second API owner for the same root. Kernel release after
  shutdown or process death permits the next owner. This protects cooperating API
  processes on one local filesystem; unrelated database writers and multi-host
  shared-storage leases are outside this lock protocol.
- Windows creates each child suspended, assigns its exact retained process handle
  to a private job with kill-on-close, verifies/resumes its unique initial thread,
  then permits startup. This closes the demonstrated spawn-before-assignment race.
  Normal shutdown or supervisor death closes the job and terminates its associated
  tree. Assignment/resume failure kills/reaps the owned child. POSIX creates a new
  process session/group and terminates that owned group during cleanup. A Windows
  supervisor crash between suspended process creation and job assignment can still
  leave an unassigned suspended child; atomic job-at-creation or durable allocation
  recovery remains open. This is not an all-phases crash-safe startup guarantee.
  POSIX
  SIGKILL/power-loss cleanup is not a service-manager guarantee; the API's parent
  pipe watcher cooperates on EOF, while a hard-killed supervisor may leave the UI.
- API readiness checks a per-launch random instance marker from a private launch
  wrapper route. Frontend readiness checks a bounded rendered monitoring page while
  its owned child is alive. It does not cryptographically authenticate frontend
  process/socket ownership against a hostile local process.
- Probes are loopback-only with redirects/proxies disabled. The existing bounded
  trusted-command transport runs each probe with a parent-enforced deadline and
  output cap, so slow header/body delivery cannot extend the total readiness wait.
  Readiness is limited to 30 seconds per service. It proves local responsiveness,
  not complete production dependency health or a release gate.
- Closing the API's parent pipe requests Uvicorn shutdown. Cleanup gives the
  direct child two seconds before terminating the owned tree, then waits/reaps.
  Interrupted in-flight work keeps the existing uncertainty/reconciliation rules;
  this is not a lossless drain guarantee. Child output is suppressed and failures
  are sanitized rather than printing headers, payloads or environment secrets.

Windows API decisions were checked against Microsoft's
[job object lifecycle](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
[thread entry layout](https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/ns-tlhelp32-threadentry32)
and [ResumeThread semantics](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-resumethread).
Installed Next 16.4.0 CLI documentation/help confirmed the built-server command and
explicit loopback hostname flag.

## Evidence and remaining work

The installed command reached READY with the real API and built UI on 8124/3124,
then reported STOPPED and reaped its processes after a bounded run. The actual
integration test verifies the rendered UI can reach the API, provider configuration
remains disabled despite inherited enabling settings, an unauthenticated control
write is refused, an authenticated paper safety change persists across complete
restart, and both ports are released. A trusted Node exit fixture verifies that
frontend startup failure also cleans up an already ready API.

A separate fresh virtual environment with only `.[dev]` and no system packages
also passed the 26 focused startup checks. Nautilus, Parquet and Temporal packages
were explicitly absent. This verifies the documented local startup closure, not
a full-suite result or locked/reproducible deployment in that environment.

Focused tests additionally cover invalid configuration, environment separation,
busy-port preservation, owned descendants, Windows supervisor death, suspended
assignment, premature child exit, slow-header deadlines, API EOF shutdown and
same-state competing API refusal. Existing repair lock tests verify its unchanged
namespace, restart/owner death and file-integrity constraints.

The Windows/Linux CI job builds the actual frontend and requires these integrations
with `AI_QUANT_REQUIRE_LOCAL_WEB=1`. Hosted run
[40836433785](https://github.com/iamprateeknk-pixel/AI_Quant_OS/actions/runs/40836433785)
passed all 17 jobs on code revision `0dfd881`, including the Linux and Windows
locked suites and local-application integration jobs. This verifies the checked-in
paper startup paths under CI; it does not certify production services, locked
Python deployment outside the tested profiles, global writer coordination,
staging, durable process/startup diagnostics, complete system health checks,
operator sessions or independent review. Docker/data
services and optional workers are separate explicit workflows. This command does
not complete the full execution pack or permit live capital.

Rollback: stop the launcher, retain its state directory, and use the documented
separate manual API/frontend commands with the same intended paper database. Do
not run manual and supervised API writers concurrently against that database.
