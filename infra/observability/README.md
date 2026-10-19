# Local paper trace collector

Development configuration for upstream OpenTelemetry Collector Contrib **0.162.0**.
The Windows amd64 archive and executable hashes are pinned in
`collector-release.json`. The archive matched the official GitHub release asset
digest; independent signature attestation and Linux verification remain pending.

## Run with the existing local binary

From the project root, in a dedicated PowerShell terminal:

```powershell
$collectorManifest = Get-Content infra/observability/collector-release.json -Raw | ConvertFrom-Json
$collectorBinary = Join-Path (Get-Location) 'data-local/tools/otelcol-contrib-0.162.0/otelcol-contrib.exe'
if ((Get-FileHash -LiteralPath $collectorBinary -Algorithm SHA256).Hash.ToLowerInvariant() -ne $collectorManifest.executable_sha256) { throw 'Collector executable hash differs' }
$collectorDirectory = Join-Path (Get-Location) 'data-local/observability'
New-Item -ItemType Directory -Path $collectorDirectory -Force | Out-Null
$env:AI_QUANT_COLLECTOR_TRACE_FILE = Join-Path $collectorDirectory 'paper-traces.jsonl'
& $collectorBinary validate --config=infra/observability/collector.json
if ($LASTEXITCODE -ne 0) { throw 'Collector configuration refused' }
& $collectorBinary --config=infra/observability/collector.json
```

Use a trusted upstream archive at the manifest's exact URL if the local binary is
absent. Verify its archive SHA-256 and the extracted executable SHA-256 against
the manifest before execution. This is a per-user development process, not a
Windows service or automatic app launch. No Docker operation is needed.

In the backend's launch environment, explicitly set `AI_QUANT_OTEL_ENABLED=1` and
`AI_QUANT_OTEL_ENDPOINT=http://127.0.0.1:4318/v1/traces`. Existing disabled defaults
remain intact. The collector must start separately; enabling SDK recording does
not prove collector availability.

## Limits and evidence

Only IPv4 loopback OTLP HTTP is enabled. The trace-only pipeline rejects request
bodies above 262144 bytes and uses a 128 MiB memory limiter with a 32 MiB spike
allowance. There is no gRPC, remote exporter, debug payload export, health listener
or metrics listener. Collector logs are warnings/errors; application metadata is
already selected by the SDK allowlist. The receiver trusts local processes and
has no authentication. Keep files within a private local workspace; arbitrary
local clients and raw file contents remain untrusted. This is not production
network authorization or an independently attested trace archive.

The upstream JSON file exporter rotates at 32 MiB, retaining at most three
backups with seven-day retention and UTC names. Rotation is enabled without
`append`, which upstream disallows with rotation. Native checks confirmed that
the observed file survives original process termination and a separately
admitted restart. Rotation may remove old records. Files are not an audit journal,
fsync/power-loss guarantee, tamper-proof ledger or complete delivery certificate.
The exporter is upstream alpha; do not infer production acceptance from these
development checks.

`tests/test_collector_deployment.py` requires `AI_QUANT_NATIVE_COLLECTOR_EXE` for
native acceptance, verifies its pinned bytes and version, validates the actual
configuration, and uses an original owned process on a fresh local test port.
It exports actual paper market/risk/order/replay requests through the collector,
checks retained SDK identity/parentage/privacy, rejects oversized input by the
specific native body-size error, and confirms that collector outage leaves paper
orders working while original SDK outcomes become unconfirmed. Both collector
handles are observed terminal; no unknown worker is killed or replaced.

Stop only the process you started. Retain its trace files. Never reset SDK
counters, clear uncertain custody or retry historical exports to turn an
unconfirmed outcome into delivery. Collector/Grafana service deployment, protected
span search, metrics/log correlation, independent acceptance and all P00–P13
release gates remain open.

Sources reviewed 2026-10-11: [release](https://github.com/open-telemetry/opentelemetry-collector-releases/releases/tag/v0.162.0),
[pinned file exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.162.0/exporter/fileexporter/README.md),
[pinned receiver](https://github.com/open-telemetry/opentelemetry-collector/blob/v0.162.0/receiver/otlpreceiver/README.md).
# Protected span observations added in 0148

Set `AI_QUANT_COLLECTOR_TRACE_FILE` to the same private local active trace file in
the backend's environment to admit operator reads. The SDK export enable flag is
independent: previously retained files can be inspected while current SDK export
is disabled. GET `/api/v1/observability/traces/{sdk_trace_id}` requires the existing
operator token/session before and after capture. The canonical nonzero 32-hex ID
comes from the paper response header or retained advisory binding; it is distinct
from an advisory run UUID or graph execution ID. No caller chooses a file path.

The selected response preserves original span/parent IDs and nanosecond strings
plus safe enums. It includes captured prefix hashes/lengths, never paths, raw
events/descriptions/errors or private input. RECORDED_SPANS and NOT_OBSERVED refer
only to retained prefixes; they do not certify complete history/delivery. Reads
are bounded, checked for original prefix rewrites and inventory changes, and
withhold results if any source is ambiguous/unavailable. File prefixes are not an
atomic multi-file journal or independently authentic. No reset/flush/retry is
exposed. The Traces console presents retained spans parent-first and orders
siblings by recorded start time; this parentage does not establish complete causal
history. See ADR 0148 and the saved-span console increment in `docs/BUILD_STATUS.md`.
