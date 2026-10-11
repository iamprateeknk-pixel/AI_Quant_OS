# 0148: Read selected local collector spans through operator authorization

Date: 2026-10-11. Development, PAPER only.

Expose `/api/v1/observability/traces/{trace_id}` for a canonical nonzero SDK trace
ID. Pre/post operator authorization and no-store match existing observability
routes. The configured local collector file is server-owned configuration, never
a request path. No configuration means DISABLED; bad/missing/ambiguous source
means UNAVAILABLE without inferred spans or file prefixes. A remote UNC setting
is refused at read time and cannot prevent the original app/HALT from starting.

Scan the active file and at most three timestamp-named rotations. Bound directory
inventory to 1024 entries, files to 32 MiB each, individual lines to 1 MiB, selected
spans to 128 and checked scan work to a 2.5-second monotonic deadline. This bounds
work at checkpoints, not kernel I/O or production latency. Require regular file
leaves and complete JSONL records; reject duplicate JSON keys/nonfinite input.
Capture original byte length/hash, reread the original prefix, and verify original
file identity/size and final inventory. Append beyond the captured prefix is
allowed; rewrite/truncation/replacement/rotation during observation withdraws it.
Prefixes are separate local observations, not an atomic multi-file audit snapshot.

Select only exact paper resource/instrumentation identity and allowed SDK names,
IDs, nanosecond strings, enums, route templates and UUID attributes. Raw payload,
events, status descriptions, paths, environment values and errors never render.
Reject duplicate span IDs, self/cyclic parentage and invalid ordering. Preserve a
missing parent ID without creating a parent or claiming a complete trace.

RECORDED_SPANS identifies selected records; NOT_OBSERVED refers only to scanned
retained prefixes. delivery_verified, independently_attested and complete_trace
are always false. Local files can be forged/edited by local users and retention
can delete records. No linkage to an original audit commitment, complete export
history, exporter health, ongoing permission or independent authenticity is
inferred. No write/reset/flush/retry or trading authority is introduced.

Native integration uses the actual pinned Collector Contrib and actual paper
application order flow. Its protected selected span IDs equal retained original
SDK IDs and keep parentage/privacy; authorization, revocation, append/rewrite,
retention, input bounds, missing states and actual response schema are checked.
An attempted lookahead path regex was rejected by installed Pydantic's Rust regex;
canonical 32-hex syntax plus explicit pre-read nonzero guard replaces it.

Rollback reverts additive models/source/route/wiring/schema only. Retain collector
files, SDK bindings and audit heads. Console span rendering/search, indexing,
full causal links, Linux/service/Grafana/alerts, independent/browser acceptance and
all P00–P13 release gates remain pending.
