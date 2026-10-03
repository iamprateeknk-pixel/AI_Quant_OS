from datetime import datetime, timedelta, timezone
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def recipe(event_id="payrolls", *, observed_at=NOW):
    return {
        "observed_at": observed_at.isoformat(),
        "valid_until": (observed_at + timedelta(seconds=120)).isoformat(),
        "coverage_start": (NOW - timedelta(minutes=30)).isoformat(),
        "coverage_end": (NOW + timedelta(days=1)).isoformat(),
        "economic_calendar": [
            {
                "event_id": event_id,
                "kind": "SCHEDULED",
                "currencies": ["USD"],
                "severity": "HIGH",
                "occurs_at": (NOW + timedelta(minutes=5)).isoformat(),
                "reported_at": (NOW - timedelta(minutes=10)).isoformat(),
            }
        ],
        "event_classifications": [],
    }


def test_immutable_feed_roundtrip_latest_and_expiry_survive_restart(tmp_path):
    from ai_quant_os.event_feed import EventFeed
    from ai_quant_os.event_feed_store import EventFeedStore

    path = tmp_path / "events.sqlite3"
    feed = EventFeed.model_validate(recipe())
    with EventFeedStore(path) as store:
        assert store.put(feed, now=NOW) is True
        assert store.put(feed, now=NOW) is False
        assert store.latest() == feed
    with EventFeedStore(path) as store:
        assert store.get(feed.content_ref) == feed
        assert store.latest().valid_until < NOW + timedelta(seconds=121)


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "unknown_currency",
        "wrong_kind",
        "bad_expiry",
        "outside_coverage",
        "future_report",
        "extra_authority",
    ],
)
def test_invalid_feed_contract_is_rejected(change):
    from ai_quant_os.event_feed import EventFeed

    raw = recipe()
    if change == "duplicate":
        raw["economic_calendar"] *= 2
    elif change == "unknown_currency":
        raw["economic_calendar"][0]["currencies"] = ["XYZ"]
    elif change == "wrong_kind":
        raw["economic_calendar"][0]["kind"] = "DETECTED"
    elif change == "bad_expiry":
        raw["valid_until"] = (NOW + timedelta(days=1)).isoformat()
    elif change == "outside_coverage":
        raw["economic_calendar"][0]["occurs_at"] = (NOW + timedelta(days=2)).isoformat()
    elif change == "extra_authority":
        raw["economic_calendar"][0]["override_risk"] = True
    else:
        raw["economic_calendar"][0]["reported_at"] = (NOW + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError):
        EventFeed.model_validate(raw)


def test_archive_capacity_and_future_or_backdated_publication_refuse_without_replacement(tmp_path):
    from ai_quant_os.event_feed import EventFeed
    from ai_quant_os.event_feed_store import EventFeedStore

    with EventFeedStore(tmp_path / "events.sqlite3", max_feeds=1) as store:
        feed = EventFeed.model_validate(recipe())
        store.put(feed, now=NOW)
        with pytest.raises(ValueError):
            store.put(EventFeed.model_validate(recipe("second")), now=NOW)
        with pytest.raises(ValueError):
            store.put(
                EventFeed.model_validate(recipe("future", observed_at=NOW + timedelta(seconds=1))),
                now=NOW,
            )
        assert store.latest() == feed


@pytest.mark.parametrize("tamper", ["payload", "unpublished", "audit"])
def test_corrupt_feed_archive_rejects_reads_and_new_publications(tmp_path, tamper):
    from ai_quant_os.event_feed import EventFeed
    from ai_quant_os.event_feed_store import EventFeedStore

    path = tmp_path / "events.sqlite3"
    feed = EventFeed.model_validate(recipe())
    with EventFeedStore(path) as store:
        store.put(feed, now=NOW)
    with sqlite3.connect(path) as conn:
        if tamper == "payload":
            conn.execute("UPDATE event_feeds SET payload_json='{}'")
        elif tamper == "unpublished":
            conn.execute("DELETE FROM event_feeds")
        else:
            conn.execute(
                "UPDATE audit_events SET event_json=?", (json.dumps({"action": "changed"}),)
            )
    with pytest.raises(ValueError):
        with EventFeedStore(path) as store:
            store.latest()


def test_backdated_publish_cannot_replace_newer_feed_even_with_capacity(tmp_path):
    from ai_quant_os.event_feed import EventFeed
    from ai_quant_os.event_feed_store import EventFeedStore

    with EventFeedStore(tmp_path / "events.sqlite3") as store:
        current = EventFeed.model_validate(recipe())
        store.put(current, now=NOW)
        older = EventFeed.model_validate(recipe("older", observed_at=NOW - timedelta(seconds=1)))
        with pytest.raises(ValueError):
            store.put(older, now=NOW)
        assert store.latest() == current and store.get(older.content_ref) is None


def test_concurrent_store_connections_publish_once_with_atomic_audit(tmp_path):
    from ai_quant_os.event_feed import EventFeed
    from ai_quant_os.event_feed_store import EventFeedStore

    path = tmp_path / "events.sqlite3"
    feed = EventFeed.model_validate(recipe())
    with EventFeedStore(path):
        pass

    def publish_once(_):
        with EventFeedStore(path) as store:
            return store.put(feed, now=NOW)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(publish_once, range(8)))
    assert sum(results) == 1
    with EventFeedStore(path) as store:
        assert store.latest() == feed


@pytest.mark.parametrize("loss", ["table", "rows"])
def test_missing_metadata_is_not_baselined_as_a_new_archive(tmp_path, loss):
    from ai_quant_os.event_feed import EventFeed
    from ai_quant_os.event_feed_store import EventFeedStore

    path = tmp_path / "events.sqlite3"
    with EventFeedStore(path) as store:
        store.put(EventFeed.model_validate(recipe()), now=NOW)
    with sqlite3.connect(path) as conn:
        if loss == "table":
            conn.execute("DROP TABLE audit_head")
        else:
            conn.execute("DELETE FROM audit_head")
            conn.execute("DELETE FROM audit_chain")
    with pytest.raises(ValueError, match="metadata"):
        EventFeedStore(path)


@pytest.mark.parametrize("target", ["observed_at", "event"])
def test_timestamp_conversion_refuses_utc_overflow_as_validation_error(target):
    from ai_quant_os.event_feed import EventFeed

    raw = recipe()
    if target == "observed_at":
        raw["observed_at"] = "0001-01-01T00:00:00+14:00"
    else:
        raw["economic_calendar"][0]["occurs_at"] = "0001-01-01T00:00:00+14:00"
    with pytest.raises(ValueError):
        EventFeed.model_validate(raw)


def test_expiry_duration_validation_does_not_overflow_at_max_datetime():
    from ai_quant_os.event_feed import EventFeed

    raw = {
        "observed_at": "9999-12-31T23:59:58Z",
        "valid_until": "9999-12-31T23:59:59Z",
        "coverage_start": "9999-12-31T23:59:00Z",
        "coverage_end": "9999-12-31T23:59:59Z",
    }
    assert EventFeed.model_validate(raw).valid_until.year == 9999
