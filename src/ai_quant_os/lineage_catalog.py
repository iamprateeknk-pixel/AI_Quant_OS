"""PostgreSQL operational catalog for immutable bar archive lineage."""

from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from ai_quant_os.contracts import _require_aware
from ai_quant_os.data_archive import BarManifest, DataHealth
from ai_quant_os.tick_archive import TickManifest


class PostgresLineageCatalog:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def register_source(self, source_id: str, *, kind: str) -> None:
        if not source_id.strip() or kind not in {"CSV", "BROKER", "VENDOR"}:
            raise ValueError("Invalid source")
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO quant_data.sources (source_id, source_kind) "
                    "VALUES (%s, %s) ON CONFLICT (source_id) DO NOTHING",
                    (source_id, kind),
                )
                cursor.execute(
                    "SELECT source_kind FROM quant_data.sources WHERE source_id = %s",
                    (source_id,),
                )
                if cursor.fetchone()[0] != kind:
                    raise ValueError("Source kind conflict")

    def record_archive(self, manifest: BarManifest) -> None:
        columns = (
            "archive_id",
            "source_id",
            "source_sha256",
            "dataset_fingerprint",
            "parquet_sha256",
            "raw_uri",
            "normalized_uri",
            "row_count",
            "duplicate_rows",
            "first_observed_at",
            "last_observed_at",
            "ingested_at",
            "schema_version",
        )
        values = (
            manifest.archive_id,
            manifest.source_id,
            manifest.source_sha256,
            manifest.dataset_fingerprint,
            manifest.parquet_sha256,
            manifest.raw_path,
            manifest.parquet_path,
            manifest.row_count,
            manifest.duplicate_rows,
            manifest.first_observed_at,
            manifest.last_observed_at,
            manifest.ingested_at,
            manifest.schema_version,
        )
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO quant_data.bar_archives (" + ", ".join(columns) + ") "
                    "VALUES (" + ", ".join(["%s"] * len(columns)) + ") "
                    "ON CONFLICT (archive_id) DO NOTHING",
                    values,
                )
                if cursor.rowcount == 0:
                    existing = self._get_archive(connection, manifest.archive_id)
                    if existing != manifest:
                        raise ValueError("Archive metadata conflict")

    def get_archive(self, archive_id: str) -> BarManifest:
        with psycopg.connect(self._dsn) as connection:
            return self._get_archive(connection, archive_id)

    @staticmethod
    def _get_archive(connection: psycopg.Connection, archive_id: str) -> BarManifest:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT archive_id, source_id, source_sha256, dataset_fingerprint, "
                "parquet_sha256, raw_uri, normalized_uri, row_count, duplicate_rows, "
                "first_observed_at, last_observed_at, ingested_at, schema_version "
                "FROM quant_data.bar_archives WHERE archive_id = %s",
                (archive_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(archive_id)
        return BarManifest(
            archive_id=row["archive_id"],
            source_id=row["source_id"],
            source_sha256=row["source_sha256"],
            dataset_fingerprint=row["dataset_fingerprint"],
            parquet_sha256=row["parquet_sha256"],
            raw_path=row["raw_uri"],
            parquet_path=row["normalized_uri"],
            row_count=row["row_count"],
            duplicate_rows=row["duplicate_rows"],
            schema_version=row["schema_version"],
            ingested_at=row["ingested_at"].astimezone(timezone.utc),
            first_observed_at=row["first_observed_at"].astimezone(timezone.utc),
            last_observed_at=row["last_observed_at"].astimezone(timezone.utc),
        )

    def record_quality(
        self, archive_id: str, health: DataHealth, *, checked_at: datetime, trace_id: str
    ) -> None:
        _require_aware(checked_at)
        if not trace_id.strip():
            raise ValueError("Trace id is required")
        with psycopg.connect(self._dsn) as connection:
            connection.execute(
                "INSERT INTO quant_data.quality_observations "
                "(archive_id, checked_at, age_seconds, fresh, trace_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (archive_id, checked_at, health.age_seconds, health.fresh, trace_id),
            )

    def latest_quality(self, archive_id: str) -> tuple[datetime, float, bool] | None:
        with psycopg.connect(self._dsn) as connection:
            row = connection.execute(
                "SELECT checked_at, age_seconds, fresh "
                "FROM quant_data.quality_observations WHERE archive_id = %s "
                "ORDER BY checked_at DESC, id DESC LIMIT 1",
                (archive_id,),
            ).fetchone()
        if row is None:
            return None
        return row[0].astimezone(timezone.utc), row[1], row[2]

    def record_tick_archive(self, manifest: TickManifest) -> None:
        values = (
            manifest.archive_id,
            manifest.source_id,
            manifest.source_sha256,
            manifest.dataset_fingerprint,
            manifest.parquet_sha256,
            manifest.raw_path,
            manifest.parquet_path,
            manifest.row_count,
            manifest.duplicate_rows,
            manifest.first_observed_at,
            manifest.last_observed_at,
            manifest.ingested_at,
            manifest.schema_version,
        )
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO quant_data.tick_archives "
                    "(archive_id, source_id, source_sha256, dataset_fingerprint, "
                    "parquet_sha256, raw_uri, normalized_uri, row_count, duplicate_rows, "
                    "first_observed_at, last_observed_at, ingested_at, schema_version) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (archive_id) DO NOTHING",
                    values,
                )
                if cursor.rowcount == 0:
                    existing = self._get_tick_archive(connection, manifest.archive_id)
                    if existing != manifest:
                        raise ValueError("Tick archive metadata conflict")

    def get_tick_archive(self, archive_id: str) -> TickManifest:
        with psycopg.connect(self._dsn) as connection:
            return self._get_tick_archive(connection, archive_id)

    @staticmethod
    def _get_tick_archive(connection: psycopg.Connection, archive_id: str) -> TickManifest:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT archive_id, source_id, source_sha256, dataset_fingerprint, "
                "parquet_sha256, raw_uri, normalized_uri, row_count, duplicate_rows, "
                "first_observed_at, last_observed_at, ingested_at, schema_version "
                "FROM quant_data.tick_archives WHERE archive_id = %s",
                (archive_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(archive_id)
        return TickManifest(
            archive_id=row["archive_id"],
            source_id=row["source_id"],
            source_sha256=row["source_sha256"],
            dataset_fingerprint=row["dataset_fingerprint"],
            parquet_sha256=row["parquet_sha256"],
            raw_path=row["raw_uri"],
            parquet_path=row["normalized_uri"],
            row_count=row["row_count"],
            duplicate_rows=row["duplicate_rows"],
            schema_version=row["schema_version"],
            ingested_at=row["ingested_at"].astimezone(timezone.utc),
            first_observed_at=row["first_observed_at"].astimezone(timezone.utc),
            last_observed_at=row["last_observed_at"].astimezone(timezone.utc),
        )

    def record_tick_quality(
        self, archive_id: str, health: DataHealth, *, checked_at: datetime, trace_id: str
    ) -> None:
        _require_aware(checked_at)
        if not trace_id.strip():
            raise ValueError("Trace id is required")
        with psycopg.connect(self._dsn) as connection:
            connection.execute(
                "INSERT INTO quant_data.tick_quality_observations "
                "(archive_id, checked_at, age_seconds, fresh, trace_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (archive_id, checked_at, health.age_seconds, health.fresh, trace_id),
            )

    def latest_tick_quality(self, archive_id: str) -> tuple[datetime, float, bool] | None:
        with psycopg.connect(self._dsn) as connection:
            row = connection.execute(
                "SELECT checked_at, age_seconds, fresh "
                "FROM quant_data.tick_quality_observations WHERE archive_id = %s "
                "ORDER BY checked_at DESC, id DESC LIMIT 1",
                (archive_id,),
            ).fetchone()
        if row is None:
            return None
        return row[0].astimezone(timezone.utc), row[1], row[2]
