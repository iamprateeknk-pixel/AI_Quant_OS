"""Immutable local advisory snapshots; archived evidence never restores market freshness."""

import hashlib
import re
import sqlite3
from pathlib import Path
from threading import Lock
from datetime import datetime, timezone
from decimal import Decimal
import json

from pydantic import TypeAdapter

from ai_quant_os.contracts import _require_aware
from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.world_state import WorldState
from ai_quant_os.market_data import Bar, BarDataset

_ADAPTER = TypeAdapter(WorldState)
_MAX_BYTES = 1_000_000


def _validate(snapshot: WorldState) -> None:
    _require_aware(snapshot.as_of)
    _require_aware(snapshot.valid_until)
    if snapshot.valid_until < snapshot.as_of:
        raise ValueError("Invalid world-state archive expiry")
    if (
        snapshot.schema_version != "1.0"
        or snapshot.source != "PAPER_SIMULATED"
        or snapshot.policy_version != "paper-regime-v1"
        or snapshot.status not in {"EMPTY", "DEGRADED", "READY"}
        or re.fullmatch(r"world:[a-f0-9]{64}", snapshot.world_state_version) is None
    ):
        raise ValueError("Invalid world-state archive contract")
    if not snapshot.instruments or len({i.instrument_id for i in snapshot.instruments}) != len(
        snapshot.instruments
    ):
        raise ValueError("Invalid world-state archive instruments")
    for item in snapshot.instruments:
        if (
            item.quote_status not in {"MISSING", "UNTRUSTED", "FUTURE", "STALE", "FRESH"}
            or item.history_status not in {"INSUFFICIENT", "STALE", "FRESH"}
            or item.regime not in {"UNKNOWN", "TRENDING", "RANGING", "HIGH_VOLATILITY"}
            or not 0 <= item.bar_count <= 4096
        ):
            raise ValueError("Invalid world-state archive evidence")
        for number in (
            item.mid_price,
            item.spread_bps,
            item.return_bps,
            item.realized_volatility_bps,
        ):
            if number is not None and not number.is_finite():
                raise ValueError("Invalid world-state archive numeric evidence")


class WorldStateStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        max_snapshots: int = 10000,
        max_bars: int = 1_000_000,
        connection: sqlite3.Connection | None = None,
        lock: object | None = None,
    ) -> None:
        if not 1 <= max_snapshots <= 1_000_000 or not 1 <= max_bars <= 10_000_000:
            raise ValueError("Invalid world-state archive capacity")
        self._max_snapshots = max_snapshots
        self._max_bars = max_bars
        if (connection is None) != (lock is None):
            raise ValueError(
                "A shared connection and its transaction lock must be supplied together"
            )
        self._lock = lock if lock is not None else Lock()
        self._connection = connection or sqlite3.connect(
            str(path) if path else ":memory:", check_same_thread=False
        )
        with self._connection:
            self._connection.execute("""CREATE TABLE IF NOT EXISTS world_state_snapshots (
                world_state_version TEXT PRIMARY KEY,
                snapshot_json TEXT NOT NULL,
                content_hash TEXT NOT NULL
            )""")
            self._connection.execute("""CREATE TABLE IF NOT EXISTS world_state_bars (
                instrument_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                bar_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                PRIMARY KEY (instrument_id, observed_at)
            )""")

    @staticmethod
    def _bar_json(bar: Bar) -> str:
        return json.dumps(
            {
                "instrument_id": bar.instrument_id,
                "observed_at": bar.observed_at.isoformat(),
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": str(bar.volume),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def record_bars(
        self, bars: tuple[Bar, ...], *, audit_event_key: str | None = None
    ) -> tuple[int, int]:
        if (
            type(bars) is not tuple
            or not 1 <= len(bars) <= 4096
            or any(type(bar) is not Bar for bar in bars)
            or (
                audit_event_key is not None
                and (not isinstance(audit_event_key, str) or not audit_event_key)
            )
        ):
            raise ValueError("Bounded typed market bars are required")
        dataset = BarDataset(bars)
        encoded = tuple((bar, self._bar_json(bar)) for bar in dataset.bars)
        duplicates = dataset.duplicate_rows
        pending = []
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            for bar, raw in encoded:
                digest = hashlib.sha256(raw.encode()).hexdigest()
                prior = self._connection.execute(
                    "SELECT bar_json, content_hash FROM world_state_bars "
                    "WHERE instrument_id = ? AND observed_at = ?",
                    (bar.instrument_id, bar.observed_at.isoformat()),
                ).fetchone()
                if prior is not None:
                    if hashlib.sha256(prior[0].encode()).hexdigest() != prior[1]:
                        raise ValueError("Market bar archive integrity failure")
                    if prior != (raw, digest):
                        raise ValueError("Market bar archive conflict")
                    duplicates += 1
                    continue
                pending.append((bar, raw, digest))
            count = self._connection.execute("SELECT COUNT(*) FROM world_state_bars").fetchone()[0]
            if count + len(pending) > self._max_bars:
                raise ValueError("Market bar archive capacity exhausted")
            for bar, raw, digest in pending:
                self._connection.execute(
                    "INSERT INTO world_state_bars VALUES (?, ?, ?, ?)",
                    (bar.instrument_id, bar.observed_at.isoformat(), raw, digest),
                )
            if audit_event_key is not None:
                AuditChain(self._connection).append(
                    audit_event_key,
                    {
                        "action": "PAPER_WORLD_STATE_BARS",
                        "intent_id": "",
                        "reason": f"INSERTED:{len(pending)}",
                    },
                )
        return len(pending), duplicates

    def read_bars(
        self, instrument_id: str, start: datetime, through: datetime, *, limit: int = 4096
    ) -> tuple[Bar, ...]:
        rows = self._bar_rows(instrument_id, start, through, limit=limit, detect_overflow=True)
        return self._decode_bar_rows(instrument_id, rows)

    def read_bars_page(
        self, instrument_id: str, start: datetime, through: datetime, *, limit: int = 4096
    ) -> tuple[Bar, ...]:
        """Read the earliest bounded page so callers can advance by its last timestamp."""
        rows = self._bar_rows(instrument_id, start, through, limit=limit, detect_overflow=False)
        return self._decode_bar_rows(instrument_id, rows)

    def _bar_rows(self, instrument_id, start, through, *, limit, detect_overflow):
        _require_aware(start)
        _require_aware(through)
        if (
            not isinstance(instrument_id, str)
            or not instrument_id
            or len(instrument_id) > 64
            or start > through
            or type(limit) is not int
            or not 1 <= limit <= 4096
        ):
            raise ValueError("Invalid bounded market bar query")
        with self._lock:
            rows = self._connection.execute(
                "SELECT observed_at, bar_json, content_hash FROM world_state_bars "
                "WHERE instrument_id = ? AND observed_at >= ? AND observed_at <= ? "
                "ORDER BY observed_at LIMIT ?",
                (
                    instrument_id,
                    start.astimezone(timezone.utc).isoformat(),
                    through.astimezone(timezone.utc).isoformat(),
                    limit + 1 if detect_overflow else limit,
                ),
            ).fetchall()
        if detect_overflow and len(rows) > limit:
            raise ValueError("Market bar query exceeds result limit")
        return rows

    def _decode_bar_rows(self, instrument_id, rows):
        bars = []
        try:
            for observed_at, raw, digest in rows:
                encoded = raw.encode()
                if len(encoded) > 4096 or hashlib.sha256(encoded).hexdigest() != digest:
                    raise ValueError("Market bar archive content hash mismatch")
                data = json.loads(raw)
                bar = Bar(
                    instrument_id=data["instrument_id"],
                    observed_at=datetime.fromisoformat(data["observed_at"]),
                    open=Decimal(data["open"]),
                    high=Decimal(data["high"]),
                    low=Decimal(data["low"]),
                    close=Decimal(data["close"]),
                    volume=Decimal(data["volume"]),
                )
                if (
                    bar.instrument_id != instrument_id
                    or bar.observed_at.isoformat() != observed_at
                    or self._bar_json(bar) != raw
                ):
                    raise ValueError("Market bar archive identity mismatch")
                bars.append(bar)
        except (KeyError, TypeError, ValueError, ArithmeticError) as error:
            raise ValueError("Market bar archive integrity verification failed") from error
        return tuple(bars)

    def put(self, snapshot: WorldState) -> bool:
        raw = _ADAPTER.dump_json(snapshot)
        if len(raw) > _MAX_BYTES:
            raise ValueError("World-state archive payload limit exceeded")
        _validate(_ADAPTER.validate_json(raw))
        serialized = raw.decode()
        digest = hashlib.sha256(raw).hexdigest()
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            prior = self._connection.execute(
                "SELECT snapshot_json, content_hash FROM world_state_snapshots WHERE world_state_version = ?",
                (snapshot.world_state_version,),
            ).fetchone()
            if prior is not None:
                if prior != (serialized, digest):
                    raise ValueError("World-state archive integrity conflict")
                return False
            count = self._connection.execute(
                "SELECT COUNT(*) FROM world_state_snapshots"
            ).fetchone()[0]
            if count >= self._max_snapshots:
                raise ValueError("World-state archive capacity exhausted")
            self._connection.execute(
                "INSERT INTO world_state_snapshots VALUES (?, ?, ?)",
                (snapshot.world_state_version, serialized, digest),
            )
            return True

    def get(self, world_state_version: str) -> WorldState | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT snapshot_json, content_hash FROM world_state_snapshots WHERE world_state_version = ?",
                (world_state_version,),
            ).fetchone()
        if row is None:
            return None
        return self._decode(world_state_version, row)

    @staticmethod
    def _decode(world_state_version: str, row: tuple[str, str]) -> WorldState:
        try:
            raw, digest = row
            encoded = raw.encode()
            if len(encoded) > _MAX_BYTES or hashlib.sha256(encoded).hexdigest() != digest:
                raise ValueError("World-state archive byte hash mismatch")
            snapshot = _ADAPTER.validate_json(encoded)
            _validate(snapshot)
            if (
                snapshot.world_state_version != world_state_version
                or _ADAPTER.dump_json(snapshot) != encoded
            ):
                raise ValueError("World-state archive identity or canonical form mismatch")
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("World-state archive integrity verification failed") from error
        return snapshot

    def research_snapshots(self, *, limit: int = 64) -> tuple[WorldState, ...]:
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError("Invalid world research archive bound")
        with self._lock:
            rows = self._connection.execute(
                "SELECT world_state_version,snapshot_json,content_hash FROM world_state_snapshots ORDER BY rowid LIMIT ?",
                (limit + 1,),
            ).fetchall()
        if len(rows) > limit:
            raise ValueError("World research archive exceeds collection bound")
        return tuple(self._decode(reference, (raw, digest)) for reference, raw, digest in rows)
