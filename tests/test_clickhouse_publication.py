"""Opt-in HTTP integration checks for verified archive publication."""

import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from ai_quant_os.data_archive import BarArchive
from ai_quant_os.clickhouse_publication import ClickHouseArchivePublisher
from ai_quant_os.tick_archive import TickArchive


@pytest.fixture
def publisher():
    endpoint = os.getenv("P02_TEST_CLICKHOUSE_ENDPOINT")
    if not endpoint:
        pytest.skip("Set P02_TEST_CLICKHOUSE_ENDPOINT to run ClickHouse HTTP integration tests")
    return ClickHouseArchivePublisher(
        endpoint,
        user=os.environ["P02_TEST_CLICKHOUSE_USER"],
        password=os.environ["P02_TEST_CLICKHOUSE_PASSWORD"],
    )


def test_bar_archive_publication_is_verified_and_idempotent(
    publisher, tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "bars.csv"
    source.write_text(
        "instrument_id,observed_at,open,high,low,close,volume\n"
        "EURUSD,2026-01-01T00:00:00+00:00,1.09,1.11,1.08,1.10,100\n",
        encoding="utf-8",
    )
    archive = BarArchive(tmp_path / "bars")
    manifest = archive.ingest_csv(
        source,
        source_id=f"fixture:{uuid4()}",
        ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    first = publisher.publish_bars(archive, manifest.archive_id)
    second = publisher.publish_bars(archive, manifest.archive_id)
    monkeypatch.setenv("AI_QUANT_CLICKHOUSE_USER", os.environ["P02_TEST_CLICKHOUSE_USER"])
    monkeypatch.setenv("AI_QUANT_CLICKHOUSE_PASSWORD", os.environ["P02_TEST_CLICKHOUSE_PASSWORD"])
    from ai_quant_os.data_cli import main

    assert (
        main(
            [
                "publish-clickhouse",
                manifest.archive_id,
                "--root",
                str(archive.root),
                "--endpoint-url",
                os.environ["P02_TEST_CLICKHOUSE_ENDPOINT"],
            ]
        )
        == 0
    )
    cli_result = json.loads(capsys.readouterr().out)

    assert first.row_count == 1 and first.already_published is False
    assert second.row_count == 1 and second.already_published is True
    assert cli_result["already_published"] is True


def test_tick_archive_publication_is_verified_and_idempotent(publisher, tmp_path) -> None:
    source_id = f"fixture:ticks:{uuid4()}"
    source = tmp_path / "ticks.csv"
    source.write_text(
        "instrument_id,source_id,sequence,observed_at,bid,ask,bid_size,ask_size\n"
        f"EURUSD,{source_id},1,2026-01-01T00:00:00+00:00,1.09,1.10,100,200\n",
        encoding="utf-8",
    )
    archive = TickArchive(tmp_path / "ticks")
    manifest = archive.ingest_csv(
        source, source_id=source_id, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )

    first = publisher.publish_ticks(archive, manifest.archive_id)
    second = publisher.publish_ticks(archive, manifest.archive_id)

    assert first.row_count == 1 and first.already_published is False
    assert second.row_count == 1 and second.already_published is True


def test_partial_clickhouse_archive_is_not_appended_again(publisher, tmp_path) -> None:
    source = tmp_path / "bars.csv"
    source.write_text(
        "instrument_id,observed_at,open,high,low,close,volume\n"
        "EURUSD,2026-01-01T00:00:00+00:00,1.09,1.11,1.08,1.10,100\n"
        "EURUSD,2026-01-01T00:01:00+00:00,1.10,1.12,1.09,1.11,110\n",
        encoding="utf-8",
    )
    archive = BarArchive(tmp_path / "partial-bars")
    manifest = archive.ingest_csv(
        source,
        source_id=f"fixture:{uuid4()}",
        ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    publisher._request(
        "INSERT INTO quant_market.bars_v1 FORMAT JSONEachRow",
        (
            '{"instrument_id":"EURUSD","observed_at":"2026-01-01 00:00:00.000000",'
            '"open":"1.09","high":"1.11","low":"1.08","close":"1.10",'
            '"volume":"100","source_archive_id":"'
            + manifest.archive_id
            + '","ingested_at":"2026-01-01 00:00:00.000000"}\n'
        ).encode(),
    )

    with pytest.raises(RuntimeError, match="partial or duplicate archive"):
        publisher.publish_bars(archive, manifest.archive_id)
