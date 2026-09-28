# P02 data platform handoff

The local ingestion adapter accepts historical bar CSV files, normalizes aware timestamps to UTC, rejects conflicting duplicates, and writes the original bytes and a typed Parquet copy under `BarArchive.root`. Object names contain SHA-256 hashes. A manifest links source, dataset fingerprint, quality counts, time range, and both object hashes. The manifest is published last. Replay checks both object hashes and the reconstructed dataset fingerprint before returning bars.

Install with `python -m pip install -e ".[dev,data]"`. Example:

```powershell
ai-quant-data ingest .\bars.csv --root .\data-local --source-id fixture:eurusd
ai-quant-data replay <archive-id> --root .\data-local
ai-quant-data health <archive-id> --root .\data-local --max-age-seconds 300
```

The `health` command exits with code 2 when the newest bar is stale or dated in the future. A fixed dataset replays in the same order and has the same fingerprint. An identical source and source ID return the existing manifest.

The layout is designed for object storage mirroring: `raw/sha256/<prefix>/<hash>.csv`, `normalized/bars/schema=v1/<hash>.parquet`, and `manifests/<archive-id>.json`. The filesystem adapter is the runnable local implementation. Object store upload and production ingestion jobs remain open.

`migrations/postgres/0001_lineage.up.sql` defines operational lineage and quality metadata. It was applied and integration-tested against a temporary PostgreSQL 18 container. The `PostgresLineageCatalog` adapter records CSV sources, archive manifests, and timestamped freshness observations. First apply the migration to the target database and set `AI_QUANT_DATA_DATABASE_URL` in the process environment. To save catalog records, add `--catalog` to `ingest`; for `health`, add both `--catalog` and `--trace-id <id>`. The local archive remains the source of raw and normalized objects; the catalog stores their relative paths and checksums. Repeating catalog ingestion with the same manifest is safe, while conflicting metadata is rejected.

`migrations/clickhouse/0001_market_bars.sql` defines a UTC analytics bar table; it has not been applied to a running ClickHouse service. PostgreSQL testing was local and ephemeral, not a deployment. Rollback preserves archived objects and database tables; revert readers and writers first, then perform an explicitly reviewed data migration if schema removal is ever necessary.

S3/MinIO object upload, ClickHouse integration, Valkey cache integration, ingest metrics/traces, production source adapters, durable tick archival, and independent review remain open. No data service has capital authority.

Quote tick CSV normalization is available through `import_ticks_csv` in `ai_quant_os.tick_data`. The file needs `instrument_id,source_id,sequence,observed_at,bid,ask,bid_size,ask_size`. Each source must provide a stable sequence that is unique for that instrument and source ID. Identical repeated keys are counted and collapsed; different payloads at the same key are rejected. Aware timestamps become UTC, replay order and fingerprint are stable, and freshness rejects stale or future ticks. Durable tick archive and ingestion adapters remain open.
