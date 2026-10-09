# Agent call capacity view

The Costs page now reads `/api/v1/agents/call-budget` through the existing
server-only operator session transport. Credentials stay in the HTTP-only cookie
and backend request. Fixed local origins, redirect refusal, no-store requests,
three-second timeouts and bounded response bodies retain the existing boundary.

The page shows all 18 roles in canonical order, current UTC daily/monthly periods,
reserved routine attempts, remaining routine capacity and protected recovery
reserves. Exhausted routine capacity is labeled explicitly. Policy and audit head
fingerprints are available in a disclosure. The view grants no write authority.

Counts describe reservations before graph tool effects and declared context fetches. Failed and interrupted
attempts remain counted. Versions of one role share capacity. Recovery spending
is disabled; legacy providers and earlier snapshot preparation remain outside this quota.
Token usage, monetary charges, compute and data costs are not measured here.

The frontend rejects incomplete or duplicate roles, invalid counters, unsupported
accounting claims, malformed provenance and impossible or inconsistent UTC dates.
It projects only observation fields and rejects inconsistent configured ceilings.
Disabled limits, unavailable/corrupt records and sign-in requirements have separate
messages, with no fabricated usage rows or zero-cost totals. Refresh reads current
records; automatic polling and retention management remain pending.

## Verification

A missing-parser failing check preceded implementation. Six feature tests cover
projection, counter/provenance validation and rendered enforced/error/disabled
states. The protected transport test verifies the exact endpoint and server-only
session header. All 35 frontend tests, production build, type checking and lint
passed. Evidence is retained in `evaluations/agent-call-capacity-ui-0099.json`.

Browser acceptance remains pending. The earlier local preview launch was rejected
by execution policy; this turn did not bypass it or use another launch path.
Static React markup checks do not establish responsive layout, keyboard interaction
or browser privacy acceptance.

## Remaining full-plan scope

Provider token and monetary accounting, routing economics, context-source budget
integration, authorized recovery spending, retention/migration, complete Costs
workflows and independent acceptance remain pending. P00-P13 remains incomplete.
