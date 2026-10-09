# Demo sync health API and Brokers panel — increment 0115

## Backend and local composition

The owner-protected GET `/api/v1/brokers/demo-sync/health` reads only `health.json`
from a server-configured absolute state directory. Configure
`AI_QUANT_DEMO_SYNC_STATE`, or pass `demo_sync_state_dir` when creating the app.
No client parameter selects a path. The normal operator token/session boundary
runs before file access, including revoked-session rejection. Responses are
no-store and typed as `demo-sync-health-api.schema.json`.

The API reports whether a directory is configured and the existing bounded
AVAILABLE/STALE/UNAVAILABLE health view. Its source is explicitly
LOCAL_UNVERIFIED_HEARTBEAT. A missing, malformed or future record cannot become a
healthy broker observation. Invalid local data is withheld without exposing its
content, path or diagnostic text. GET grants no order/portfolio authority; there
is no service start/stop, native capture, enrollment or credential endpoint.

The local launcher supplies `<local state>/demo-sync` only to the backend and
excludes the demo password from both child environments. To connect an explicitly
started foreground demo service to that local application, use that directory
for the service's `--state-dir`. This directory assignment does not start a demo
service or import the native SDK.

## Mission Control

Brokers now fetches the existing broker registry and protected demo health in
parallel. The fixed server mediator keeps the operator session in its HttpOnly
server cookie and forwards it only to the existing validated local backend. Its
fetch has no-store, a fixed timeout, bounded body and redirect refusal. Browser
props receive only public metadata; the panel parser projects explicit fields.

The panel distinguishes sign-in, unconfigured, unavailable, fresh/stale,
running/stopped/disabled and halted observations. It shows the last sync state,
fixed failure reason, original UTC publication/success/history/due times and
consecutive failed cycles. Pending UUID/window and service run/tick metadata are
available under an accessible details control. A halted/stopped service does not
show its old due time as a scheduled observation.

The parser validates denied authority at every layer, explicit unverified native
attestation, UUIDs, limits, UTC calendar/time precision, exact thirty-second
publication windows and complete pending intent. It rechecks age at server render
time. Private extra fields are discarded. Historical detail remains visible when
stale, with a warning that current process state is unknown. This is a fetched
snapshot with explicit refresh; it is not a live stream or process attestation.

## Verification and limits

Backend checks cover auth before file access, normal owner reads, revoked sessions,
fixed path, no-store, method restriction, missing/stale/malformed records, exact
schema export and launcher environment separation. Existing demo stack, owner
session and local environment checks are included in the focused checkout run.
Frontend checks cover parser bounds/precision/authority, metadata-only rendering,
missing/sign-in/stale/halted states and server session mediation. Production
compilation/type checking and scoped frontend lint also run.

The earlier browser launch was blocked by policy. No alternate launcher was used;
layout, focus, keyboard, theme and responsive browser acceptance remain pending.
No actual SDK, terminal, real enrollment/password or broker was used. Local
heartbeat file authenticity remains unverified. Native demo acceptance, service
manager deployment, explicit halted recovery, full lifecycle/history reconciliation,
order idempotency/routing, alternatives, independent review and P13 live gates
remain pending. This increment does not complete P04 or P12.

Rollback retains the service/controller/gateway/projection journals and custody
records. Existing public schema bytes and service behavior stay unchanged; the
API schema and frontend metadata projection are additive.
