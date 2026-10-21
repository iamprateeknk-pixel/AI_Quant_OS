from datetime import datetime, timedelta, timezone
import json

import pytest

from ai_quant_os import data_archive


NOW = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)
CSV = (
    "instrument_id,observed_at,open,high,low,close,volume\n"
    "EURUSD,2026-01-01T00:00:00+00:00,1.09,1.11,1.08,1.10,100\n"
    "EURUSD,2026-01-01T00:01:00+00:00,1.10,1.12,1.09,1.11,200\n"
    "EURUSD,2026-01-01T00:01:00+00:00,1.10,1.12,1.09,1.11,200\n"
)


def test_archive_preserves_raw_bytes_and_replays_normalized_parquet(tmp_path) -> None:
    source = tmp_path / "feed.csv"
    source.write_bytes(CSV.encode())
    archive = data_archive.BarArchive(tmp_path / "archive")

    manifest = archive.ingest_csv(source, source_id="fixture:feed", ingested_at=NOW)

    assert manifest.schema_version == 1
    assert manifest.row_count == 2
    assert manifest.duplicate_rows == 1
    assert (archive.root / manifest.raw_path).read_bytes() == CSV.encode()
    assert (archive.root / manifest.parquet_path).read_bytes()[:4] == b"PAR1"
    assert archive.replay(manifest.archive_id).fingerprint == manifest.dataset_fingerprint
    assert (
        archive.ingest_csv(source, source_id="fixture:feed", ingested_at=NOW + timedelta(1))
        == manifest
    )


def test_immutable_publish_uses_no_replace_windows_rename_when_links_are_denied(
    tmp_path, monkeypatch
) -> None:
    if data_archive.os.name != "nt":
        pytest.skip("Windows rename fallback is platform-specific")
    target = tmp_path / "objects" / "immutable.bin"
    original_link = data_archive.os.link

    def denied_link(source, destination):
        error = PermissionError(13, "hard links denied")
        error.winerror = 5
        raise error

    monkeypatch.setattr(data_archive.os, "link", denied_link)
    data_archive._write_immutable(target, b"same")
    assert target.read_bytes() == b"same"

    # Existing identical objects remain idempotent, and conflicting bytes never
    # replace the previously published object.
    data_archive._write_immutable(target, b"same")
    with pytest.raises(ValueError, match="Immutable archive object changed"):
        data_archive._write_immutable(target, b"different")
    assert target.read_bytes() == b"same"
    monkeypatch.setattr(data_archive.os, "link", original_link)


def test_archive_replay_rejects_modified_parquet(tmp_path) -> None:
    source = tmp_path / "feed.csv"
    source.write_text(CSV, encoding="utf-8")
    archive = data_archive.BarArchive(tmp_path / "archive")
    manifest = archive.ingest_csv(source, source_id="fixture:feed", ingested_at=NOW)
    (archive.root / manifest.parquet_path).write_bytes(b"corrupt")

    with pytest.raises(ValueError, match="checksum"):
        archive.replay(manifest.archive_id)
    with pytest.raises(ValueError, match="checksum"):
        archive.health(manifest.archive_id, now=NOW, max_age=timedelta(minutes=2))


def test_archive_rejects_manifest_source_identity_change(tmp_path) -> None:
    source = tmp_path / "feed.csv"
    source.write_text(CSV, encoding="utf-8")
    archive = data_archive.BarArchive(tmp_path / "archive")
    manifest = archive.ingest_csv(source, source_id="fixture:feed", ingested_at=NOW)
    path = archive.root / "manifests" / f"{manifest.archive_id}.json"
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["source_id"] = "fixture:other"
    path.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(ValueError, match="identity"):
        archive.replay(manifest.archive_id)


def test_archive_rejects_tampered_quality_metadata(tmp_path) -> None:
    source = tmp_path / "feed.csv"
    source.write_text(CSV, encoding="utf-8")
    archive = data_archive.BarArchive(tmp_path / "archive")
    manifest = archive.ingest_csv(source, source_id="fixture:feed", ingested_at=NOW)
    path = archive.root / "manifests" / f"{manifest.archive_id}.json"
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["duplicate_rows"] = 0
    changed["last_observed_at"] = NOW.isoformat()
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="metadata"):
        archive.replay(manifest.archive_id)


def test_archive_health_detects_staleness_and_future_timestamps(tmp_path) -> None:
    source = tmp_path / "feed.csv"
    source.write_text(CSV, encoding="utf-8")
    archive = data_archive.BarArchive(tmp_path / "archive")
    manifest = archive.ingest_csv(source, source_id="fixture:feed", ingested_at=NOW)

    fresh = archive.health(manifest.archive_id, now=NOW, max_age=timedelta(minutes=2))
    assert fresh.fresh and fresh.duplicate_rows == 1
    stale = archive.health(
        manifest.archive_id, now=NOW + timedelta(minutes=5), max_age=timedelta(minutes=2)
    )
    assert not stale.fresh and stale.age_seconds == 360
    future = archive.health(
        manifest.archive_id, now=NOW - timedelta(minutes=2), max_age=timedelta(minutes=2)
    )
    assert not future.fresh and future.age_seconds == -60


def test_conflicting_duplicate_does_not_publish_archive(tmp_path) -> None:
    source = tmp_path / "feed.csv"
    source.write_text(
        CSV + "EURUSD,2026-01-01T00:01:00+00:00,1.10,1.12,1.09,1.115,200\n",
        encoding="utf-8",
    )
    archive = data_archive.BarArchive(tmp_path / "archive")
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        archive.ingest_csv(source, source_id="fixture:feed", ingested_at=NOW)
    assert not list(archive.root.rglob("*.json"))


def test_data_cli_ingests_replays_and_reports_staleness(tmp_path, capsys) -> None:
    from ai_quant_os.data_cli import main

    source = tmp_path / "feed.csv"
    source.write_text(CSV, encoding="utf-8")
    root = tmp_path / "archive"
    assert (
        main(
            [
                "ingest",
                str(source),
                "--root",
                str(root),
                "--source-id",
                "fixture:feed",
                "--at",
                NOW.isoformat(),
            ]
        )
        == 0
    )
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["row_count"] == 2
    assert main(["replay", manifest["archive_id"], "--root", str(root)]) == 0
    assert (
        json.loads(capsys.readouterr().out)["dataset_fingerprint"]
        == manifest["dataset_fingerprint"]
    )
    assert (
        main(
            [
                "health",
                manifest["archive_id"],
                "--root",
                str(root),
                "--now",
                (NOW + timedelta(minutes=5)).isoformat(),
                "--max-age-seconds",
                "120",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["fresh"] is False
