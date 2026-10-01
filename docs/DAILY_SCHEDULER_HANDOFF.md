# Automatic daily paper evidence

The local application can now capture UTC boundary checkpoints and publish daily paper accounting while it runs. The worker uses the existing paper ledger and process-local quotes, with no risk approval, broker dispatch, model call or promotion authority.

## Enablement

The worker is off by default. Configure a persistent `AI_QUANT_PAPER_DB` and the existing operator token, then set `AI_QUANT_DAILY_EVIDENCE_ENABLED=1` before starting the application. `0` disables it; other values are rejected. Programmatic applications can pass `daily_evidence_enabled=True` to `create_app`. Enabling without persistence or an operator token is rejected.

The worker runs inside the application lifespan alongside the existing agent runtime. It polls the UTC clock once per second, moves SQLite/accounting work to a background thread, and finishes an in-flight database operation before shutdown. It has no effect while the application is stopped. Structured logs use the `ai_quant_os.daily_scheduler` logger; configure INFO logging to receive per-day events.

## Capture and publication

- On first activation, persist the activation time and capture the current day's opening checkpoint. Starting after the boundary retains a late capture; existing daily accounting withholds any required opening mark. It does not backdate a capture.
- At a new UTC day, capture that day's boundary once. This is the closing checkpoint for yesterday and the opening checkpoint for today. The first checked capture wins; restart and matching work preserve it, including its prices and original timestamp.
- Publish each closed day for which both automatic boundary checkpoints exist. Accounting uses the unchanged daily formula and mark freshness/capture checks. A report may be `INCOMPLETE_MARKS`; publication alone does not prove full evidence or forward performance.
- Record `MISSED / BOUNDARY_CHECKPOINT_MISSING` for days whose boundary was not captured, including downtime gaps. No historical quote or checkpoint is synthesized. A day with source/publication conflict records `FAILED / PUBLICATION_CONFLICT_OR_LINEAGE_ERROR`.
- Process at most seven closed days per tick. Longer backlogs drain over later ticks. `completed_days` counts terminal receipts, including missed/failed days; separate published/missed/failed counts prevent interpreting it as successful coverage.

The `daily-auto-YYYY-MM-DD` checkpoint namespace is reserved; manual capture requests cannot preoccupy it. A manual report already published under different source checkpoints is preserved and the scheduler records a conflict. Terminal receipts cannot be overwritten. Operator-led investigation, amendment and retention workflows remain unfinished.

## Restart, failures and integrity

Activation and per-day receipts share the sibling evidence database with existing checkpoints/reports. Receipt events have canonical hashes, a previous-event link rooted in activation, and an atomic committed head. Reads check consecutive UTC dates, hashes/head and the exact referenced published report. Deleting receipts/heads or corrupting bytes is refused.

Daily publication and its scheduler receipt are separate transactions. If the process stops after publication but before receipt commit, restart checks and adopts the existing matching report, then commits its receipt. This local idempotency is possible because publication has no external side effect. It does not introduce a general broker/job retry policy.

The worker checks paper audit integrity before capture/publication work. Failure halts new evidence writes. Clock regression is refused; recovery requires the clock to agree with recorded history. Verification/storage and unexpected callback failures remain visible through sanitized error codes and structured logs, without private exception contents. Error retries back off from one second to at most sixty seconds; successful polling returns to one second. Errors may therefore cause a late/missing price rather than fabricated evidence.

Routine same-day polling caches its completed work; it does not read the entire portfolio every second. Explicit status reads verify durable history, and new-day work verifies it again. Archive limits remain 1,000 checkpoints, 730 reports and 64 MiB of artifact bytes; the receipt journal is also bounded to 730 days. There is no pruning/reset. These limits require a reviewed retention policy for prolonged operation.

Local clocks, manual paper inputs and host administration remain trusted. Ledger and quotes are not captured in one common transaction. Hashes detect inconsistent local records but are not external attestations. A local worker is not an HA/distributed scheduler, Temporal workflow or reliable market-feed service.

## Operator status

`GET /api/v1/paper/evidence/scheduler` requires the existing operator token and valid paper audit integrity. It exposes enablement, activation time, current boundary capture time, terminal/published/missed/failed counts, backlog, last receipt and a sanitized last error. States include `DISABLED`, `STARTING`, `WAITING`, `CATCHING_UP` and `ERROR`; direct audit-halt work reports `HALTED_AUDIT`, while the API's existing audit guard refuses access if audit integrity has failed. Existing daily retrieval routes expose the resulting source-retaining reports.

No automatic user notifications or new agent tool permissions are introduced. Report comparison, reality-gap alerting and demotion remain separate pending work.

## Verification

Eleven focused tests cover ordinary rollover and restart, downtime without fake captures, interrupted receipt commit after publication, audit/clock refusal, archive/head corruption, actual application worker startup/rollover/shutdown and protected status, configuration, late mark withholding, bounded catch-up, reserved identifiers and sanitized unexpected failures. The lifecycle probe waits for the actual first boundary capture, so activation alone is not mistaken for readiness.

Two independent controlled-clock schedulers consumed the retained paper API fixture and produced identical source-retaining evaluations, with 1.40 USD daily accounting and verified publication receipts. Restart preserved source checkpoints and report hashes. The saved `docs/evaluations/daily-scheduler-baseline.json` and its schema validated. This is controlled replay evidence, not a real midnight uptime or production ingress measurement. Hosted CI, scheduling under production load, independent capture timestamps, alert delivery, retention/recovery and the remaining P09 requirements are unverified or unfinished.
