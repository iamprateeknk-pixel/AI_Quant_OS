from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import importlib.util
import sqlite3

import pytest

from ai_quant_os.agent_registry import load_agent_catalog

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


def raw_bars(*, minute=0, instrument="EURUSD", price="1.1"):
    return (
        "instrument_id,observed_at,open,high,low,close,volume\n"
        + "\n".join(
            f"{instrument},2026-10-01T00:{minute + i:02}:00Z,{price},{price},{price},{price},1"
            for i in range(3)
        )
        + "\n"
    ).encode()


def catalog(path, **options):
    assert importlib.util.find_spec("ai_quant_os.research_evidence_catalog") is not None
    from ai_quant_os.research_evidence_catalog import ResearchEvidenceCatalog

    return ResearchEvidenceCatalog(path, **options)


def publish(store, raw, resource_class="TRAINING", source_id="development"):
    return store.publish(raw, source_id=source_id, resource_class=resource_class, received_at=NOW)


def test_checked_training_reader_withholds_holdout_and_restarts_exactly(tmp_path):
    path = tmp_path / "research-data.sqlite3"
    with catalog(path) as store:
        training = publish(store, raw_bars())
        holdout = publish(store, raw_bars(minute=10), "HOLDOUT", "sealed")
        rows = store.training(load_agent_catalog(), "A08", "0.1.0", scope="SANDBOX")
        assert len(rows) == 1 and rows[0][0] == training
        assert rows[0][1].bars[0].observed_at.minute == 0
        assert all(record.dataset_ref != holdout.dataset_ref for record, _ in rows)
        assert len(store.list()) == 2
    with catalog(path) as store:
        assert store.list() == (training, holdout)
        assert publish(store, raw_bars()) == training


@pytest.mark.parametrize(
    "incoming, classification",
    [
        (raw_bars(), "HOLDOUT"),
        (raw_bars(minute=2, price="1.2"), "HOLDOUT"),
    ],
)
def test_same_or_overlapping_observations_cannot_cross_classification(
    tmp_path, incoming, classification
):
    with catalog(tmp_path / "data.sqlite3") as store:
        first = publish(store, raw_bars())
        with pytest.raises(ValueError, match="overlap"):
            publish(store, incoming, classification, "different-source")
        assert store.list() == (first,)


@pytest.mark.parametrize(
    "agent, scope, lifecycle",
    [
        ("A08", "PAPER", "SHADOW"),
        ("A01", "SANDBOX", "SHADOW"),
        ("A08", "SANDBOX", "QUARANTINED"),
    ],
)
def test_unadmitted_agent_training_reads_are_denied(tmp_path, agent, scope, lifecycle):
    registry = load_agent_catalog()
    registry.set_lifecycle(agent, "0.1.0", lifecycle)
    with catalog(tmp_path / "data.sqlite3") as store:
        publish(store, raw_bars())
        with pytest.raises(PermissionError):
            store.training(registry, agent, "0.1.0", scope=scope)


def test_concurrent_cross_class_publication_commits_only_one_label(tmp_path):
    path = tmp_path / "data.sqlite3"
    with catalog(path) as first, catalog(path) as second:

        def submit(store, label):
            try:
                return publish(store, raw_bars(), label)
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(submit, first, "TRAINING"),
                pool.submit(submit, second, "HOLDOUT"),
            ]
            results = [task.result() for task in futures]
        assert sum(record is not None for record in results) == 1
        assert len(first.list()) == len(second.list()) == 1


@pytest.mark.parametrize("damage", ["raw", "receipt", "journal", "all_metadata"])
def test_catalog_corruption_is_refused_without_rebaselining(tmp_path, damage):
    path = tmp_path / "data.sqlite3"
    with catalog(path) as store:
        publish(store, raw_bars())
    with closing(sqlite3.connect(path)) as db, db:
        if damage == "raw":
            db.execute("UPDATE research_datasets SET raw_csv=?", (b"changed",))
        elif damage == "receipt":
            db.execute("UPDATE research_datasets SET receipt_json='{}'")
        elif damage == "journal":
            db.execute("DELETE FROM audit_events")
        else:
            for table in ("research_datasets", "audit_events", "audit_chain", "audit_head"):
                db.execute(f"DROP TABLE {table}")
    with pytest.raises(ValueError):
        catalog(path)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"x" * 1048577,
        raw_bars() + raw_bars().splitlines()[1] + b"\n",
        raw_bars(instrument="UNKNOWN"),
        raw_bars().replace(b"2026-10-01", b"2027-10-01"),
    ],
    ids=["empty", "byte-overflow", "duplicate", "unknown-instrument", "future-observation"],
)
def test_invalid_or_unbounded_research_observations_are_refused(tmp_path, raw):
    with catalog(tmp_path / "data.sqlite3") as store:
        with pytest.raises(ValueError):
            publish(store, raw)
        assert store.list() == ()


def test_capacity_denial_keeps_previous_immutable_source(tmp_path):
    with catalog(tmp_path / "data.sqlite3", capacity=1) as store:
        first = publish(store, raw_bars())
        with pytest.raises(ValueError, match="capacity"):
            publish(store, raw_bars(minute=10), source_id="second")
        assert store.list() == (first,)


def test_read_permission_revocation_withholds_an_already_loaded_dataset(tmp_path):
    from ai_quant_os.research_evidence_catalog import ResearchEvidenceCatalog

    registry = load_agent_catalog()

    class RevokingCatalog(ResearchEvidenceCatalog):
        revoke = False

        def _verified(self):
            rows = super()._verified()
            if self.revoke:
                registry.set_lifecycle("A08", "0.1.0", "QUARANTINED")
            return rows

    with RevokingCatalog(tmp_path / "data.sqlite3") as store:
        publish(store, raw_bars())
        store.revoke = True
        with pytest.raises(PermissionError):
            store.training(registry, "A08", "0.1.0", scope="SANDBOX")


def test_existing_empty_catalog_file_is_refused(tmp_path):
    path = tmp_path / "data.sqlite3"
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="metadata"):
        catalog(path)


def test_identical_republication_does_not_extend_original_receipt_time(tmp_path):
    from datetime import timedelta

    with catalog(tmp_path / "data.sqlite3") as store:
        receipt = publish(store, raw_bars())
        retry = store.publish(
            raw_bars(),
            source_id="development",
            resource_class="TRAINING",
            received_at=NOW + timedelta(days=1),
        )
        assert retry == receipt and retry.received_at == NOW


def test_extra_csv_row_fields_cannot_be_silently_dropped(tmp_path):
    raw = raw_bars().replace(b",1\n", b",1,unexpected\n")
    with catalog(tmp_path / "data.sqlite3") as store:
        with pytest.raises(ValueError):
            publish(store, raw)
        assert store.list() == ()


def test_caller_decimal_precision_does_not_change_retained_dataset_identity(tmp_path):
    from decimal import Context, localcontext

    with catalog(tmp_path / "data.sqlite3") as store:
        first = publish(store, raw_bars(price="1.12345678901234567890123456789"))
        with localcontext(Context(prec=8)):
            assert store.list() == (first,)
