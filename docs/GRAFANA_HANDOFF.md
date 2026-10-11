# Local native Grafana paper HTTP dashboard — increment 0153

The pinned Grafana OSS 13.2.3 Windows amd64 archive is retained under the ignored
local tool directory. Its checksum matches the official version-specific download
page. Every original regular member is retained and its name/length/hash inventory
is bound by `infra/observability/grafana-release.json`. This does not provide
independent signature, process or filesystem custody attestation.

`infra/observability/grafana/` provides the loopback configuration, fixed local
Prometheus datasource, file dashboard provider and Paper HTTP Observations board.
The board distinguishes latest accepted/failed scrape and observation age from
historical metric samples. Rate/failure/latency panels are gated on the current
fixed source's `up == 1`; absent/failed source is not replaced with zero values.
The board has no trading, broker, source repair, capital or trace-delivery control.
It is a paper HTTP dashboard, not the full platform observability requirement.

## Explicit private foreground admission

Use a new private per-user configuration/data directory and verify the complete
distribution against the pinned archive/tree before execution. Copy the provided
provisioning and dashboard directories there. Provide privately generated strong
`operator-admin-password` and `operator-secret-key` files in that directory;
the configuration requires both files, and native checks prove refusal when
either is missing. Create the private `data`, `logs` and `plugins` directories
before admission; native SQLite does not create a missing data-path parent. Do not commit
their values, put them in command arguments or reuse Grafana's default admin
credential. The configured local administrator is `paper-operator`.

From that private directory, configure these paths for the original foreground
process:

```powershell
$env:GF_PATHS_DATA = '<private-directory>\data'
$env:GF_PATHS_LOGS = '<private-directory>\logs'
$env:GF_PATHS_PLUGINS = '<private-directory>\plugins'
$env:GF_PATHS_PROVISIONING = '<private-directory>\provisioning'
$env:AI_QUANT_GRAFANA_DASHBOARDS = '<private-directory>\dashboards'
& '<verified-grafana-directory>\bin\grafana.exe' server `
  --homepath='<verified-grafana-directory>' `
  --config='<provided-configuration-directory>\grafana.ini'
```

The fixed listener is `127.0.0.1:3100`; Mission Control's existing port is separate.
Datasource access stays server-side through local Prometheus on port 9090; the
paper API operator token belongs only to the separately configured scraper.
Anonymous dashboard/datasource access, signup, embedding, analytics/update/news
checks, plugin preinstallation/admin/public-key downloads, Live and alert delivery
are disabled. Other bundled backend datasource plugins are disabled. Dashboard
and datasource provisioning are not editable through their normal UI flows.
Private file ACLs, account lifecycle, viewer roles, network/TLS deployment and
resource/retention constraints require separate deployment acceptance.

Stop only your original process/group, including its owned plugin workers. The
native acceptance fixture uses the repository's existing suspended-before-job-
assignment `OwnedProcess` lifetime boundary and captures job-member handles before
shutdown, then waits for actual terminal handle states. Do not discover/kill other
Grafana/plugin instances by process name or reset unknown data directories. Keep
the original database/logs and package bytes. No auto-service install, autostart or
browser preview is performed in this increment.

## Native verification scope

Native acceptance runs an actual owned paper HTTP server, original
order/replay requests, owned pinned Prometheus and owned pinned Grafana. It checks
missing-bootstrap refusal, package immutability, authentication, provisioned board
and datasource, applied private settings, hidden credentials, native datasource
proxy/query frames, native denial of provisioned-board saves, current rate
withholding after failed scrapes, historical samples and unchanged HALT/single-order
inventory. Original known job-member handles are observed terminal. Whole native
package identity is checked before and after the test group. Exact staged results
are recorded in evaluation 0153. The first cold package read was slow; it was
observed on its original live handle until terminal, without timeout replacement.

Native Grafana acceptance requires explicit `AI_QUANT_NATIVE_GRAFANA_HOME` and
`AI_QUANT_NATIVE_PROMETHEUS_DIR`; absence skips acceptance rather than proving it.
No browser rendering, visual/a11y or interaction acceptance is claimed. Prior
browser refusal and uncertain candidate/Docker custody are preserved. Other
platform dashboards, full trace/log/risk/queue views, justified alerts and linked
runbooks, services/Linux, private production credentials, load/custody/retention,
independent review and all full P00–P13 gates remain incomplete.

Sources reviewed 2026-10-11: [official pinned OSS download/checksum](https://grafana.com/grafana/download/13.2.3?edition=oss&platform=windows),
[pinned defaults](https://github.com/grafana/grafana/blob/v13.2.3/conf/defaults.ini),
[official provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/).
