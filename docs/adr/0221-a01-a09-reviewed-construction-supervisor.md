# ADR 0221: A01 A09 reviewed-construction supervisor

Date: 2026-10-20

## Status

Accepted for the local PAPER/SANDBOX application.

## Decision

Add A01 0.9.0 as an optional supervisor for A09 0.2.0 construction. Admit it
only through a typed operator route carrying an already active
`hypothesis-review:<sha256>` reference. Bind only that reference into the
parent task declaration. At dispatch, read the parent declaration again and
compile A09's context against the same active reference through the existing
builder compiler and `CANDIDATE_CREATE` permission check.

The parent catalog, wake plan, and receipt are version-bound to A09 0.2.0.
Existing A01 0.6.0–0.8.0 cohorts and A09's direct operator review remain
unchanged. A09's result is an inert candidate; construction grants no execution,
promotion, or PAPER order authority.

## Consequences

The typed input denies extra fields, and the generic PAPER context route refuses
A01 0.9.0. An inactive review cannot produce a context. Restart reuses the
persisted parent receipt. This is application-level SANDBOX routing, not
independent process or storage isolation, and does not complete P07 acceptance.

Rollback removes A01 0.9.0 registration, route, dispatch contract, and schemas
as a unit. Retained A08/A09 history remains readable.
