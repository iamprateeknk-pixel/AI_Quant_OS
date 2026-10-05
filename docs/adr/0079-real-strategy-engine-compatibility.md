# ADR 0079: Real strategy engine compatibility

Date: 2026-10-05
Status: Accepted for local synthetic development verification

## Evidence and decision

Starting the stopped Docker Desktop restored the fixed local Linux endpoint.
The existing archive passed its original independent byte pins, engine admission,
load acknowledgement and loaded-image inspection. No replacement worker or image
was assembled.

The first real create stopped before worker start. Retained inspect metadata showed
`AttachStdout=true`, `AttachStderr=true`, disabled stdin/TTY, an empty `Cgroup`
selector and the exact protected kernel paths in a different order. The policy
incorrectly grouped output capture with interactive input and compared the path
inventory as an ordered list.

Admit strictly boolean output-capture flags, keep every interactive input flag false,
admit only the empty cgroup selector, and compare the complete kernel-path inventories
without ordering. Missing, extra, duplicate and non-string paths remain denied. The
create arguments, worker bytes, image pins, resource limits, source binding and
ownership/recovery contracts remain unchanged.

## Verification

Red focused checks reproduced the output-attachment and default-path mismatch before
each correction. Sixteen compatibility/rejection checks passed after the corrections;
six later checks covered interactive input and exact read-only path inventory.

The real integration completed all ten non-warmup decisions of the twelve-bar
synthetic package using unique admitted containers. It compared returned targets
with the declared direction rule, performed BASE and DECLARED_STRESS native replay,
verified removal and input release, and retrieved the same immutable report using
a fresh CLI process. The affected suite passed 103 tests in 147.19 seconds.
The frozen full repository regression passed 1,857 tests with 15 optional skips in
970.21 seconds, with the real-engine test enabled. A clean staged checkout passed
60 lifecycle tests with its separate engine gate skipped. All 17 earlier schemas
and unchanged worker/custody/execution/replay/release sources match HEAD. Lint,
format and staged secret scan passed. The architecture checker still lacks a
machine-readable layer declaration; manual review found no import or layer change.
See the retained summary at
`docs/evaluations/strategy-prefix-engine-0079.json`.

## Limits

This proves the observed local synthetic execution path. It does not establish
independent kernel isolation, malicious-source resistance, proposed test execution,
statistical validation, holdout infrastructure isolation, champion selection or
promotion acceptance. Existing execution/isolation/promotion authority fields stay
false. Other engine versions, failure modes and production deployment require their
own evidence. Failed run inputs and their acknowledged cleanup history are retained.

## Rollback

Revert the policy change and disable `AI_QUANT_STRATEGY_ENGINE_TEST`. The earlier
strict policy will refuse these Docker defaults before starting a worker. Do not
edit or delete prior run archives or custody journals; reconcile owned containers
using their original pinned cleanup commands. Shared Docker state is not rolled
back by the source change.
