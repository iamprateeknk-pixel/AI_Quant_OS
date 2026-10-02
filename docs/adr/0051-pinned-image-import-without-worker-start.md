# ADR 0051: Pinned image import without worker start

Date: 2026-10-02
Status: Accepted for parent transport; actual daemon import/runtime pending

## Decision

Verify the once-read immutable archive before any daemon command, require the
fixed local Linux engine's sandbox prerequisites/containerd store/API 1.49, send
only those bytes through bounded stdin, and inspect the independent manifest pin
with explicit platform selection. Keep config and manifest identities distinct.
Never derive approval from load output or migrate approved worker pins.

Extend the existing transport with a bounded immutable input writer alongside both
output readers. Refuse incomplete delivery, retain command deadline/output bounds
and terminate/reap the owned child before joining its pipe threads.

## Consequences

The parent can import once without opening a raced archive path in Docker or
starting target code. Post-load failures are uncertain and never cause retries,
shared-image removal or weaker admission. No durable import journal, daemon
cancellation proof or actual Docker compatibility is claimed. Success retains
false image/execution/promotion authority. Runtime isolation and behavioral
certification remain pending. Disable/revert the command to roll back while
preserving archives, existing jobs and approved pins.
