import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import redis

from ai_quant_os.data_cli import main
from ai_quant_os.tick_archive import TickArchive
from ai_quant_os.valkey_cache import LatestTickCache


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def cache():
    url = os.getenv("P02_TEST_VALKEY_URL")
    if not url:
        pytest.skip("Set P02_TEST_VALKEY_URL to run Valkey integration tests")
    client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    client.ping()
    return LatestTickCache(client)


def archive_tick(tmp_path, source_id: str, sequence: int, bid: str, second: int):
    source = tmp_path / f"ticks-{sequence}-{bid}.csv"
    source.write_text(
        "instrument_id,source_id,sequence,observed_at,bid,ask,bid_size,ask_size\n"
        f"EURUSD,{source_id},{sequence},2026-01-01T00:00:{second:02d}+00:00,"
        f"{bid},1.20,100,200\n",
        encoding="utf-8",
    )
    archive = TickArchive(tmp_path / f"archive-{sequence}-{bid}")
    manifest = archive.ingest_csv(source, source_id=source_id, ingested_at=NOW)
    return archive, manifest


def test_cache_keeps_latest_verified_tick_and_rejects_conflict(cache, tmp_path) -> None:
    source_id = f"fixture:{uuid4()}"
    first_archive, first = archive_tick(tmp_path, source_id, 1, "1.09", 0)
    second_archive, second = archive_tick(tmp_path, source_id, 2, "1.10", 1)
    conflict_archive, conflict = archive_tick(tmp_path, source_id, 2, "1.11", 1)

    assert cache.publish(first_archive, first.archive_id, ttl_seconds=60) == 1
    assert cache.publish(second_archive, second.archive_id, ttl_seconds=60) == 1
    assert cache.publish(first_archive, first.archive_id, ttl_seconds=60) == 0
    with pytest.raises(ValueError, match="Conflicting"):
        cache.publish(conflict_archive, conflict.archive_id, ttl_seconds=60)

    cached = cache.get(
        "EURUSD", source_id, now=NOW + timedelta(seconds=2), max_age=timedelta(seconds=5)
    )
    assert cached is not None
    assert cached.tick.sequence == 2
    assert cached.archive_id == second.archive_id
    assert cached.dataset_fingerprint == second.dataset_fingerprint


def test_cache_rejects_stale_and_future_ticks(cache, tmp_path) -> None:
    source_id = f"fixture:{uuid4()}"
    archive, manifest = archive_tick(tmp_path, source_id, 1, "1.09", 0)
    cache.publish(archive, manifest.archive_id, ttl_seconds=60)
    assert (
        cache.get("EURUSD", source_id, now=NOW + timedelta(seconds=6), max_age=timedelta(seconds=5))
        is None
    )
    assert (
        cache.get("EURUSD", source_id, now=NOW - timedelta(seconds=1), max_age=timedelta(seconds=5))
        is None
    )


def test_cache_rejects_timestamp_regression_and_corrupt_value(cache, tmp_path) -> None:
    source_id = f"fixture:{uuid4()}"
    first_archive, first = archive_tick(tmp_path, source_id, 1, "1.09", 1)
    regressed_archive, regressed = archive_tick(tmp_path, source_id, 2, "1.10", 0)
    cache.publish(first_archive, first.archive_id, ttl_seconds=60)
    with pytest.raises(ValueError, match="older timestamp"):
        cache.publish(regressed_archive, regressed.archive_id, ttl_seconds=60)
    cache._client.set(cache._key("EURUSD", source_id), b"not-json", ex=60)
    assert (
        cache.get("EURUSD", source_id, now=NOW + timedelta(seconds=2), max_age=timedelta(seconds=5))
        is None
    )


def test_cli_populates_verified_tick_cache(cache, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("AI_QUANT_VALKEY_URL", os.environ["P02_TEST_VALKEY_URL"])
    source_id = f"fixture:{uuid4()}"
    archive, manifest = archive_tick(tmp_path, source_id, 1, "1.09", 0)
    assert (
        main(
            [
                "cache-ticks",
                manifest.archive_id,
                "--root",
                str(archive.root),
                "--ttl-seconds",
                "60",
            ]
        )
        == 0
    )
    assert '"updated": 1' in capsys.readouterr().out
    cached = cache.get(
        "EURUSD", source_id, now=NOW + timedelta(seconds=1), max_age=timedelta(seconds=5)
    )
    assert cached is not None and cached.archive_id == manifest.archive_id
