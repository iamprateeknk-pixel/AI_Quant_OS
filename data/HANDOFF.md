# P02 data platform handoff

The local ingestion adapter accepts historical bar CSV files, normalizes aware timestamps to UTC, rejects conflicting duplicates, and writes the original bytes and a typed Parquet copy under `BarArchive.root`. Object names contain SHA-256 hashes. A manifest links source, dataset fingerprint, quality counts, time range, and both object hashes. The manifest is published last. Replay checks both object hashes and the reconstructed dataset fingerprint before returning bars.

Install with `python -m pip install -e ".[dev,data]"`. Example:

```powershell
ai-quant-data ingest .\bars.csv --root .\data-local --source-id fixture:eurusd
ai-quant-data replay <archive-id> --root .\data-local
ai-quant-data health <archive-id> --root .\data-local --max-age-seconds 300
ai-quant-data ingest-ticks .\ticks.csv --root .\data-local --source-id fixture:ticks
ai-quant-data replay-ticks <tick-archive-id> --root .\data-local
ai-quant-data health-ticks <tick-archive-id> --root .\data-local --max-age-seconds 5
ai-quant-data mirror <archive-id> --kind bars --root .\data-local --bucket quant-archives --prefix datasets --endpoint-url http://127.0.0.1:9000
ai-quant-data restore <archive-id> --kind bars --root .\restored --bucket quant-archives --prefix datasets --endpoint-url http://127.0.0.1:9000
ai-quant-data cache-ticks <tick-archive-id> --root .\data-local --ttl-seconds 60
```

The `health` command exits with code 2 when the newest bar is stale or dated in the future. A fixed dataset replays in the same order and has the same fingerprint. An identical source and source ID return the existing manifest.

The layout is designed for object storage mirroring: `raw/sha256/<prefix>/<hash>.csv`, `normalized/bars/schema=v1/<hash>.parquet`, and `manifests/<archive-id>.json`. `S3ArchiveMirror` copies verified bar or tick archives to an existing S3-compatible bucket, publishing the manifest last, then can restore and replay an archive into a separate local root. Repeated uploads use conditional writes, compare existing bytes, and reject changes. Pass `--kind ticks` for tick archives. The S3 client uses the standard AWS credential chain; configure credentials in the process environment or an external credential provider, not on the command line. Provision bucket access limited to GetObject and PutObject for the chosen prefix; the application does not need delete or list access. The object-store adapter passed in-process and HTTP S3 emulator checks. MinIO-specific compatibility and production object-store provisioning remain unverified.

`migrations/postgres/0001_lineage.up.sql` and `0002_tick_lineage.up.sql` define operational bar and tick lineage and quality metadata. Both were applied and integration-tested against a temporary PostgreSQL 18 container. The `PostgresLineageCatalog` adapter records CSV sources, archive manifests, and timestamped freshness observations. First apply both migrations in order to the target database and set `AI_QUANT_DATA_DATABASE_URL` in the process environment. To save catalog records, add `--catalog` to either ingest command; for either health command, add both `--catalog` and `--trace-id <id>`. The local archive remains the source of raw and normalized objects; the catalog stores their relative paths and checksums. Repeating catalog ingestion with the same manifest is safe, while conflicting metadata is rejected.

`migrations/clickhouse/0001_market_bars.sql` and `0002_market_ticks.sql` define UTC analytics tables. Both were applied to a temporary ClickHouse 26.8 service and accepted sample bar and tick rows. PostgreSQL and ClickHouse testing was local and ephemeral, not a deployment. Rollback preserves archived objects and database tables; revert readers and writers first, then perform an explicitly reviewed data migration if schema removal is ever necessary.

Set `AI_QUANT_VALKEY_URL` in the process environment before `cache-ticks`. `LatestTickCache` verifies the tick archive before updating a short-lived latest-tick entry per instrument and source. It uses an atomic compare-and-set so older sequences cannot replace newer ones; conflicting sequences and timestamp regressions fail. Reads return no value for missing, corrupt, stale, or future entries. The cache is disposable and does not grant capital authority. It was integration-tested against a temporary Valkey 9.0 service. Production Valkey authentication, TLS, provisioning, and monitoring remain open.

Production S3/MinIO provisioning, ClickHouse publication jobs, ingest metrics/traces, production source adapters, and independent review remain open. No data service has capital authority.

Quote tick CSV normalization is available through `import_ticks_csv` in `ai_quant_os.tick_data`. The file needs `instrument_id,source_id,sequence,observed_at,bid,ask,bid_size,ask_size`. Each source must provide a stable sequence that is unique for that instrument and source ID. Identical repeated keys are counted and collapsed; different payloads at the same key are rejected. Aware timestamps become UTC, replay order and fingerprint are stable, and freshness rejects stale or future ticks. `TickArchive` preserves the raw file and typed Parquet copy, with checksum-verified replay. Its manifest lives under `manifests/ticks/` to keep bar and tick identifiers separate. Bar and tick replay also validate the manifest's duplicate count and time range against the preserved raw source.
