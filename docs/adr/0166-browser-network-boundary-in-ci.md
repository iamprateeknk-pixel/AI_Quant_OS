# ADR 0166: Enforce the browser network boundary in CI

Date: 2026-10-12

## Decision

The web regression suite includes an AST check over every client entry point and its local import graph. It rejects known broker SDK packages and transport targets that are not fixed same-origin `/api/` routes. The web test suite is now an explicit required CI step between lint and production build.

## Limits

This source check catches direct imports and statically visible network targets; it is not a proof against obfuscated code or arbitrary runtime code generation. Browser runtime tests and the remaining P12/P00-P13 acceptance gates are still required.
