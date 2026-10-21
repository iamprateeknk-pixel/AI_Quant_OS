from datetime import datetime, timezone
import sqlite3

import pytest

from ai_quant_os.research_evidence_catalog import (
    HoldoutEvidenceStore,
    ResearchEvidenceCatalog,
    copy_holdout_evidence_store,
)
from ai_quant_os.holdout_validator_service import _open_service_holdout_store


NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
RAW_HOLDOUT = (
    b"instrument_id,observed_at,open,high,low,close,volume\n"
    b"EURUSD,2026-10-01T00:00:00Z,1.1,1.1,1.1,1.1,1\n"
    b"EURUSD,2026-10-01T00:01:00Z,1.2,1.2,1.2,1.2,1\n"
)


def test_service_holdout_store_reads_verified_holdout_and_is_read_only(tmp_path):
    catalog_path = tmp_path / "research-data.sqlite3"
    with ResearchEvidenceCatalog(catalog_path) as catalog:
        receipt = catalog.publish(
            RAW_HOLDOUT,
            source_id="sealed",
            resource_class="HOLDOUT",
            received_at=NOW,
        )
    holdout_path = catalog_path.with_name("research-data.holdout.sqlite3")

    with HoldoutEvidenceStore(holdout_path) as store:
        assert store.list() == (receipt,)
        assert store.get(receipt.dataset_ref) == (receipt, RAW_HOLDOUT)
        with pytest.raises(sqlite3.OperationalError):
            store._db.execute("DELETE FROM research_datasets")


def test_service_holdout_store_rejects_corrupt_raw_bytes(tmp_path):
    catalog_path = tmp_path / "research-data.sqlite3"
    with ResearchEvidenceCatalog(catalog_path) as catalog:
        receipt = catalog.publish(
            RAW_HOLDOUT,
            source_id="sealed",
            resource_class="HOLDOUT",
            received_at=NOW,
        )
    holdout_path = catalog_path.with_name("research-data.holdout.sqlite3")
    with sqlite3.connect(holdout_path) as connection:
        connection.execute(
            "UPDATE research_datasets SET raw_csv=? WHERE dataset_ref=?",
            (RAW_HOLDOUT + b"tamper", receipt.dataset_ref),
        )

    with pytest.raises(ValueError):
        HoldoutEvidenceStore(holdout_path)


def test_validator_service_opens_only_its_holdout_catalog(tmp_path):
    data_dir = tmp_path / "service-data"
    data_dir.mkdir()
    catalog_path = data_dir / "research-data.sqlite3"
    with ResearchEvidenceCatalog(catalog_path) as catalog:
        receipt = catalog.publish(
            RAW_HOLDOUT,
            source_id="sealed",
            resource_class="HOLDOUT",
            received_at=NOW,
        )

    with _open_service_holdout_store(data_dir) as store:
        assert store.list() == (receipt,)


def test_holdout_store_staging_copies_only_a_verified_snapshot_without_overwrite(tmp_path):
    catalog_path = tmp_path / "legacy" / "research-data.sqlite3"
    catalog_path.parent.mkdir()
    with ResearchEvidenceCatalog(catalog_path) as catalog:
        receipt = catalog.publish(
            RAW_HOLDOUT,
            source_id="sealed",
            resource_class="HOLDOUT",
            received_at=NOW,
        )
    source = catalog_path.with_name("research-data.holdout.sqlite3")
    destination_dir = tmp_path / "service-data"
    destination_dir.mkdir()
    destination = destination_dir / "research-data.holdout.sqlite3"

    migrated = copy_holdout_evidence_store(source, destination)

    assert migrated == (receipt,)
    assert source.is_file()
    with HoldoutEvidenceStore(destination) as store:
        assert store.get(receipt.dataset_ref) == (receipt, RAW_HOLDOUT)
    assert copy_holdout_evidence_store(source, destination) == (receipt,)

    destination.write_bytes(b"do not replace")
    with pytest.raises(FileExistsError):
        copy_holdout_evidence_store(source, destination)
    assert destination.read_bytes() == b"do not replace"


def test_holdout_store_staging_refuses_corrupt_source_without_creating_destination(tmp_path):
    catalog_path = tmp_path / "research-data.sqlite3"
    with ResearchEvidenceCatalog(catalog_path) as catalog:
        receipt = catalog.publish(
            RAW_HOLDOUT,
            source_id="sealed",
            resource_class="HOLDOUT",
            received_at=NOW,
        )
    source = catalog_path.with_name("research-data.holdout.sqlite3")
    with sqlite3.connect(source) as connection:
        connection.execute(
            "UPDATE research_datasets SET raw_csv=? WHERE dataset_ref=?",
            (b"tampered", receipt.dataset_ref),
        )
    destination_dir = tmp_path / "service-data"
    destination_dir.mkdir()
    destination = destination_dir / "research-data.holdout.sqlite3"

    with pytest.raises(ValueError):
        copy_holdout_evidence_store(source, destination)
    assert not destination.exists()


def test_holdout_store_staging_rejects_destination_hard_link_to_source(tmp_path):
    catalog_path = tmp_path / "research-data.sqlite3"
    with ResearchEvidenceCatalog(catalog_path) as catalog:
        catalog.publish(
            RAW_HOLDOUT,
            source_id="sealed",
            resource_class="HOLDOUT",
            received_at=NOW,
        )
    source = catalog_path.with_name("research-data.holdout.sqlite3")
    destination_dir = tmp_path / "service-data"
    destination_dir.mkdir()
    destination = destination_dir / "research-data.holdout.sqlite3"
    destination.hardlink_to(source)

    with pytest.raises(ValueError, match="separate files"):
        copy_holdout_evidence_store(source, destination)
