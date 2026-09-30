"""Immutable local advisory snapshots; archived evidence never restores market freshness."""

import hashlib
import re
import sqlite3
from pathlib import Path
from threading import Lock

from pydantic import TypeAdapter

from ai_quant_os.contracts import _require_aware
from ai_quant_os.world_state import WorldState

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
    def __init__(self, path: Path | None = None, *, max_snapshots: int = 10000) -> None:
        if not 1 <= max_snapshots <= 1_000_000:
            raise ValueError("Invalid world-state archive capacity")
        self._max_snapshots = max_snapshots
        self._lock = Lock()
        self._connection = sqlite3.connect(
            str(path) if path else ":memory:", check_same_thread=False
        )
        with self._connection:
            self._connection.execute("""CREATE TABLE IF NOT EXISTS world_state_snapshots (
                world_state_version TEXT PRIMARY KEY,
                snapshot_json TEXT NOT NULL,
                content_hash TEXT NOT NULL
            )""")

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
