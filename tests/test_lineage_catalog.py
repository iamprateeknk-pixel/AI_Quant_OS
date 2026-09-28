import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from ai_quant_os.data_archive import BarArchive
from ai_quant_os.data_cli import main
from ai_quant_os.lineage_catalog import PostgresLineageCatalog
from ai_quant_os.tick_archive import TickArchive


@pytest.fixture
def catalog():
    dsn = os.getenv("P02_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set P02_TEST_DATABASE_URL to run PostgreSQL integration tests")
    return PostgresLineageCatalog(dsn)


def test_archive_registration_is_idempotent_and_conflicts_fail(catalog, tmp_path) -> None:
    source = tmp_path / "bars.csv"
    source.write_text(
        "instrument_id,observed_at,open,high,low,close,volume\n"
        "EURUSD,2026-01-01T00:00:00+00:00,1.09,1.11,1.08,1.10,100\n",
        encoding="utf-8",
    )
    archive = BarArchive(tmp_path / "archive")
    source_id = f"fixture:{uuid4()}"
    manifest = archive.ingest_csv(
        source, source_id=source_id, ingested_at=datetime.now(timezone.utc)
    )

    catalog.register_source(manifest.source_id, kind="CSV")
    catalog.record_archive(manifest)
    catalog.record_archive(manifest)
    assert catalog.get_archive(manifest.archive_id) == manifest

    with pytest.raises(ValueError, match="conflict"):
        catalog.record_archive(replace(manifest, row_count=manifest.row_count + 1))
    assert catalog.get_archive(manifest.archive_id) == manifest


def test_quality_observation_is_durable(catalog, tmp_path) -> None:
    source = tmp_path / "bars.csv"
    source.write_text(
        "instrument_id,observed_at,open,high,low,close,volume\n"
        "GBPUSD,2026-01-01T00:00:00+00:00,1.25,1.26,1.24,1.25,100\n",
        encoding="utf-8",
    )
    archive = BarArchive(tmp_path / "archive")
    source_id = f"fixture:{uuid4()}"
    manifest = archive.ingest_csv(
        source, source_id=source_id, ingested_at=datetime.now(timezone.utc)
    )
    catalog.register_source(manifest.source_id, kind="CSV")
    catalog.record_archive(manifest)
    checked_at = datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc)
    health = archive.health(manifest.archive_id, now=checked_at, max_age=timedelta(minutes=5))
    catalog.record_quality(manifest.archive_id, health, checked_at=checked_at, trace_id="test")
    assert catalog.latest_quality(manifest.archive_id) == (checked_at, health.age_seconds, False)


def test_cli_ingest_and_health_persist_catalog_records(
    catalog, tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.setenv("AI_QUANT_DATA_DATABASE_URL", catalog._dsn)
    source = tmp_path / "bars.csv"
    source.write_text(
        "instrument_id,observed_at,open,high,low,close,volume\n"
        "USDJPY,2026-01-01T00:00:00+00:00,140,141,139,140,100\n",
        encoding="utf-8",
    )
    root = tmp_path / "archive"
    assert (
        main(
            [
                "ingest",
                str(source),
                "--root",
                str(root),
                "--source-id",
                "fixture:cli",
                "--at",
                "2026-01-01T00:01:00+00:00",
                "--catalog",
            ]
        )
        == 0
    )
    capsys.readouterr()
    manifest = BarArchive(root).ingest_csv(
        source,
        source_id="fixture:cli",
        ingested_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
    )
    assert catalog.get_archive(manifest.archive_id) == manifest
    assert main(["replay", manifest.archive_id, "--root", str(root)]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "health",
                manifest.archive_id,
                "--root",
                str(root),
                "--now",
                "2026-01-01T00:10:00+00:00",
                "--max-age-seconds",
                "300",
                "--catalog",
                "--trace-id",
                "cli-test",
            ]
        )
        == 2
    )
    assert catalog.latest_quality(manifest.archive_id) == (
        datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc),
        600.0,
        False,
    )


def test_tick_lineage_and_quality_are_durable(catalog, tmp_path) -> None:
    source_id = f"fixture:{uuid4()}"
    source = tmp_path / "ticks.csv"
    source.write_text(
        "instrument_id,source_id,sequence,observed_at,bid,ask,bid_size,ask_size\n"
        f"EURUSD,{source_id},1,2026-01-01T00:00:00+00:00,1.09,1.10,100,200\n",
        encoding="utf-8",
    )
    archive = TickArchive(tmp_path / "archive")
    manifest = archive.ingest_csv(
        source, source_id=source_id, ingested_at=datetime.now(timezone.utc)
    )
    catalog.register_source(source_id, kind="CSV")
    catalog.record_tick_archive(manifest)
    catalog.record_tick_archive(manifest)
    assert catalog.get_tick_archive(manifest.archive_id) == manifest
    with pytest.raises(ValueError, match="conflict"):
        catalog.record_tick_archive(replace(manifest, row_count=manifest.row_count + 1))
    checked_at = datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc)
    health = archive.health(manifest.archive_id, now=checked_at, max_age=timedelta(minutes=5))
    catalog.record_tick_quality(manifest.archive_id, health, checked_at=checked_at, trace_id="tick")
    assert catalog.latest_tick_quality(manifest.archive_id) == (checked_at, 600.0, False)


def test_tick_cli_catalog_records(catalog, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("AI_QUANT_DATA_DATABASE_URL", catalog._dsn)
    source_id = f"fixture:{uuid4()}"
    source = tmp_path / "ticks.csv"
    source.write_text(
        "instrument_id,source_id,sequence,observed_at,bid,ask,bid_size,ask_size\n"
        f"EURUSD,{source_id},1,2026-01-01T00:00:00+00:00,1.09,1.10,100,200\n",
        encoding="utf-8",
    )
    root = tmp_path / "archive"
    assert (
        main(
            [
                "ingest-ticks",
                str(source),
                "--root",
                str(root),
                "--source-id",
                source_id,
                "--at",
                "2026-01-01T00:01:00+00:00",
                "--catalog",
            ]
        )
        == 0
    )
    capsys.readouterr()
    manifest = TickArchive(root).ingest_csv(
        source,
        source_id=source_id,
        ingested_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
    )
    assert catalog.get_tick_archive(manifest.archive_id) == manifest
    assert (
        main(
            [
                "health-ticks",
                manifest.archive_id,
                "--root",
                str(root),
                "--now",
                "2026-01-01T00:10:00+00:00",
                "--max-age-seconds",
                "300",
                "--catalog",
                "--trace-id",
                "tick-cli",
            ]
        )
        == 2
    )
    assert catalog.latest_tick_quality(manifest.archive_id) == (
        datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc),
        600.0,
        False,
    )
