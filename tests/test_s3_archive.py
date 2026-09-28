from datetime import datetime, timezone

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from ai_quant_os.data_archive import BarArchive
from ai_quant_os.data_cli import main
from ai_quant_os.s3_archive import S3ArchiveMirror
from ai_quant_os.tick_archive import TickArchive


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
BAR_CSV = (
    "instrument_id,observed_at,open,high,low,close,volume\n"
    "EURUSD,2026-01-01T00:00:00+00:00,1.09,1.11,1.08,1.10,100\n"
)
TICK_CSV = (
    "instrument_id,source_id,sequence,observed_at,bid,ask,bid_size,ask_size\n"
    "EURUSD,fixture:ticks,1,2026-01-01T00:00:00+00:00,1.09,1.10,100,200\n"
)


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        client.create_bucket(Bucket="quant-test")
        yield client


def test_bar_archive_upload_is_idempotent_and_restores(s3, tmp_path) -> None:
    source = tmp_path / "bars.csv"
    source.write_text(BAR_CSV, encoding="utf-8")
    archive = BarArchive(tmp_path / "source")
    manifest = archive.ingest_csv(source, source_id="fixture:bars", ingested_at=NOW)
    mirror = S3ArchiveMirror(s3, bucket="quant-test", prefix="datasets")

    mirror.publish_bar(archive, manifest.archive_id)
    mirror.publish_bar(archive, manifest.archive_id)
    restored = mirror.restore_bar(manifest.archive_id, tmp_path / "restored")

    assert restored == manifest
    assert BarArchive(tmp_path / "restored").replay(manifest.archive_id).fingerprint == (
        manifest.dataset_fingerprint
    )


def test_tick_archive_upload_and_restore(s3, tmp_path) -> None:
    source = tmp_path / "ticks.csv"
    source.write_text(TICK_CSV, encoding="utf-8")
    archive = TickArchive(tmp_path / "source")
    manifest = archive.ingest_csv(source, source_id="fixture:ticks", ingested_at=NOW)
    mirror = S3ArchiveMirror(s3, bucket="quant-test", prefix="datasets")

    mirror.publish_tick(archive, manifest.archive_id)
    restored = mirror.restore_tick(manifest.archive_id, tmp_path / "restored")

    assert restored == manifest
    assert TickArchive(tmp_path / "restored").replay(manifest.archive_id).fingerprint == (
        manifest.dataset_fingerprint
    )


def test_existing_remote_object_is_never_replaced(s3, tmp_path) -> None:
    source = tmp_path / "bars.csv"
    source.write_text(BAR_CSV, encoding="utf-8")
    archive = BarArchive(tmp_path / "source")
    manifest = archive.ingest_csv(source, source_id="fixture:bars", ingested_at=NOW)
    mirror = S3ArchiveMirror(s3, bucket="quant-test", prefix="datasets")
    s3.put_object(Bucket="quant-test", Key=f"datasets/{manifest.raw_path}", Body=b"tampered")

    with pytest.raises(ValueError, match="Immutable"):
        mirror.publish_bar(archive, manifest.archive_id)
    remote = s3.get_object(Bucket="quant-test", Key=f"datasets/{manifest.raw_path}")
    assert remote["Body"].read() == b"tampered"
    with pytest.raises(ClientError):
        s3.get_object(Bucket="quant-test", Key=f"datasets/manifests/{manifest.archive_id}.json")


def test_restore_rejects_tampered_remote_bytes(s3, tmp_path) -> None:
    source = tmp_path / "bars.csv"
    source.write_text(BAR_CSV, encoding="utf-8")
    archive = BarArchive(tmp_path / "source")
    manifest = archive.ingest_csv(source, source_id="fixture:bars", ingested_at=NOW)
    mirror = S3ArchiveMirror(s3, bucket="quant-test", prefix="datasets")
    mirror.publish_bar(archive, manifest.archive_id)
    s3.put_object(Bucket="quant-test", Key=f"datasets/{manifest.raw_path}", Body=b"tampered")

    with pytest.raises(ValueError, match="checksum"):
        mirror.restore_bar(manifest.archive_id, tmp_path / "restored")


def test_object_prefix_cannot_escape_namespace(s3) -> None:
    with pytest.raises(ValueError, match="prefix"):
        S3ArchiveMirror(s3, bucket="quant-test", prefix="../other")


def test_cli_mirrors_and_restores_archive(s3, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    source = tmp_path / "bars.csv"
    source.write_text(BAR_CSV, encoding="utf-8")
    root = tmp_path / "source"
    manifest = BarArchive(root).ingest_csv(source, source_id="fixture:bars", ingested_at=NOW)
    assert (
        main(
            [
                "mirror",
                manifest.archive_id,
                "--kind",
                "bars",
                "--root",
                str(root),
                "--bucket",
                "quant-test",
                "--prefix",
                "datasets",
            ]
        )
        == 0
    )
    capsys.readouterr()
    restored_root = tmp_path / "restored"
    assert (
        main(
            [
                "restore",
                manifest.archive_id,
                "--kind",
                "bars",
                "--root",
                str(restored_root),
                "--bucket",
                "quant-test",
                "--prefix",
                "datasets",
            ]
        )
        == 0
    )
    assert BarArchive(restored_root).replay(manifest.archive_id).fingerprint == (
        manifest.dataset_fingerprint
    )
