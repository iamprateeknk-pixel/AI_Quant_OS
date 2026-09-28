from datetime import datetime, timedelta, timezone

import pytest

from ai_quant_os.tick_archive import TickArchive
from ai_quant_os.data_cli import main


NOW = datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc)
CSV = (
    "instrument_id,source_id,sequence,observed_at,bid,ask,bid_size,ask_size\n"
    "EURUSD,fixture:tick,1,2026-01-01T00:00:00+00:00,1.09,1.10,100,200\n"
    "EURUSD,fixture:tick,2,2026-01-01T00:00:01+00:00,1.10,1.11,101,201\n"
    "EURUSD,fixture:tick,2,2026-01-01T00:00:01+00:00,1.10,1.11,101,201\n"
)


def test_tick_archive_preserves_raw_and_verifies_replay(tmp_path) -> None:
    source = tmp_path / "ticks.csv"
    source.write_bytes(CSV.encode())
    archive = TickArchive(tmp_path / "archive")
    manifest = archive.ingest_csv(source, source_id="fixture:tick", ingested_at=NOW)

    assert manifest.row_count == 2
    assert manifest.duplicate_rows == 1
    assert (archive.root / manifest.raw_path).read_bytes() == CSV.encode()
    assert (archive.root / manifest.parquet_path).read_bytes()[:4] == b"PAR1"
    assert archive.replay(manifest.archive_id).fingerprint == manifest.dataset_fingerprint
    assert (
        archive.ingest_csv(source, source_id="fixture:tick", ingested_at=NOW + timedelta(days=1))
        == manifest
    )


def test_tick_archive_rejects_corrupt_normalized_file(tmp_path) -> None:
    source = tmp_path / "ticks.csv"
    source.write_text(CSV, encoding="utf-8")
    archive = TickArchive(tmp_path / "archive")
    manifest = archive.ingest_csv(source, source_id="fixture:tick", ingested_at=NOW)
    (archive.root / manifest.parquet_path).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        archive.replay(manifest.archive_id)


def test_tick_archive_detects_staleness_and_future_ticks(tmp_path) -> None:
    source = tmp_path / "ticks.csv"
    source.write_text(CSV, encoding="utf-8")
    archive = TickArchive(tmp_path / "archive")
    manifest = archive.ingest_csv(source, source_id="fixture:tick", ingested_at=NOW)
    fresh = archive.health(manifest.archive_id, now=NOW, max_age=timedelta(seconds=1))
    stale = archive.health(
        manifest.archive_id, now=NOW + timedelta(seconds=2), max_age=timedelta(seconds=1)
    )
    future = archive.health(
        manifest.archive_id, now=NOW - timedelta(seconds=2), max_age=timedelta(seconds=1)
    )
    assert fresh.fresh and fresh.age_seconds == 1
    assert not stale.fresh and stale.age_seconds == 3
    assert not future.fresh and future.age_seconds == -1


def test_tick_archive_rejects_mismatched_source_and_conflicts_before_publish(tmp_path) -> None:
    source = tmp_path / "ticks.csv"
    source.write_text(CSV, encoding="utf-8")
    archive = TickArchive(tmp_path / "archive")
    with pytest.raises(ValueError, match="source"):
        archive.ingest_csv(source, source_id="other", ingested_at=NOW)
    source.write_text(
        CSV + "EURUSD,fixture:tick,2,2026-01-01T00:00:01+00:00,1.09,1.11,101,201\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Conflicting duplicate tick"):
        archive.ingest_csv(source, source_id="fixture:tick", ingested_at=NOW)
    assert not list((archive.root / "manifests").rglob("*.json"))


def test_tick_cli_ingests_replays_and_reports_health(tmp_path, capsys) -> None:
    source = tmp_path / "ticks.csv"
    source.write_text(CSV, encoding="utf-8")
    root = tmp_path / "archive"
    assert (
        main(
            [
                "ingest-ticks",
                str(source),
                "--root",
                str(root),
                "--source-id",
                "fixture:tick",
                "--at",
                NOW.isoformat(),
            ]
        )
        == 0
    )
    import json

    manifest = json.loads(capsys.readouterr().out)
    assert manifest["row_count"] == 2
    assert main(["replay-ticks", manifest["archive_id"], "--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["row_count"] == 2
    assert (
        main(
            [
                "health-ticks",
                manifest["archive_id"],
                "--root",
                str(root),
                "--now",
                (NOW + timedelta(minutes=1)).isoformat(),
                "--max-age-seconds",
                "5",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["fresh"] is False
