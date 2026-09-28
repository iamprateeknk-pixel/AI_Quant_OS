CREATE DATABASE IF NOT EXISTS quant_market;

CREATE TABLE IF NOT EXISTS quant_market.bars_v1 (
    instrument_id LowCardinality(String),
    observed_at DateTime64(6, 'UTC'),
    open Decimal(38, 18),
    high Decimal(38, 18),
    low Decimal(38, 18),
    close Decimal(38, 18),
    volume Decimal(38, 18),
    source_archive_id String,
    ingested_at DateTime64(6, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(observed_at)
ORDER BY (instrument_id, observed_at, source_archive_id);
