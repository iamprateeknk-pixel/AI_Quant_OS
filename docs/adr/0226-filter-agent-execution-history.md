# ADR 0226: Filter archived execution history by agent version

## Status

Implemented as a read-only extension to the protected graph-run archive page.

## Decision

Allow the archive page endpoint to accept `agent_id` and `agent_version` only as a
validated pair. Preserve the archive integrity head as the pagination snapshot,
and scope totals, cursors, and rows to the selected pair. The browser verifies the
same identity on every returned row. Agent Registry contract details link to this
filtered Traces view; historical summaries remain metadata-only and operator
protected.

## Limits

The filter reads existing audit-verified records and makes no database or wire
schema change. It does not establish current permissions, runtime health, quality,
calibration, or promotion readiness. No execution or control action is exposed.

## Rollback

Revert the endpoint filter, registry link, Traces query handling, and this ADR/status
entry. Existing graph records and audit events remain unchanged; no migration or
data repair is required.
