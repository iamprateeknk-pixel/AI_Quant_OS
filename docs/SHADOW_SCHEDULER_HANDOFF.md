# Automatic PAPER shadow scheduler

## Scope

This increment adds an opt-in local worker that advances verified shadow assignments from PAPER bars already archived by the world-state ingress. The scheduler does not collect market data, create assignments, fabricate bars during downtime, place orders, or promote candidates. Every report remains marked `promotion_allowed: false`.

Enable it with `AI_QUANT_SHADOW_SCHEDULER_ENABLED=1` (or `create_app(shadow_scheduler_enabled=True)`). Configuration requires persistent PAPER storage, an operator token, a research workspace, and existing `shadow.sqlite3`, `candidates.sqlite3`, and `strategies/` inputs. It starts and stops with the application lifespan. The protected status endpoint is `GET /api/v1/paper/shadows/scheduler`.

Each cycle verifies PAPER audit integrity, enumerates a bounded inventory of hash-verified assignments, skips retired assignments, and reads bars after the last assignment/report source timestamp. Each observation waits until at least two new bars are available. The scheduler caps the cumulative verified source at 4,096 bars, matching the shadow evidence contract; it reports `SHADOW_SOURCE_LIMIT_REACHED` when more bars arrive after that cap. Deterministic command IDs make a retried source batch idempotent. The worker polls every five seconds when healthy and uses capped exponential backoff after errors. It does not synthesize data for downtime.

The worker handles at most 1,000 assignments, and shadow evidence is limited to 4,096 cumulative source bars per assignment. The source bound requires a new shadow assignment for continued observations after the cap. Larger assignment inventories surface an error for operator attention. Source authenticity, deletion-proof completeness, provider scheduling, and independent recovery evidence are not established; ingress accepts operator-submitted `PAPER_SIMULATED` bars only. This is local research evidence, not live-broker validation or production readiness.

## Verification

Focused tests cover verified assignment enumeration, initial history wait, idempotent catch-up, audit-integrity halt, configuration, protected status, and application lifespan startup/shutdown. See `docs/BUILD_STATUS.md` for the run evidence.
