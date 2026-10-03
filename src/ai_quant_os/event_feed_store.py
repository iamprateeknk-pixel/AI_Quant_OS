"""Immutable local event archive, atomically bound to its publication journal."""

from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import Lock

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.contracts import _require_aware
from ai_quant_os.event_feed import EventFeed

MAX_EVENT_FEED_BYTES = 65536


class EventFeedStore:
    def __init__(self, path: Path | None = None, *, max_feeds: int = 128) -> None:
        if type(max_feeds) is not int or not 1 <= max_feeds <= 128:
            raise ValueError("Invalid event archive capacity")
        self._capacity = max_feeds
        self._lock = Lock()
        self._connection = sqlite3.connect(
            path or ":memory:", check_same_thread=False, isolation_level=None
        )
        self._audit = AuditChain(self._connection)
        try:
            with self._transaction():
                required = {"event_feeds", "audit_events", "audit_chain", "audit_head"}
                existing = {
                    row[0]
                    for row in self._connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if existing and not required <= existing:
                    raise ValueError("Event archive metadata missing")
                if (
                    existing
                    and self._connection.execute("SELECT COUNT(*) FROM audit_head").fetchone()[0]
                    != 1
                ):
                    raise ValueError("Event archive metadata missing")
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS event_feeds (feed_ref TEXT PRIMARY KEY, payload_json TEXT NOT NULL, content_hash TEXT NOT NULL)"
                )
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
                )
                self._audit.initialize()
                self._verified()
        except BaseException:
            self._connection.close()
            raise

    @contextmanager
    def _transaction(self):
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise

    def __enter__(self) -> "EventFeedStore":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _event(reference: str) -> dict[str, str]:
        return {"action": "EVENT_FEED_PUBLISH", "intent_id": "", "reason": reference}

    def _verified(self) -> tuple[EventFeed, ...]:
        if (
            self._connection.execute("SELECT COUNT(*) FROM event_feeds").fetchone()[0]
            > self._capacity
            or self._connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
            > self._capacity
        ):
            raise ValueError("Event archive exceeds capacity")
        self._audit.require_valid()
        rows = self._connection.execute(
            "SELECT f.feed_ref, f.payload_json, f.content_hash, a.event_json FROM audit_chain c JOIN audit_events a USING(event_key) LEFT JOIN event_feeds f ON f.feed_ref=c.event_key ORDER BY c.sequence"
        ).fetchall()
        if len(rows) != self._connection.execute("SELECT COUNT(*) FROM event_feeds").fetchone()[0]:
            raise ValueError("Event archive publication coverage mismatch")
        feeds = []
        for reference, raw, digest, event in rows:
            if (
                raw is None
                or len(raw.encode()) > MAX_EVENT_FEED_BYTES
                or sha256(raw.encode()).hexdigest() != digest
            ):
                raise ValueError("Event archive byte integrity failed")
            feed = EventFeed.model_validate_json(raw)
            if (
                feed.content_ref != reference
                or feed.model_dump_json() != raw
                or json.loads(event) != self._event(reference)
                or (feeds and feed.observed_at < feeds[-1].observed_at)
            ):
                raise ValueError("Event archive identity or publication integrity failed")
            feeds.append(feed)
        return tuple(feeds)

    def put(self, feed: EventFeed, *, now: datetime) -> bool:
        _require_aware(now)
        feed = EventFeed.model_validate_json(feed.model_dump_json())
        raw = feed.model_dump_json()
        if (
            len(raw.encode()) > MAX_EVENT_FEED_BYTES
            or not feed.observed_at <= now < feed.valid_until
        ):
            raise ValueError("Event publication is future, expired or oversized")
        with self._lock, self._transaction():
            feeds = self._verified()
            if any(row.content_ref == feed.content_ref for row in feeds):
                return False
            if len(feeds) >= self._capacity or (feeds and feed.observed_at < feeds[-1].observed_at):
                raise ValueError("Event archive capacity or publication order refused")
            self._connection.execute(
                "INSERT INTO event_feeds VALUES (?, ?, ?)",
                (feed.content_ref, raw, sha256(raw.encode()).hexdigest()),
            )
            self._audit.append(feed.content_ref, self._event(feed.content_ref))
            return True

    def latest(self) -> EventFeed | None:
        with self._lock, self._transaction():
            feeds = self._verified()
            return feeds[-1] if feeds else None

    def get(self, reference: str) -> EventFeed | None:
        with self._lock, self._transaction():
            return next((row for row in self._verified() if row.content_ref == reference), None)
