# A06 paper event risk

A06 0.2.0 runs as a `PARTIAL`, `SHADOW` mission in the persisted graph. It reads
exactly economic calendar, event classifications and local paper positions.
Confidence is zero, uncertainty is one and escalation is mandatory. Every
instrument assessment has `may_add_risk=false`; authority remains `RISK_KERNEL`.
All eighteen original 0.1.0 manifests remain planned.

## Publish and execute

Operator-protected `POST /api/v1/paper/events/feed` accepts the strict contract in
`schemas/event-feed.schema.json`. Supply aware timestamps and synthetic event
observations; the server normalizes UTC and derives an immutable content reference.

```json
{
  "observed_at": "2026-10-03T10:00:00Z",
  "valid_until": "2026-10-03T10:02:00Z",
  "coverage_start": "2026-10-03T09:00:00Z",
  "coverage_end": "2026-10-04T10:00:00Z",
  "economic_calendar": [{
    "event_id": "synthetic-usd-event",
    "kind": "SCHEDULED",
    "currencies": ["USD"],
    "severity": "HIGH",
    "occurs_at": "2026-10-03T10:05:00Z",
    "reported_at": "2026-10-03T10:00:00Z"
  }],
  "event_classifications": []
}
```

Replace these illustrative timestamps with the current observation and coverage.
Expired or future publication is refused. Detected events belong in
`event_classifications`, have kind `DETECTED`, and cannot occur after reporting.
Provenance is always `PAPER_OPERATOR_SYNTHETIC_EVENT_FEED`. Classification/severity
is supplied by the operator; no external calendar or news classifier is connected.

Public `GET /api/v1/events/feed` returns current freshness and the original feed.
Protected `GET /api/v1/events/feeds/{feed_ref}` returns archival bytes without
refreshing expiry. Execute with the existing protected graph routes:

```text
POST /api/v1/paper/agents/context
{"agent_id":"A06","agent_version":"0.2.0","task_id":"event-observation"}

POST /api/v1/paper/agents/execute
{"run_id":"event-observation-1","context_ref":"<returned context_ref>"}

GET /api/v1/agents/graph-runs/event-observation-1
```

## Declared paper policy

The uncalibrated feature is `max(0, 1 - abs(seconds_until_event)/window) * weight`.
HIGH uses a 900-second window and weight 1; MEDIUM uses 300 seconds and weight
0.5. Both recommend blackout inside the inclusive window, including its boundary
where the feature equals zero. LOW uses 900 seconds and weight 0.25, with no
blackout recommendation. UNKNOWN withholds window, score and recommendation.
This feature is not a probability or a validated live trading threshold.

Relevance covers only an instrument's direct base and quote currencies. A
positive observed blackout yields `NO_NEW_RISK`. Missing, stale or future feeds,
unknown relevant severity, or incomplete coverage yield `UNASSESSED` unless a
positive blackout is already observed. A negative recommendation requires fresh
coverage of the observation time plus and minus 900 seconds and yields
`OBSERVE_ONLY`; it does not authorize capital. Indirect currency/portfolio event
effects, marked exposure, collateral and liquidity are unassessed.

The output schema is `schemas/event-risk-state.schema.json`. Root measurements
retain full event IDs and signed time differences. Each instrument's
`event_indices` contains zero-based references into that root measurement array.
This avoids duplicating long IDs while preserving all 128 supported events
within the existing graph output budget. Typed validation checks these
associations and recomputes the declared policy. Decimal output uses fixed-point
strings; proximity representations are bounded to 20 digits and exponent -6..12.

## Evidence and storage boundaries

Feeds contain at most 128 combined scheduled/detected events, unique ASCII IDs
of 1..64 characters, and only EUR/GBP/JPY/USD currencies. TTL is at most 300
seconds and declared coverage at most seven days. The local archive holds at
most 128 feeds of 65,536 bytes each. New publications commit with their audit
record in one SQLite transaction and must not move observation time backwards.
Duplicate publication is idempotent. Capacity exhaustion requires a separately
designed retention/export policy; the service does not delete history.

The durable app uses a separate `<paper-db-stem>.events.sqlite3`. Reads verify
canonical bytes, hashes, publication coverage, order and the journal chain.
Missing integrity metadata or altered/deleted records fail closed; storage
failures return 503. The local chain has no independent external anchor and
cannot prove absence of privileged whole-archive resealing.

A06 captures fills and pending orders together from the local paper ledger,
retaining raw evidence and its hash. It reports filled net base units and
pending buy/sell units separately. Input limits are 512 fills and 512 pending
orders; quantity bounds are positive, at most 1e12, 64 stored digits and exponent
-12..12. The 65,536-byte per-input and 160,000-byte total context limits may
reject a large ledger earlier. Evidence is never silently truncated. Calendar
and classifications share one checked feed snapshot; A06 does not require world
price arithmetic. Source facts expire within five seconds and fresh feed expiry.
Current unavailability facts do not refresh an old feed.

Paper quote, L1 quote and bar HTTP intake now require configured instruments and
finite prices in 1e-12..1e12 with at most 64 stored digits. All OHLC values in a
batch pass admission before any bar is recorded. This fixes a reproduced extreme
price arithmetic failure. Request shapes and sealed trading/risk modules remain
unchanged. Stale quotes and excessive spreads still block actual order admission
before and after A06 execution.

## Retained development baseline

```text
python -m ai_quant_os.event_evaluation tests/fixtures/event_baseline.json --output <new-report-path>
```

The evaluator refuses overwrite and returns failure on mismatched expectations.
`evaluations/event-baseline.json` retains seven matching actual persisted graph
cases: scheduled HIGH, detected MEDIUM, UNKNOWN severity, incomplete prior
coverage, expired feed, missing feed and an empty fresh feed. Each output passes
typed and JSON Schema validation. Source hashes bind six named event modules,
the evaluator and the recipe, with context references and measured invocation
duration. API composition, market intake, transitive runtime and environment are
outside these named source pins. LF attributes preserve retained named bytes.

This is synthetic development evidence. External providers, calibrated policy,
held-out acceptance, production scheduling/telemetry, independent review and
promotion remain open. A06 is not added to the A02/A07 repair or genome catalogs.
The full execution pack and live-release gates remain unfinished.

## Rollback

Stop the owned application, preserve its complete state directory including the
event archive and graph receipts, then revert this increment. No shared database
migration is introduced. Older code lacks the A06 0.2.0 manifest and may not serve
its receipts; retain them for a compatible reader. The planned 0.1.0 manifest is
not an executable rollback. No order or safety transition is needed.

## Local verification

The full suite passed 1,152 tests with 15 skips in 614.78 seconds, with actual
built-frontend integration required. The focused event/market/registry suite
passed 160 tests in 21.51 seconds. Lint and formatting passed for 241 Python
files. Both 96-package dependency locks reproduced and installed dependencies
passed consistency checks. All named A04/A05/A06 source, evaluator and fixture
pins and output schemas verified in the worktree and a fresh index checkout.
Both new schemas reproduce the current models. Four sealed strategy closures,
ten retained artifact/schema checks, nine older artifact bytes and previous
genome/protocol compatibility checks passed. Staged changes and all 81 previous
commits passed secret scanning; one exact verified source-checksum line has a
scoped false-positive exception. The structure map was refreshed. Automatic
architecture checking cannot run without its missing declaration; manual direct
import review passed. Hosted/Linux runtime and production verification remain open.
