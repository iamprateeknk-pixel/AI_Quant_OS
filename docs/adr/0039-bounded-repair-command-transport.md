# 0039 — Bound parent-side repair command output while reading

Status: Accepted for command transport; container execution pending.

## Decision

Replace post-capture Docker metadata output checks with concurrent streaming binary
pipe drains under a combined byte cap. Validate limits before launch, use only an
explicit environment, kill/reap the owned CLI on error or deadline, and require
reader termination before returning.

## Consequences

Infinite stdout or stderr cannot grow retained parent output beyond the cap.
Trusted subprocess fixtures test boundary bytes and actual timeout cleanup. This
does not isolate target code or terminate Docker containers; future execution
must separately own container creation, admission, termination and job cleanup.
Live confinement and behavioral certificates remain unverified. See
`../REPAIR_TRANSPORT_HANDOFF.md` for verification scope and limitations.
