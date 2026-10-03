"""Atomic, immutable raw ECB captures with checked normalization and local journal."""

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
from threading import Lock

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.policy_rates import (
    CaptureMode,
    MAX_RATE_CSV_BYTES,
    RateCapture,
    normalize_ecb_rates,
)

MAX_CAPTURE_JSON_BYTES = 16384


class RateArchive:
    def __init__(self, path: Path, *, max_captures: int = 128) -> None:
        if type(max_captures) is not int or not 1 <= max_captures <= 128:
            raise ValueError("Invalid rate archive capacity")
        self._capacity = max_captures
        self._lock = Lock()
        existed = path.exists()
        self._connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._audit = AuditChain(self._connection)
        try:
            with self._transaction():
                required = {"rate_captures", "audit_events", "audit_chain", "audit_head"}
                existing = {
                    row[0]
                    for row in self._connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if (existed or existing) and (
                    not required <= existing
                    or self._connection.execute("SELECT COUNT(*) FROM audit_head").fetchone()[0]
                    != 1
                ):
                    raise ValueError("Rate archive metadata missing")
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS rate_captures (capture_ref TEXT PRIMARY KEY, raw_csv BLOB NOT NULL, capture_json TEXT NOT NULL)"
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

    def __enter__(self) -> "RateArchive":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _event(reference: str) -> dict[str, str]:
        return {"action": "RATE_CAPTURE_PUBLISH", "intent_id": "", "reason": reference}

    def _verified(self) -> tuple[tuple[RateCapture, bytes], ...]:
        if (
            any(
                self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                > self._capacity
                for table in ("rate_captures", "audit_events", "audit_chain")
            )
            or self._connection.execute(
                "SELECT COUNT(*) FROM rate_captures WHERE length(raw_csv)>? OR length(CAST(capture_json AS BLOB))>?",
                (MAX_RATE_CSV_BYTES, MAX_CAPTURE_JSON_BYTES),
            ).fetchone()[0]
        ):
            raise ValueError("Rate archive exceeds budget")
        self._audit.require_valid()
        rows = self._connection.execute(
            "SELECT r.capture_ref,r.raw_csv,r.capture_json,a.event_json FROM audit_chain c JOIN audit_events a USING(event_key) LEFT JOIN rate_captures r ON r.capture_ref=c.event_key ORDER BY c.sequence"
        ).fetchall()
        if (
            len(rows)
            != self._connection.execute("SELECT COUNT(*) FROM rate_captures").fetchone()[0]
        ):
            raise ValueError("Rate archive publication coverage mismatch")
        result = []
        for reference, raw, payload, event in rows:
            if not isinstance(raw, bytes):
                raise ValueError("Rate archive raw bytes missing")
            capture = RateCapture.model_validate_json(payload)
            reconstructed = normalize_ecb_rates(
                raw, received_at=capture.received_at, capture_mode=capture.capture_mode
            )
            if (
                capture != reconstructed
                or capture.capture_ref != reference
                or capture.model_dump_json() != payload
                or json.loads(event) != self._event(reference)
                or (result and capture.received_at < result[-1][0].received_at)
            ):
                raise ValueError("Rate archive byte, identity or journal mismatch")
            result.append((capture, raw))
        return tuple(result)

    def publish(
        self, raw: bytes, *, received_at: datetime, capture_mode: CaptureMode
    ) -> RateCapture:
        capture = normalize_ecb_rates(raw, received_at=received_at, capture_mode=capture_mode)
        payload = capture.model_dump_json()
        if len(payload.encode()) > MAX_CAPTURE_JSON_BYTES:
            raise ValueError("Rate capture exceeds byte budget")
        with self._lock, self._transaction():
            rows = self._verified()
            if any(row.capture_ref == capture.capture_ref for row, _ in rows):
                return capture
            if len(rows) >= self._capacity or (
                rows and capture.received_at < rows[-1][0].received_at
            ):
                raise ValueError("Rate archive capacity or receipt order refused")
            self._connection.execute(
                "INSERT INTO rate_captures VALUES (?,?,?)", (capture.capture_ref, raw, payload)
            )
            self._audit.append(capture.capture_ref, self._event(capture.capture_ref))
            return capture

    @staticmethod
    def _reference(reference: str) -> None:
        if re.fullmatch(r"rate-capture:[0-9a-f]{64}", reference) is None:
            raise ValueError("Invalid rate capture reference")

    def get(self, reference: str) -> RateCapture | None:
        self._reference(reference)
        with self._lock, self._transaction():
            return next(
                (capture for capture, _ in self._verified() if capture.capture_ref == reference),
                None,
            )

    def raw(self, reference: str) -> bytes:
        self._reference(reference)
        with self._lock, self._transaction():
            value = next(
                (raw for capture, raw in self._verified() if capture.capture_ref == reference), None
            )
            if value is None:
                raise ValueError("Rate capture not found")
            return value

    def list(self) -> tuple[RateCapture, ...]:
        with self._lock, self._transaction():
            return tuple(capture for capture, _ in self._verified())
