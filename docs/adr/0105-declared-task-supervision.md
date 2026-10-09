# ADR 0105: Declared task cohorts for A01

Status: accepted for bounded paper development, 2026-10-09.

## Decision

Publish A01 0.4.0 with a separate, protected typed task declaration route. Four
server-owned task kinds select fixed existing specialist versions. Store the
declaration as context evidence, bind it to task/world/time and verify the output
against original evidence. Retain NOT_REQUESTED catalog entries for transparency.
Reuse existing child dispatch, permission/source/deadline checks, durable quota
admission and receipt identity. Preserve prior immutable supervisor versions.

## Reason

The frozen execution pack asks for only needed agents to wake. A fixed full
cohort unnecessarily fetches unrelated sources and consumes their role capacity.
An explicit narrow task kind provides reviewable routing without inventing an
uncalibrated autonomous task classifier or granting caller-defined capabilities.

## Consequences

Separate request/output schemas and one additional immutable manifest version.
Same task ID with a different kind produces different context and child receipts.
No database migration or paid provider calls. Rollback uses the compatible prior
supervisor endpoints while preserving existing saved contexts and receipts.
Older code cannot execute the new version; preserve a compatible reader for it.
Full task registry, scheduler, adaptive routing and release certification remain
open. The fixed mappings require a new reviewed version for semantic changes.
