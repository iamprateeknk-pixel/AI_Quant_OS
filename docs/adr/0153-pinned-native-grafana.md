# 0153 — Provision original native Grafana paper HTTP observations

## Decision

Pin the official Grafana OSS 13.2.3 Windows amd64 archive and complete original
regular-file inventory. Provide authenticated loopback configuration, private
file bootstrap, fixed server-side Prometheus source and immutable file dashboard.
Request/error/failure/p95 rate panels use original metrics and withhold current
samples when the fixed source's scrape is down; separate status/age panels expose
that condition. No zero fallback, trading control or full-platform claim exists.

Use the existing trusted `OwnedProcess` boundary. The native capture wrapper
inherits the Windows job assigned before the initial thread resumes; native
Grafana and plugin workers join that exact job. Capture and verify live job-member
handles before closing it, wait for their actual terminal state and retain private
data/logs. Do not discover or kill other instances. Native package tree identity
is checked before/after the test group; it is not independent source/process
custody or a candidate execution sandbox.

## Evidence and limits

Explicit native admission initially failed on missing pins. The first native
integration exited before readiness; private startup capture showed a missing
data-directory parent. Creating the new fixture's private directories corrected
it. Missing admin-password/secret-key provider checks validate actual errors,
not an unrelated nonzero exit. Native authenticated provisioned board/datasource,
settings/privacy, original order/replay data through Grafana query frames,
rejected scrape/history/HALT and exact owned process cleanup are checked.
The native administrator cannot save the provisioned board. A current query
uses a refreshed end timestamp after observing the failed scrape and accepts
Grafana's empty-field frame as missing data, while rejecting numeric zero.
Earlier assertions requested a historical timestamp or assumed an empty frame
contained a value column; the original failed runs are retained.
Cold initial whole-package hashing was observed to completion, not restarted.
The first staged422-test run passed; its style gate caught an imported fixture
name shadowed by a test argument. Resolve the existing fixture through pytest
request under a distinct local name; keep that original report.
All failed original fixtures/logs remain preserved. Evaluation 0153 records exact
final staged verification. No browser launch/visual/a11y claim is made.

This is the first local HTTP observation board. Other required platform/queue/
trace/log/risk dashboards, independently justified alerts/runbooks, private roles/
credential/network/service/Linux/resource/load/retention/custody deployment,
independent review and all full P00–P13 release gates remain incomplete.

## Rollback

Revert additive pins/config/provisioning/board/tests/runbook. Close only the exact
original admitted process/job including its owned plugins; retain its data/logs.
No app migration, dependency/source change, collector deletion, journal/queue
reset, order retry or automatic service installation is needed.
