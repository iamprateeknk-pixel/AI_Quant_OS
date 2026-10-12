# Browser network boundary handoff

## Change

`apps/web/tests/browser-network-boundary.test.mjs` walks client entry modules and local imports, then rejects known broker SDK imports and network transports whose target is not a same-origin `/api/` route. Dynamic URL helper parameters pass only when every call site supplies a fixed local API path. `npm test` runs the complete Node regression suite, and CI now invokes it before the production build.

## Verification

- `npm test`: 151 passed.
- TypeScript `--noEmit`: passed.
- `npm run lint`: zero errors; one existing unused-variable warning in `tests/realtime.test.mjs`.
- `npm run build`: passed.
- `git diff --check` and staged secret scan: passed.

## Limits

The scanner is static and cannot prove behavior of obfuscated code, arbitrary code generation, or every possible SDK package name. The browser still requires runtime and P12 accessibility/visual acceptance. No broker, live capital, provider or external service was used.

## Rollback

Revert the scanner test, `npm test` script, and CI step together. No runtime data or migration is involved.
