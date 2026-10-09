# Evolution genome archive view

## Implemented

`/evolution` reads the configured, protected agent genome archive through the existing
server session. It displays actual saved agent/version identities, baseline or
unverified challenger state, parent references, evaluation objectives and source
fingerprints. Source contents, prompts and operator nonces are not rendered.

Configure `AI_QUANT_EVOLUTION_WORKSPACE` to the workspace used by the evolution CLI
(normally `data-local/evolution`). The archive must already exist. Missing or disabled
archives are unavailable, not an empty healthy catalog. A valid empty archive displays
zero saved genomes. No archive is seeded through this view.

The protected `GET /api/v1/evolution/genomes/page` supports limits 1–10 and offsets
0–1000. Continued pages require the original audit head. A changed head returns 409;
archive corruption returns a sanitized 503. All retained genome identities, canonical
bytes, audit bindings and parent policies are checked inside the page transaction,
including records outside the selected page. The existing archive remains immutable.

Metadata pages fit the 131072-byte server transport limit. An exact objective decimal
longer than 64 characters is omitted with `details_truncated=true`; it is never rounded
or silently shortened. The UI directs the reader to the original retained objective.
The archive and original genome fingerprints are unchanged.

## Verification and limitation

Failing endpoint and parser checks preceded implementation. A further failing check
exposed an oversized decimal preview and was fixed without weakening the assertion.
All 94 scoped archive, CLI, session, health and research tests passed in both the
workspace and exact staged checkout. All 27 frontend checks passed, including the
private session transport. Workspace production build/type checks and lint passed.
The fresh checkout also passed a separate type check and lint; its production build
failed because the dependency junction points outside the bundler filesystem root.
No fresh-checkout build success is claimed. Machine-readable evidence is retained
in `evaluations/evolution-genome-ui-0096.json`.

The local web preview launch was rejected by the execution policy with “blocked by
policy.” No browser verification success is claimed. The test-owned API was stopped.
Eleven synthetic archival fixtures remain in `data-local/evolution-genomes-0096`;
their proposed source was parsed as archival data and never executed. The browser
probe is prepared but remains unrun, including keyboard, mobile, paging and logout
checks. Visual acceptance is pending.

## Full-plan work still open

This view does not complete Evolution or P11/P12. Strategy genome history, champion
comparison, DSPy/Jev optimization runs, actual admitted offline/replay/adversarial
evaluation, shadow/canary evidence, tournaments, promotion/rollback targets, audited
freeze policies, operational telemetry and independent acceptance remain pending.
P00–P13 and all required release gates remain active and incomplete.
