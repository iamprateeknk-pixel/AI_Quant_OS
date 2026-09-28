import os
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import uuid4

import boto3
import pytest

from ai_quant_os.data_archive import BarArchive
from ai_quant_os.s3_archive import S3ArchiveMirror
from ai_quant_os.tick_archive import TickArchive


@pytest.fixture
def mirror():
    endpoint = os.getenv("P02_TEST_S3_ENDPOINT")
    if not endpoint:
        pytest.skip("Set P02_TEST_S3_ENDPOINT to run S3 HTTP integration tests")
    if urlparse(endpoint).hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("S3 HTTP integration tests require a loopback endpoint")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id=os.environ["P02_TEST_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["P02_TEST_S3_SECRET_KEY"],
    )
    return S3ArchiveMirror(
        client, bucket=os.environ["P02_TEST_S3_BUCKET"], prefix=f"integration/{uuid4()}"
    )


@pytest.mark.parametrize("kind", ["bars", "ticks"])
def test_http_upload_restore_and_replay(mirror, tmp_path, kind) -> None:
    source = tmp_path / f"{kind}.csv"
    if kind == "bars":
        source.write_text(
            "instrument_id,observed_at,open,high,low,close,volume\n"
            "EURUSD,2026-01-01T00:00:00+00:00,1.09,1.11,1.08,1.10,100\n",
            encoding="utf-8",
        )
        archive_type = BarArchive
        source_id = "fixture:bars"
    else:
        source.write_text(
            "instrument_id,source_id,sequence,observed_at,bid,ask,bid_size,ask_size\n"
            "EURUSD,fixture:ticks,1,2026-01-01T00:00:00+00:00,1.09,1.10,100,200\n",
            encoding="utf-8",
        )
        archive_type = TickArchive
        source_id = "fixture:ticks"
    archive = archive_type(tmp_path / "source")
    manifest = archive.ingest_csv(
        source, source_id=source_id, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    if kind == "bars":
        mirror.publish_bar(archive, manifest.archive_id)
        restored = mirror.restore_bar(manifest.archive_id, tmp_path / "restored")
    else:
        mirror.publish_tick(archive, manifest.archive_id)
        restored = mirror.restore_tick(manifest.archive_id, tmp_path / "restored")
    assert restored == manifest
    assert archive_type(tmp_path / "restored").replay(manifest.archive_id).fingerprint == (
        manifest.dataset_fingerprint
    )
