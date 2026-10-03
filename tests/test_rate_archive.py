from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3

import pytest

from test_policy_rates import NOW, recipe


def test_atomic_immutable_raw_capture_and_restart_replay(tmp_path):
    from ai_quant_os.rate_archive import RateArchive

    path = tmp_path / "rates.sqlite3"
    with RateArchive(path) as archive:
        first = archive.publish(recipe(), received_at=NOW, capture_mode="LOCAL_CSV_IMPORT")
        assert archive.publish(recipe(), received_at=NOW, capture_mode="LOCAL_CSV_IMPORT") == first
        assert archive.raw(first.capture_ref) == recipe()
    with RateArchive(path) as archive:
        assert archive.get(first.capture_ref) == first and archive.list() == (first,)
        assert archive.get("rate-capture:" + "0" * 64) is None


@pytest.mark.parametrize(
    "fault", ["raw", "normalized", "deleted", "audit", "missing_head_rows", "missing_head_table"]
)
def test_altered_or_missing_archive_integrity_fails_closed(tmp_path, fault):
    from ai_quant_os.rate_archive import RateArchive

    path = tmp_path / "rates.sqlite3"
    with RateArchive(path) as archive:
        archive.publish(recipe(), received_at=NOW, capture_mode="LOCAL_CSV_IMPORT")
    with sqlite3.connect(path) as conn:
        if fault == "raw":
            conn.execute("UPDATE rate_captures SET raw_csv=?", (recipe().replace(b"2.5", b"3.5"),))
        elif fault == "normalized":
            conn.execute("UPDATE rate_captures SET capture_json=replace(capture_json,'2.5','3.5')")
        elif fault == "deleted":
            conn.execute("DELETE FROM rate_captures")
        elif fault == "audit":
            conn.execute("UPDATE audit_events SET event_json='{}'")
        elif fault == "missing_head_rows":
            conn.execute("DELETE FROM audit_head")
            conn.execute("DELETE FROM audit_chain")
        else:
            conn.execute("DROP TABLE audit_head")
    with pytest.raises(ValueError):
        RateArchive(path)


def test_capacity_backdated_and_invalid_publication_cannot_change_prior_capture(tmp_path):
    from ai_quant_os.rate_archive import RateArchive

    path = tmp_path / "rates.sqlite3"
    with RateArchive(path, max_captures=1) as archive:
        first = archive.publish(recipe(), received_at=NOW, capture_mode="LOCAL_CSV_IMPORT")
        with pytest.raises(ValueError):
            archive.publish(
                recipe(), received_at=NOW + timedelta(seconds=1), capture_mode="LOCAL_CSV_IMPORT"
            )
        with pytest.raises(ValueError):
            archive.publish(b"invalid", received_at=NOW, capture_mode="LOCAL_CSV_IMPORT")
        assert archive.list() == (first,)
    with RateArchive(path) as archive:
        with pytest.raises(ValueError):
            archive.publish(
                recipe(), received_at=NOW - timedelta(seconds=1), capture_mode="LOCAL_CSV_IMPORT"
            )
        assert archive.list() == (first,)


def test_concurrent_capture_commits_one_raw_and_journal_record(tmp_path):
    from ai_quant_os.rate_archive import RateArchive

    path = tmp_path / "rates.sqlite3"
    with RateArchive(path):
        pass

    def publish(_):
        with RateArchive(path) as archive:
            return archive.publish(
                recipe(), received_at=NOW, capture_mode="LOCAL_CSV_IMPORT"
            ).capture_ref

    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(pool.map(publish, range(8)))
    assert len(set(refs)) == 1
    with RateArchive(path) as archive:
        assert len(archive.list()) == 1


@pytest.mark.parametrize(
    "reference", ["../escape", "rate-capture:" + "x" * 64, "rate-capture:" + "0" * 65]
)
def test_invalid_archive_reference_is_refused(tmp_path, reference):
    from ai_quant_os.rate_archive import RateArchive

    with RateArchive(tmp_path / "rates.sqlite3") as archive:
        with pytest.raises(ValueError):
            archive.get(reference)
