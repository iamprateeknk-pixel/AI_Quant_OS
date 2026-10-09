# ADR 0113: Persist observation scheduling intent before dispatch

Status: implemented library boundary; native and independent acceptance pending.

Continuous demo observation needs a durable due time and overlapping history
cursor. Restart must retain the original attempt across gateway and projection
commits; uncertain worker custody cannot be treated as a reconnect opportunity.

Use an explicitly enabled tick controller with a dedicated SQLite audit journal.
Pin schedule/connection policy, commit UUID/window before work, and advance the
cursor only after verified projection import. Recover already imported receipts
by commitment. Permit capped backoff only for completed transient observations
with confirmed child exit; halt on identity, integrity, custody or history gaps.

Consequences: separate databases have crash boundaries addressed by immutable
attempt replay, not a distributed transaction. Single-instance timer overlap is
serialized; multi-owner service leasing is not implemented. Retained health is
an observation, not a continuous connectivity attestation. A service timer and
operator recovery/composition remain to be built. No order or portfolio authority
is introduced; native demo testing and P13 gates remain pending.
