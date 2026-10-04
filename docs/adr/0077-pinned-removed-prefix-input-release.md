# ADR 0077: Pinned archive before removed-prefix input release

## Status

Implemented explicit archival and exact deletion/recovery control. Tests use
simulated daemon observations and disposable owned files; real worker execution
and independent container-removal attestation remain unverified.

## Decision

Keep the original staging, journal, lifecycle, execution and replay implementations
unchanged. Add a separate operator path for a successfully completed, audited
removed prefix. First restore its original package/job/plan under the old independent
pins, capture the exact eight request/sidecar files with their identities and content,
reconstruct the original source/SDK/history request and complete create/start recipe,
then exclusively publish and fsync a bounded recovery bundle. Preparation deletes
nothing. The caller retains its independently obtained SHA-256 before release.

Release requires that bundle's original package, opaque job name, same full native
parent identity chain and exact current audited REMOVED record. Admit every surviving
file by original inode/device/size/time/link count/mode and exact bytes. Refuse unknown
job files/sidecars, replaced files, changed roots/history and missing inputs without
an exact prior release attempt. Hold the permanent cooperative root lock throughout.

Before destructive work, re-fsync the archive and publish/fsync a release-attempt
record bound to its independent hash. Delete only the known request, its empty
directory and seven known sidecars; never recurse, delete neighbouring jobs, remove
the ownership journal/lock, or perform daemon/image cleanup. Retain recovery bytes,
attempt and deterministic final observation. Resume partial deletion only under the
same independently pinned bundle and current removal history. Re-fsync existing
markers so a prior fsync failure cannot be silently treated as durable success.

## Limits

This is explicit input custody release, not automatic full-prefix orchestration or
an execution/isolation/promotion certificate. The archive retains all original
inputs/receipt metadata and confers no source-behavior or physical holdout-isolation
proof. The old journal remains local metadata rather than independently immutable
storage; its constructor can initialize an empty journal if missing, after which
release refuses because removal history is absent.

Filesystem/SQLite publication and deletion span operations. Partial state is retained;
directory-entry power-loss durability and a malicious host namespace are not
certified. Windows read-only mode is not a hardened ACL; only the expected request
mode change during an attempted Windows unlink is tolerated on exact retry.
Each archive is bounded to 2 MiB; archives, audit owners and independent pins remain
retained and require separate capacity/retention governance. Actual kernel execution,
separate proposed compilation/tests, full prefix orchestration, source attestation,
statistical acceptance, promotion and all production gates remain open.
