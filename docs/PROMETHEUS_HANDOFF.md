# Local native Prometheus — increment 0152

`infra/observability/prometheus-release.json` pins official Prometheus 3.15.0
Windows amd64 archive and both native executable hashes. The downloaded archive
matched the GitHub release API's digest and size. This is upstream development
source verification, not independent signature or production attestation.

`infra/observability/prometheus.json` is native YAML-compatible JSON configuration
for one fixed local paper HTTP metrics target. It reads `X-Operator-Token` from an
operator-owned `operator-token` file beside the copied configuration; the token
is absent from the configuration. Default scrapes occur every 15 seconds with a
three-second timeout, no redirects/ambient proxies/HTTP2, 1 MB body bound, 4096
sample bound and seven-label bound. Native Prometheus includes its own metric
name/job/instance labels when applying the bound. Histogram bucket labels also
count. No remote write, dynamic discovery, alert delivery or extra target exists.

## Explicit per-user foreground admission

Prepare a new private per-user monitoring directory outside tracked source. Copy
the configuration and its referenced `paper-http-alerts.json` there and provide
its `operator-token` file privately using the
existing backend operator token. The backend must have optional tracing enabled
and still bind only to loopback. Do not commit the file, put token bytes in command
arguments, or send them to the UI. File ACLs and credential rotation are the
operator's separately reviewed deployment responsibility. The API's backend
authorization remains authoritative and a wrong token produces a failed scrape.

Verify the chosen native executables against the pinned manifest first. From the
private configuration directory, validate the actual file with the pinned
`promtool.exe check config .\prometheus.json` before starting the pinned server:

```powershell
& '<verified-private-tool-directory>\prometheus.exe' `
  --config.file=.\prometheus.json `
  --storage.tsdb.path=.\tsdb `
  --storage.tsdb.retention.time=3d `
  --storage.tsdb.retention.size=128MB `
  --web.listen-address=127.0.0.1:9090 `
  --log.level=warn
```

Use only your original foreground process. Never kill an unknown listener or
reuse uncertain data directories. Retain its TSDB/logs on stop; no automatic
service install, autostart, reset, retry of orders or lifecycle/admin APIs are
enabled. The local query UI is for trusted OS users and is unauthenticated; it is
not a production network boundary. No browser launch or visual acceptance was
performed. A separate Grafana process and authenticated network/service deployment
remain pending. Retention is a configuration bound, not measured disk/memory or
power-loss acceptance, and the active head/WAL can use additional space.

## Interpreting observed metrics

`up{job="paper_http"}` describes the latest scrape, not trading health or trace
delivery. Use its current state together with the target's last error and source
age before treating telemetry as current. A failed scrape must not turn missing
measurements into zero errors/latency. Retained range queries can still contain
older samples; they do not prove a healthy current source. Counter/rate/latency
queries in PAPER_HTTP_METRICS_HANDOFF.md apply to this admitted source. Metrics
remain volatile at the app; TSDB retention does not certify an audit ledger or
complete causal lineage. Collector trace files are a separate observation source.

## Local incident rule evaluation — increment 0154

The configuration evaluates `paper-http-alerts.json` every 15 seconds. Sustained
failed scrapes and missing source observations have separate warning rules with
two-minute holds. Native rule tests cover exact hold timing, short failures,
recovery, unrelated jobs and stale markers; an actual revoked scrape also
produces a healthy pending rule in the original native server. These are local
observation alerts, without Alertmanager/notification or automatic control.
Annotations refer to [the checked-out runbook](runbooks/paper-http-monitoring.md)
by repository path; no served runbook URL is assumed. See its detection/engine
failure/acknowledgement limits before interpreting an inactive alert as recovery.

## Evidence and remaining work

Native `promtool` accepts actual original SDK exposition and copied configuration.
An owned native Prometheus process scraped an actual owned Uvicorn paper API,
queried original order/replay count and `+Inf` bucket, withheld the credential
from status/config, refused lifecycle reload, observed 401/down after changing
the fixture credential, retained original history through a range query, and
preserved HALT. Both original handles were observed terminal. A prior five-label
configuration failed with the native seven-label error; evidence is retained.
Native test admission requires explicit `AI_QUANT_NATIVE_PROMETHEUS_DIR`; absent
tools skip acceptance and do not prove it. Evaluation 0152 uses the pinned tools
without skips. Browser refusal/unknown candidate Docker custody remain intact.

Grafana dashboard provisioning/actual ingestion display, justified alerts and
linked runbooks, private service credential lifecycle, Linux/service deployment,
load/retention/latency/custody, independent review and all P00–P13 gates remain open.

Sources reviewed 2026-10-11: [pinned release](https://github.com/prometheus/prometheus/releases/tag/v3.15.0),
[pinned configuration reference](https://github.com/prometheus/prometheus/blob/v3.15.0/docs/configuration/configuration.md),
[official native promtool reference](https://prometheus.io/docs/prometheus/latest/command-line/promtool/).
