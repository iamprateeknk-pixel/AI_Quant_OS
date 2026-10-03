# ADR 0054: Supervise the local paper API and monitoring UI together

Status: Accepted for local development startup; production gates open.

## Context

P01 still required separate manual API/frontend startup. A usable local system
needs coordinated readiness, persistent paper state, refusal of conflicting
owners and cleanup on partial startup or termination.

## Decision

Add `ai-quant-local` for the trusted installed API and already built Next frontend.
Bind loopback only, separate child environments, keep provider/background features
disabled, and require bounded readiness before announcing READY. The API acquires
a kernel state-root lease before application import, reusing the existing lock
implementation with a separate namespace and preserving the repair public wrapper.

Windows children start suspended, join a kill-on-close job by exact process handle,
then resume their validated initial thread. POSIX uses a new process group. Normal
cleanup cooperates with the API's parent-pipe EOF shutdown before tree termination.
Probe subprocesses reuse the existing bounded trusted-command transport; redirects
and proxy configuration are disabled.

## Consequences

One foreground command can serve the existing real UI/API and preserve paper state
across restart. Invalid preflight and partial startup cannot announce success;
owned cleanup does not stop unrelated services. Tokens remain server-only and are
not printed or persisted by the launcher.

This is a local trusted-service lifecycle, not a repair execution sandbox or a
production service manager. POSIX hard-death cleanup, manual writer coordination,
staging, full dependency health, reproducible deployment, complete operator auth
and independent review remain open. See `docs/LOCAL_APPLICATION_HANDOFF.md` for
commands, exact ownership limits, evidence and rollback.
