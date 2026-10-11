# 0147: Run a pinned upstream local paper trace collector

Date: 2026-10-11. Development, PAPER only.

Add an upstream Collector Contrib 0.162.0 trace-only configuration and explicit
Windows amd64 release/archive/executable pin. Native release archive matches
GitHub's asset digest. Execute only the verified original binary; no package
installation, application dependency change, Docker operation or auto-start.

Bind only IPv4 loopback OTLP HTTP, 256 KiB body limit, bounded memory and rotating
32 MiB JSON trace files with three backups/seven-day retention. No remote exporter,
additional listener or payload debug logging. Local processes are the development
trust boundary; files are untrusted observations, not the original audit journal.

Actual SDK and paper application requests flow through the actual native
collector, retaining response trace ID, original parentage and replay distinction
without private request values. The file survives observed original termination
and a separate admission after terminal state. Collector outage does not disable
paper orders; original SDK unconfirmed outcomes remain unchanged on later startup.
An ACK/file read is not independent, tamper-proof, power-loss or complete delivery
attestation. File exporter upstream alpha and retention limits remain explicit.

The native size probe initially expected HTTP 413. Pinned receiver source shows
body-read errors return OTLP HTTP 400; final check requires that status and the
specific body-too-large error, never merely any parse failure. Initial failing
evidence is preserved. No existing application behavior/test was weakened.

Rollback stops only the explicitly owned collector and reverts configuration/
manifest/docs/tests while retaining existing files and original SDK outcomes.
Protected span search, Linux/service deployment, Grafana/alerts, metrics/logs,
independent attestation and full P00–P13 acceptance remain pending.
