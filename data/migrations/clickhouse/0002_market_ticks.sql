CREATE TABLE IF NOT EXISTS quant_market.ticks_v1 (
    instrument_id LowCardinality(String),
    source_id LowCardinality(String),
    sequence UInt64,
    observed_at DateTime64(6, 'UTC'),
    bid Decimal(38, 18),
    ask Decimal(38, 18),
    bid_size Decimal(38, 18),
    ask_size Decimal(38, 18),
    source_archive_id String,
    ingested_at DateTime64(6, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(observed_at)
ORDER BY (instrument_id, source_id, sequence, observed_at, source_archive_id);
