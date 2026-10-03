import sqlite3
import pytest

from test_policy_rates import NOW, recipe


def test_deleted_all_metadata_cannot_be_initialized_as_an_empty_archive(tmp_path):
    from ai_quant_os.rate_archive import RateArchive

    path = tmp_path / "rates.sqlite3"
    with RateArchive(path) as archive:
        archive.publish(recipe(), received_at=NOW, capture_mode="LOCAL_CSV_IMPORT")
    with sqlite3.connect(path) as connection:
        for table in ("audit_chain", "audit_head", "audit_events", "rate_captures"):
            connection.execute("DROP TABLE " + table)
    with pytest.raises(ValueError, match="metadata"):
        RateArchive(path)


def test_zero_byte_existing_archive_is_refused_without_rebaselining(tmp_path):
    from ai_quant_os.rate_archive import RateArchive

    path = tmp_path / "rates.sqlite3"
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="metadata"):
        RateArchive(path)
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
        )
