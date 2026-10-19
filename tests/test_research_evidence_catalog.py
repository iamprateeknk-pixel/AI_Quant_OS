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


def test_operator_preparation_reads_exact_classified_pair_without_training_leak(tmp_path):
    with catalog(tmp_path / "research.sqlite3") as store:
        training = publish(store, raw_bars())
        holdout = publish(store, raw_bars(minute=10), "HOLDOUT", "sealed")
        assert hasattr(store, "validation_pair"), "Operator preparation needs checked raw snapshots"
        pair = store.validation_pair(training.dataset_ref, holdout.dataset_ref)
        assert pair == ((training, raw_bars()), (holdout, raw_bars(minute=10)))
        with pytest.raises(ValueError, match="classification"):
            store.validation_pair(holdout.dataset_ref, training.dataset_ref)
        with pytest.raises(KeyError):
            store.validation_pair(training.dataset_ref, "research-data:" + "0" * 64)
        assert len(store.training(load_agent_catalog(), "A08", "0.2.0", scope="SANDBOX")) == 1


def test_training_and_holdout_bytes_are_persisted_in_separate_sqlite_files(tmp_path):
    path = tmp_path / "research-data.sqlite3"
    holdout_path = tmp_path / "research-data.holdout.sqlite3"
    with catalog(path) as store:
        training = publish(store, raw_bars(), source_id="training")
        holdout = publish(store, raw_bars(minute=10), "HOLDOUT", "holdout")
        assert store.list() == (training, holdout)

    with closing(sqlite3.connect(path)) as training_db:
        training_rows = training_db.execute(
            "SELECT dataset_ref, raw_csv FROM research_datasets"
        ).fetchall()
    with closing(sqlite3.connect(holdout_path)) as holdout_db:
        holdout_rows = holdout_db.execute(
            "SELECT dataset_ref, raw_csv FROM research_datasets"
        ).fetchall()
    assert [row[0] for row in training_rows] == [training.dataset_ref]
    assert [row[0] for row in holdout_rows] == [holdout.dataset_ref]
    assert training_rows[0][1] == raw_bars()
    assert holdout_rows[0][1] == raw_bars(minute=10)


def test_verified_legacy_mixed_catalog_moves_holdout_bytes_without_losing_receipts(tmp_path):
    from ai_quant_os.audit_chain import AuditChain
    from ai_quant_os.research_evidence_catalog import ResearchEvidenceCatalog, _receipt

    path = tmp_path / "legacy.sqlite3"
    legacy_rows = (
        (raw_bars(), "training", "TRAINING"),
        (raw_bars(minute=10), "holdout", "HOLDOUT"),
    )
    with closing(sqlite3.connect(path)) as db:
        db.executescript(
            "CREATE TABLE research_datasets (dataset_ref TEXT PRIMARY KEY, raw_csv BLOB NOT NULL, receipt_json TEXT NOT NULL);"
            "CREATE TABLE audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL);"
        )
        audit = AuditChain(db)
        audit.initialize()
        expected = []
        for raw, source_id, resource_class in legacy_rows:
            receipt = _receipt(raw, source_id, resource_class, NOW)
            db.execute(
                "INSERT INTO research_datasets VALUES (?,?,?)",
                (receipt.dataset_ref, raw, receipt.model_dump_json()),
            )
            audit.append(receipt.dataset_ref, ResearchEvidenceCatalog._event(receipt))
            expected.append(receipt)
        db.commit()

    with catalog(path) as store:
        assert store.list() == tuple(expected)
        assert (
            store.training(load_agent_catalog(), "A08", "0.2.0", scope="SANDBOX")[0][0]
            == expected[0]
        )
    with closing(sqlite3.connect(path)) as training_db:
        training_rows = training_db.execute(
            "SELECT dataset_ref, raw_csv FROM research_datasets"
        ).fetchall()
    with closing(sqlite3.connect(tmp_path / "legacy.holdout.sqlite3")) as holdout_db:
        holdout_rows = holdout_db.execute(
            "SELECT dataset_ref, raw_csv FROM research_datasets"
        ).fetchall()
    assert training_rows == [(expected[0].dataset_ref, raw_bars())]
    assert holdout_rows == [(expected[1].dataset_ref, raw_bars(minute=10))]


def test_legacy_migration_resumes_after_holdout_copy_was_committed(tmp_path):
    from ai_quant_os.audit_chain import AuditChain
    from ai_quant_os.research_evidence_catalog import ResearchEvidenceCatalog, _receipt

    path = tmp_path / "legacy.sqlite3"
    holdout_path = tmp_path / "legacy.holdout.sqlite3"
    training_raw, holdout_raw = raw_bars(), raw_bars(minute=10)
    training = _receipt(training_raw, "training", "TRAINING", NOW)
    holdout = _receipt(holdout_raw, "holdout", "HOLDOUT", NOW)
    with closing(sqlite3.connect(path)) as db:
        db.executescript(
            "CREATE TABLE research_datasets (dataset_ref TEXT PRIMARY KEY, raw_csv BLOB NOT NULL, receipt_json TEXT NOT NULL);"
            "CREATE TABLE audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL);"
        )
        audit = AuditChain(db)
        audit.initialize()
        for receipt, raw in ((training, training_raw), (holdout, holdout_raw)):
            db.execute(
                "INSERT INTO research_datasets VALUES (?,?,?)",
                (receipt.dataset_ref, raw, receipt.model_dump_json()),
            )
            audit.append(receipt.dataset_ref, ResearchEvidenceCatalog._event(receipt))
        db.commit()

    with closing(sqlite3.connect(holdout_path)) as db:
        db.executescript(
            "CREATE TABLE research_datasets (dataset_ref TEXT PRIMARY KEY, raw_csv BLOB NOT NULL, receipt_json TEXT NOT NULL);"
            "CREATE TABLE audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL);"
        )
        audit = AuditChain(db)
        audit.initialize()
        db.execute(
            "INSERT INTO research_datasets VALUES (?,?,?)",
            (holdout.dataset_ref, holdout_raw, holdout.model_dump_json()),
        )
        audit.append(holdout.dataset_ref, ResearchEvidenceCatalog._event(holdout))
        db.commit()

    with catalog(path) as store:
        assert store.list() == (training, holdout)
    with closing(sqlite3.connect(path)) as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM research_datasets WHERE dataset_ref=?", (holdout.dataset_ref,)
            ).fetchone()[0]
            == 0
        )


def test_training_reader_does_not_load_holdout_csv_bytes(tmp_path):
    with catalog(tmp_path / "research.sqlite3") as store:
        training = publish(store, raw_bars())
        holdout = publish(store, raw_bars(minute=10), "HOLDOUT", "sealed")
        store._holdout_db.execute(
            "UPDATE research_datasets SET raw_csv=? WHERE dataset_ref=?",
            (b"corrupt sealed bytes", holdout.dataset_ref),
        )
        rows = store.training(load_agent_catalog(), "A08", "0.2.0", scope="SANDBOX")
        assert rows[0][0] == training
        with pytest.raises(ValueError, match="research CSV|source identity"):
            store.validation_pair(training.dataset_ref, holdout.dataset_ref)


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

        def _verified_receipts(self, *args, **kwargs):
            rows = super()._verified_receipts(*args, **kwargs)
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
