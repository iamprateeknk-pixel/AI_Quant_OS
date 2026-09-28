import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_quant_os.contracts import _require_aware
from ai_quant_os.market_data import Bar, BarDataset, import_bars_csv_bytes


@dataclass(frozen=True)
class BarManifest:
    archive_id: str
    source_id: str
    source_sha256: str
    dataset_fingerprint: str
    parquet_sha256: str
    raw_path: str
    parquet_path: str
    row_count: int
    duplicate_rows: int
    schema_version: int
    ingested_at: datetime
    first_observed_at: datetime
    last_observed_at: datetime


@dataclass(frozen=True)
class DataHealth:
    fresh: bool
    age_seconds: float
    row_count: int
    duplicate_rows: int


def _write_immutable(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError(f"Immutable archive object changed: {path}") from None
    finally:
        temporary_path.unlink(missing_ok=True)


class BarArchive:
    """Local filesystem adapter for an S3-style immutable raw and normalized bar layout."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Archive path escapes root")
        return path

    def manifest(self, archive_id: str) -> BarManifest:
        if len(archive_id) != 64 or any(
            character not in "0123456789abcdef" for character in archive_id
        ):
            raise ValueError("Invalid archive id")
        data = json.loads(self._path(f"manifests/{archive_id}.json").read_text(encoding="utf-8"))
        for field in ("ingested_at", "first_observed_at", "last_observed_at"):
            data[field] = datetime.fromisoformat(data[field])
        manifest = BarManifest(**data)
        expected_id = hashlib.sha256(
            json.dumps([manifest.source_id, manifest.source_sha256]).encode()
        ).hexdigest()
        if manifest.archive_id != archive_id or expected_id != archive_id:
            raise ValueError("Archive manifest identity mismatch")
        if manifest.schema_version != 1:
            raise ValueError("Archive manifest schema version mismatch")
        if (
            manifest.raw_path
            != f"raw/sha256/{manifest.source_sha256[:2]}/{manifest.source_sha256}.csv"
            or manifest.parquet_path
            != f"normalized/bars/schema=v1/{manifest.parquet_sha256}.parquet"
        ):
            raise ValueError("Archive manifest object path mismatch")
        return manifest

    def ingest_csv(self, path: Path, *, source_id: str, ingested_at: datetime) -> BarManifest:
        _require_aware(ingested_at)
        if not source_id.strip():
            raise ValueError("Source id is required")
        raw = path.read_bytes()
        dataset = import_bars_csv_bytes(raw)
        if not dataset.bars:
            raise ValueError("Cannot archive an empty bar dataset")
        source_sha = hashlib.sha256(raw).hexdigest()
        archive_id = hashlib.sha256(json.dumps([source_id, source_sha]).encode()).hexdigest()
        manifest_path = self._path(f"manifests/{archive_id}.json")
        if manifest_path.exists():
            prior = self.manifest(archive_id)
            self.replay(archive_id)
            return prior

        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as error:
            raise ImportError('Parquet archive requires the "data" package extra') from error
        schema = pa.schema(
            [
                ("instrument_id", pa.string()),
                ("observed_at", pa.timestamp("us", tz="UTC")),
                *(
                    (name, pa.decimal128(38, 18))
                    for name in ("open", "high", "low", "close", "volume")
                ),
            ]
        )
        rows = [
            {
                "instrument_id": bar.instrument_id,
                "observed_at": bar.observed_at,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in dataset.bars
        ]
        table = pa.Table.from_pylist(rows, schema=schema)
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink, compression="zstd")
        parquet = sink.getvalue().to_pybytes()
        parquet_sha = hashlib.sha256(parquet).hexdigest()
        raw_path = f"raw/sha256/{source_sha[:2]}/{source_sha}.csv"
        parquet_path = f"normalized/bars/schema=v1/{parquet_sha}.parquet"
        manifest = BarManifest(
            archive_id=archive_id,
            source_id=source_id,
            source_sha256=source_sha,
            dataset_fingerprint=dataset.fingerprint,
            parquet_sha256=parquet_sha,
            raw_path=raw_path,
            parquet_path=parquet_path,
            row_count=len(dataset.bars),
            duplicate_rows=dataset.duplicate_rows,
            schema_version=1,
            ingested_at=ingested_at.astimezone(timezone.utc),
            first_observed_at=dataset.bars[0].observed_at,
            last_observed_at=dataset.bars[-1].observed_at,
        )
        encoded_manifest = json.dumps(
            {
                **manifest.__dict__,
                "ingested_at": manifest.ingested_at.isoformat(),
                "first_observed_at": manifest.first_observed_at.isoformat(),
                "last_observed_at": manifest.last_observed_at.isoformat(),
            },
            sort_keys=True,
        ).encode()
        _write_immutable(self._path(raw_path), raw)
        _write_immutable(self._path(parquet_path), parquet)
        _write_immutable(manifest_path, encoded_manifest)
        return manifest

    def replay(self, archive_id: str) -> BarDataset:
        manifest = self.manifest(archive_id)
        raw = self._path(manifest.raw_path).read_bytes()
        parquet_path = self._path(manifest.parquet_path)
        parquet = parquet_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest.source_sha256:
            raise ValueError("Raw source checksum mismatch")
        if hashlib.sha256(parquet).hexdigest() != manifest.parquet_sha256:
            raise ValueError("Parquet checksum mismatch")
        try:
            import pyarrow.parquet as pq
        except ImportError as error:
            raise ImportError('Parquet replay requires the "data" package extra') from error
        rows = pq.read_table(parquet_path).to_pylist()
        dataset = BarDataset(Bar(**row) for row in rows)
        if (
            dataset.fingerprint != manifest.dataset_fingerprint
            or len(dataset.bars) != manifest.row_count
        ):
            raise ValueError("Normalized dataset fingerprint mismatch")
        raw_dataset = import_bars_csv_bytes(raw)
        if raw_dataset.fingerprint != manifest.dataset_fingerprint:
            raise ValueError("Raw source and normalized dataset disagree")
        if (
            raw_dataset.duplicate_rows != manifest.duplicate_rows
            or raw_dataset.bars[0].observed_at != manifest.first_observed_at
            or raw_dataset.bars[-1].observed_at != manifest.last_observed_at
        ):
            raise ValueError("Archive manifest quality metadata mismatch")
        return dataset

    def health(self, archive_id: str, *, now: datetime, max_age: timedelta) -> DataHealth:
        _require_aware(now)
        if max_age.total_seconds() < 0:
            raise ValueError("Maximum age cannot be negative")
        manifest = self.manifest(archive_id)
        dataset = self.replay(archive_id)
        age = (now - dataset.bars[-1].observed_at).total_seconds()
        return DataHealth(
            fresh=0 <= age <= max_age.total_seconds(),
            age_seconds=age,
            row_count=manifest.row_count,
            duplicate_rows=manifest.duplicate_rows,
        )
