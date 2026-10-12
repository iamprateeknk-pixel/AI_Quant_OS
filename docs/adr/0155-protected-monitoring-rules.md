# 0155 — Expose selected current local rule observations through operator policy

## Decision

Add an optional protected read-only source at the HTTP/infrastructure layer;
keep deterministic domain modules unchanged. Reuse existing operator token/
session authorization before and after observation. Fix source to admitted
loopback native Prometheus, selecting only two production rules with coherent
states, original UTC timestamps and checked-out runbook paths. Disabled or
unavailable data cannot infer inactive rules or substitute a zero inventory.
No operator credentials, redirects/proxies or arbitrary raw payloads leave this
boundary. Existing local launcher forwards only an explicit backend enable flag.

## Evidence and limits

Initial17 tests failed on missing implementation. The native source then matched
actual pending evaluation through the protected API. A new negative test found
firing was accepted before120 seconds; its red report is retained and the
original hold is now enforced. A launcher test failed on its missing flag before
explicit backend-only0/1 forwarding. Final native/bounds/session/schema/launcher
and existing controls plus exact wheel identity are recorded in evaluation0155.
Current rule selection is a local trusted OS observation, not independently
attested source health, durable incident custody or notification/control proof.
Front-end incident wiring, durable acknowledgement/engine health/delivery,
production/independent/browser and full P00–P13 acceptance remain open.

## Rollback

Revert additive source/route/schema/flag/docs/tests. No dependency, database,
order-state migration, journal reset, collector deletion, candidate execution,
service installation or broker/provider operation is needed.
