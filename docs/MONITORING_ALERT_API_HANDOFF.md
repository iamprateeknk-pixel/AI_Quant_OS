# Protected local monitoring rule observations — increment0155

GET `/api/v1/observability/alerts` returns schema1.0 PAPER observations with
DISABLED, UNAVAILABLE or AVAILABLE state. AVAILABLE contains the two original
selected rules, their INACTIVE/PENDING/FIRING state, original evaluation/active
UTC times,120-second hold, warning severity and fixed checked-out runbook path.
No alert ID, durable journal, acknowledgement, notification delivery, source
attestation or trading/recovery authority is inferred. Inactive is an observed
rule state, not proof of healthy orders or safe risk resumption.

## Admission and authorization

Explicitly set `AI_QUANT_PROMETHEUS_ALERTS_ENABLED=1` for the admitted backend.
Default0 performs no source I/O. The existing local launcher forwards only this
explicit0/1 flag to the backend, never to the frontend. The fixed native source
is IPv4 loopback127.0.0.1:9090 `/api/v1/rules?type=alert`; there is no request URL,
remote host, query, credential or proxy override. Trusted in-process fixture
injection can select a fresh loopback port. No browser-to-Prometheus connection
or operator credential forwarding exists.

The existing operator token/session policy checks access before reading and
again after observation. Responses are no-store. Expired or revoked sessions
cannot receive a completed observation even if they were authorized at start.
The optional source has no lifespan worker or persistent connection. Each read
owns/closes its native HTTP client/stream; cancellation propagates. The existing
Risk Kernel, order replay/idempotency and safety routes remain independent.

## Bounds, selection and freshness

At most four reads enter source I/O at a time. A2.5-second cooperative total
budget includes waiting for that slot and streaming; native client I/O timeout
is two seconds. At most262144 uncompressed bytes and16 groups are accepted.
Redirects, ambient proxies, compression, ambiguous JSON keys and non-finite JSON
are refused. There are no retries. These are cooperative bounds, not independently
measured production CPU/memory/latency guarantees.

Exactly one paper-http-monitoring group and the two configured original rule
names/expressions/holds/labels/annotations must match. Rule health must be ok
without a raw error. Active rule/alert states must agree; one fixed source is
supported, with original loopback target labels for failed scrapes. Private or
foreign labels/annotations do not become client fields. Selected evaluation
age must be between0 and45 seconds (three production evaluation intervals);
future/stale readings withdraw the whole source. Firing cannot precede the
original120-second hold. UTC times are selected from native original data.

UNAVAILABLE has no rules and no substituted healthy/zero inventory. Original
error bodies, source configuration, URLs/headers and arbitrary payloads are not
returned or logged by this adapter. Local source identity relies on trusted OS
admission, not independent process/network attestation. Prometheus itself can
stop without delivering an alert; this endpoint then reports unavailable.

## Evidence and remaining work

Native tests read the original pending rule from a real admitted Prometheus
process after an actual authorized source scrape is revoked; protected API state
matches and original order inventory/HALT remain unaffected. Tests also cover
no source read before authorization, disabled state, current pending/firing,
changed/failed/stale/future/ambiguous rules, transport bounds/no retry, actual
async deadline/concurrency/cancellation, session expiry after read, launcher
privacy and exported schema. Native package/wheel identity is recorded in0155.

The incident frontend still needs this source wiring. Durable incident journal,
acknowledgement/recovery policy, external Alertmanager/notifications and engine
health coverage remain pending. No browser launch or production/service/Linux/
private-role/resource/load/custody/independent acceptance is claimed. All full
P00–P13 gates remain incomplete. See the paper monitoring runbook for semantics.
