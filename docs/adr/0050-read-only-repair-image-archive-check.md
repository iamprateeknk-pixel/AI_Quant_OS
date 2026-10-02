# ADR 0050: Read-only image archive check and POSIX ordering

Date: 2026-10-02
Status: Accepted for artifact integrity; runtime acceptance pending

## Decision

Make archive verification a trusted-parent readonly module/CLI requiring explicit
independent archive, manifest and config pins and the pinned server base metadata.
Bound file/container/JSON/gzip reads; verify only the owned producer's fixed OCI
layout, blob inventory, base/rootfs hashes and runtime metadata. Return integrity
evidence while leaving all image/execution/promotion authority false.

Sort generated layer paths by relative POSIX strings, rather than host Path
comparison. A Windows fixture reproduced a case-folded order different from POSIX;
the fix changes image hashes and preserves the prior archives/history.

## Consequences

The parent can detect transport tampering/missing/extra members and incorrect
runtime metadata without Docker or image/candidate execution. Caller-supplied pins
must be independently trusted; self-provided hashes confer no authenticity. The
checker is not a generic OCI loader, signed attestation, behavior certificate or
kernel enforcement proof. Host/directory races and toolchain acceptance remain
outside this boundary. Cross-host reproduction and actual image runtime gates
remain pending. Revert/disable this checker to roll back; existing job ownership,
quarantines, certificates and approved image pins are not changed.
