# A03 dated macro drivers

A03 0.2.0 now runs as a `PARTIAL`, `SHADOW` mission in the persisted paper graph.
It consumes exactly `macro_feed`, `event_feed`, `rates_yields` and `world_state`.
Its output is `CurrencyMacroBias` with confidence zero, uncertainty one and
mandatory escalation. Every currency has `bias=UNASSESSED` and
`may_add_risk=false`. Authority remains with `RISK_KERNEL`; no model, provider,
broker, order or control call occurs during this deterministic mission.

## Supply and execute

The durable API reads the verified `rates.sqlite3` beside its configured paper
database. With the default local launcher, use the same state directory:

```text
ai-quant-data fetch-ecb-rates --root data-local/local-app
```

This is a separate explicit public HTTPS data command; compiling/executing A03
does not retrieve network data. Local CSV import remains separately disclosed.
The API verifies the entire bounded rate archive and reconstructs raw facts on
each read. Missing archive becomes a `MISSING` fact without creating a database.
Corrupt, inaccessible or locked archive returns 503, preserving original data.
Storage reads run outside the asynchronous request loop; the world snapshot is
taken afterwards so database wait time does not consume its freshness window.

The existing operator-protected context and execution routes now support A03:

```text
POST /api/v1/paper/agents/context
{"agent_id":"A03","agent_version":"0.2.0","task_id":"macro-observation"}

POST /api/v1/paper/agents/execute
{"run_id":"macro-observation-1","context_ref":"<returned context_ref>"}

GET /api/v1/agents/graph-runs/macro-observation-1
```

Archived run reads also require the operator header. Receipts/checkpoints retain
the original evidence, exact version, timestamps, source references and output.
Restart serves the same receipt bytes without refreshing old evidence.

## Dated policy-rate drivers

EUR drivers retain the ECB deposit, marginal lending and main refinancing series,
their one or two original observations, source capture reference/mode and UTC
receipt time. Observation dates remain dates of rate changes. A date-only cutoff
uses the observation clock's UTC calendar date, explicitly named `UTC_DATE_ONLY`.
This is a descriptive convention and does not establish intraday effectiveness.

For two observations on or before the cutoff, the difference in annual percent
levels is the change in percentage points; multiplying by 100 yields basis
points. Missing previous observations withhold changes. Future-dated periods
remain retained but cannot supply the latest observed value or change. No
averaging of the three correlated policy rates or inferred currency forecast
occurs. `current_applicability=UNASSESSED` and historical-vintage guarantee false
remain attached to the result. Values use decimal arithmetic under an explicit
50-digit context, independent of caller precision.

Receipt status is `RECENT_RECEIPT` for an age below 300 seconds, `OLD_RECEIPT` at
or beyond that age, `FUTURE_RECEIPT` when receipt is after the observation clock,
and `MISSING` without a capture. Recent retrieval does not mean current policy.
Old captures can supply dated archival drivers with age disclosed; future
receipts and missing captures supply no drivers. Fresh source facts expire
within the paper world's validity, the remaining recent-receipt interval and a
fresh event feed's original expiry. Unavailability facts never refresh raw feeds.

Driver association uses zero-based indices in the root array. EUR references
three dated drivers when usable; GBP, JPY and USD have no rate driver feed.
Currency bias remains unassessed because releases, yields, central-bank text,
other currency inputs and calibrated interpretation are unavailable. The paper
world's macro-availability flags remain false; A03 exposes the separate archive
without silently changing world policy or calling these inputs a complete feed.

## Source and output contracts

The server supplies explicit missing release/text/yield facts. Event evidence is
the audited A06 operator-supplied synthetic feed; only fresh feeds supply
scheduled/detected counts per directly associated currency. These counts are not
event probabilities or blackout decisions. Missing, old or future event feeds
withhold counts. All 128 supported events and full IDs fit the real context and
remain preserved; counts introduce no event truncation.

All four sources share the same world version and observation time. The world
hash is reconstructed for the fixed four-instrument paper profile: five-second
quote age, sixty-second bar age and sixty-four-bar history. World status must
agree with its observations. Rate identity/receipt status and event identity/
freshness are checked independently. Mismatched sources produce an `INVALID`
graph receipt, rather than a result with guessed inputs.

Each input is capped at 65,536 bytes and the total context at 160,000 bytes. The
output schema is `schemas/currency-macro-bias.schema.json`; its validator
recomputes driver arithmetic, receipt status, required driver coverage, currency
associations and possible event-count totals. Impossible fresh/missing claims and
forged measurements are refused. The actual order gateway still blocks stale
quotes and excessive spreads before and after A03 execution.

## Retained development evaluation

```text
python -m ai_quant_os.macro_evaluation tests/fixtures/macro_baseline.json --output <new-report-path>
```

The command refuses overwrite and returns nonzero on an expectation mismatch.
`evaluations/macro-baseline.json` retains six matching persisted graph cases:
original ECB recent receipt, old receipt, future receipt, missing sources, a
separately marked local synthetic future-period case and synthetic event counts.
The original ECB receipt is copied unchanged from the prior actual HTTPS capture;
world clocks and event observations are development fixtures. This is not held-out
quality, historical availability, live scheduling or calibrated bias evidence.

The report pins the recipe, three named macro mission source files and evaluator,
with exact context references, typed/schema-valid outputs and invocation durations.
Pins do not cover API composition, registry, transitive rate/event/world helpers
or the complete environment. Prior ECB source/raw/schema pins remain preserved.
LF attributes retain named source and recipe bytes across checkout.

All eighteen original 0.1.0 manifests remain planned. A03 adds no repair/genome
source registration, model routing or promotion. Complete macro releases, yield
curves, other currencies, central-bank interpretation, independent calibration,
production scheduling/telemetry and every wider execution-pack/live gate remain
open.

## Rollback

Stop the owned local application, preserve all state including rate/event archives
and graph receipts, then revert this increment. No shared database migration is
introduced. Older code lacks A03 0.2.0 and may not serve its receipts; retain them
for a compatible reader. The original planned manifest is not an executable
rollback. No order or safety transition is required for this advisory increment.

## Local verification

136 focused macro/API/source/evaluator/registry checks passed in 18.24 seconds.
The full regression passed: 1,246 checks, with 15 skipped, in 869.46 seconds; actual built-frontend integration was required. Ruff passed for 259 Python files. Both 96-package locks reproduced and installed dependency consistency passed. The six-case report and model schema matched in a fresh index checkout. Prior ECB and A04/A05/A06 named pins, four sealed strategy closures, ten retained artifact/schema checks, nine older artifact bytes and genome/protocol compatibility checks remained intact. Staged changes and all 83 prior commits passed secret scans. Manual import review passed; automatic architecture checking remains unavailable without its machine-readable declaration.
