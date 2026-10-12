# ADR 0161: Mark the current primary navigation destination

Date: 2026-10-12

## Decision

Primary navigation derives its current destination from the pathname. Exact and nested destinations receive `aria-current="page"` and a restrained background with an inset accent. The market instrument routes share the Markets destination. Desktop and mobile navigation use separate Suspense boundaries because dynamic routes may suspend while resolving the pathname; their fallback keeps the full navigation available without claiming a current route.

## Limits

This is a route-state and accessibility improvement, not visual acceptance of the Mission Control reference. Browser review remains pending, and no P00-P13 phase gate is satisfied by this increment alone.
