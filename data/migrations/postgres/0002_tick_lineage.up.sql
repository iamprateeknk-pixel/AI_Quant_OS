BEGIN;

CREATE TABLE quant_data.tick_archives (
    archive_id text PRIMARY KEY CHECK (archive_id ~ '^[0-9a-f]{64}$'),
    source_id text NOT NULL REFERENCES quant_data.sources(source_id),
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    dataset_fingerprint text NOT NULL CHECK (dataset_fingerprint ~ '^[0-9a-f]{64}$'),
    parquet_sha256 text NOT NULL CHECK (parquet_sha256 ~ '^[0-9a-f]{64}$'),
    raw_uri text NOT NULL,
    normalized_uri text NOT NULL,
    row_count bigint NOT NULL CHECK (row_count > 0),
    duplicate_rows bigint NOT NULL CHECK (duplicate_rows >= 0),
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version > 0),
    CHECK (last_observed_at >= first_observed_at)
);

CREATE INDEX tick_archives_source_time_idx
    ON quant_data.tick_archives (source_id, last_observed_at DESC);

CREATE TABLE quant_data.tick_quality_observations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    archive_id text NOT NULL REFERENCES quant_data.tick_archives(archive_id),
    checked_at timestamptz NOT NULL,
    age_seconds double precision NOT NULL,
    fresh boolean NOT NULL,
    trace_id text NOT NULL CHECK (length(trace_id) > 0)
);

CREATE INDEX tick_quality_observations_archive_time_idx
    ON quant_data.tick_quality_observations (archive_id, checked_at DESC);

COMMIT;
