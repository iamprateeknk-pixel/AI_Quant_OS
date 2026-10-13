# Daily paper execution attribution

The protected daily-evidence workflow can publish execution attribution for an already published UTC daily accounting report. The result decomposes only fills in that half-open UTC day using the retained initial risk-decision quote, actual fill price and recorded fee. It is bound to the daily report's opening/closing checkpoints and retains the exact selected owners, fills and available benchmarks.

## API

Both routes require the existing operator token and return `Cache-Control: no-store`.

| Route | Behavior |
| --- | --- |
| `POST /api/v1/paper/evidence/daily/{day}/execution-attribution` | Publish the immutable daily execution artifact. The request has no body; all sources come from the server-owned paper ledger and the saved daily report. |
| `GET /api/v1/paper/evidence/daily/{day}/execution-attribution` | Read and reverify the retained artifact. |

Publication requires an existing daily accounting report. The opt-in daily scheduler automatically publishes attribution after its daily accounting report commits and retries transient failures, including after restart. Manual operators can also use the protected POST route. Before publishing, the server verifies that current pre-opening fills still match the saved opening checkpoint, the day's fills match the saved closing checkpoint, and current execution ownership still matches the closed day's order identity. It uses the ownership and status recorded at close, plus the ledger's durable initial decision benchmarks. Client input cannot supply or replace reports, owners, fills, prices, timestamps or paths.

The first publication wins. Repeating the publication returns its original artifact, including its original publication time. A changed source or report is refused with HTTP 409; an absent daily report returns 404; corrupted or unverifiable retained data returns 503. The artifact is retained in the sibling evidence SQLite database in an additive table, with its daily report and execution sources. The archive still enforces 730 records per daily-report table, 16 MiB per artifact and 64 MiB combined stored bytes.


## Period aggregation

`GET /api/v1/paper/evidence/daily-execution-attribution?start=YYYY-MM-DD&end=YYYY-MM-DD` returns a protected, no-store PAPER rollup over an inclusive window of at most 366 days. It reads and reverifies each retained daily execution artifact within one SQLite read transaction, then aggregates exact cash and fee totals by strategy/version/instrument. Midpoint, spread and decision-to-fill totals are withheld for a group if any included fill lacks its initial benchmark. The report carries SHA-256 references for every included daily source and explicitly lists covered and missing days; gaps are never treated as zero activity. No evidence returns `NO_EVIDENCE`; partial days or missing benchmarks return `PARTIAL`. The result is diagnostic and never grants promotion authority. Its closed schema is `docs/schemas/daily-execution-aggregate.schema.json`.

## Accounting and limits

The embedded `ExecutionContributionReport` uses the existing midpoint, side-quote, decision-to-fill and fee decomposition at actual filled quantities. Benchmark gaps remain partial or absent; the report never infers a missing initial quote. The JSON Schema is `docs/schemas/daily-execution-evidence.schema.json`.

This does not provide fill-time market quotes, latency attribution, financing, restatements or shadow aggregation. Manual paper fills remain labelled as such. An empty day can publish an `EMPTY` report. None of these records approve a candidate, change risk, or grant broker/live authority.

## Verification and rollback

`pytest -q tests/test_daily_execution_aggregation.py tests/test_daily_execution_evidence.py tests/test_daily_evidence.py tests/test_daily_scheduler.py` covers exact decomposition, missing and lookahead benchmarks, changed historical sources, immutable retries, restart/corruption, authorization and the actual API/schema response.

Rollback may remove the new API surface and ignore the new table, but preserve the evidence database and its WAL. Do not delete or rewrite published daily accounting or execution records.
