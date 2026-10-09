# ADR 0107: Protected saved execution list and trace explorer

Status: accepted for local paper development, 2026-10-09.

## Decision

Add a protected metadata-only graph collection with fixed capacity/page bounds and
head-bound keyset pagination. Reuse the original graph/audit archive and trace
contract. Link the real Traces navigation to a server-rendered execution list and
saved timeline. Keep credentials in server mediation and project metadata before
rendering. Preserve explicit source, pagination, ownership and timing failure states.

## Reason

An archival trace API alone requires a previously known run ID. The frozen Mission
Control scope calls for usable traces: operators need to find a saved run, open its
original evidence and recover from missing or changing archives without executing
it again. Context freshness must not be conflated with transition timing.

## Consequences

One new public page schema; prior schemas/individual run routes remain unchanged.
No SQL migration, quota spending, source execution or new authority. Head changes
between pages invalidate pagination instead of silently mixing snapshots. Exact
microsecond arithmetic remains compatible with the existing compiler target.
Rollback preserves backend trace/collection readers and saved evidence. UI browser
acceptance, full causal/span navigation and export/retention remain pending.
