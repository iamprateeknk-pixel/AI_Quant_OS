"""Immutable, audited storage for fail-closed promotion evidence snapshots."""

import json
import re
import sqlite3
from contextlib import closing
from hashlib import sha256
from pathlib import Path

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.promotion_contracts import PromotionDecision


_REF = re.compile(r"promotion-decision:[a-f0-9]{64}")
_MAX_RECORDS = 1000
_MAX_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_RECORD_BYTES = 1024 * 1024


def _encode(value: PromotionDecision) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _ref(raw: bytes) -> str:
    return "promotion-decision:" + sha256(raw).hexdigest()


class PromotionEvidenceStore:
    """Retain promotion gate evidence; this store cannot approve or apply a decision."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connection()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS audit_events "
                "(event_key TEXT PRIMARY KEY,event_json TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS promotion_decisions "
                "(ref TEXT PRIMARY KEY,body BLOB NOT NULL)"
            )
            AuditChain(db).initialize()
            self._verified(db)

    def _connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _event(ref: str) -> dict[str, str]:
        return {"action": "PROMOTION_EVIDENCE_RETAINED", "ref": ref}

    def _verified(self, db: sqlite3.Connection) -> dict[str, PromotionDecision]:
        count, total, largest = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(body)),0),COALESCE(MAX(LENGTH(body)),0) "
            "FROM promotion_decisions"
        ).fetchone()
        events, maximum = db.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
        ).fetchone()
        if (
            count > _MAX_RECORDS
            or total > _MAX_TOTAL_BYTES
            or largest > _MAX_RECORD_BYTES
            or events != count
            or maximum > 256
        ):
            raise ValueError("Promotion evidence integrity or capacity mismatch")
        if AuditChain(db).require_valid().legacy_event_count:
            raise ValueError("Promotion evidence cannot baseline prior audit data")

        decisions: dict[str, PromotionDecision] = {}
        for ref, raw in db.execute("SELECT ref,body FROM promotion_decisions"):
            if not isinstance(raw, bytes):
                raise ValueError("Promotion evidence integrity body type mismatch")
            try:
                value = PromotionDecision.model_validate_json(raw)
            except (ValueError, TypeError) as exc:
                raise ValueError("Promotion evidence integrity model mismatch") from exc
            event = db.execute(
                "SELECT event_json FROM audit_events WHERE event_key=?", (ref,)
            ).fetchone()
            if (
                _encode(value) != raw
                or _ref(raw) != ref
                or event is None
                or json.loads(event[0]) != self._event(ref)
            ):
                raise ValueError("Promotion evidence integrity binding mismatch")
            decisions[ref] = value
        return decisions

    def register(self, value: PromotionDecision) -> str:
        if type(value) is not PromotionDecision:
            raise ValueError("Typed promotion evidence required")
        raw = _encode(value)
        if len(raw) > _MAX_RECORD_BYTES:
            raise ValueError("Promotion evidence exceeds record bound")
        ref = _ref(raw)
        with closing(self._connection()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            decisions = self._verified(db)
            if ref in decisions:
                return ref
            total = db.execute(
                "SELECT COALESCE(SUM(LENGTH(body)),0) FROM promotion_decisions"
            ).fetchone()[0]
            if len(decisions) >= _MAX_RECORDS or total + len(raw) > _MAX_TOTAL_BYTES:
                raise ValueError("Promotion evidence capacity exhausted")
            db.execute("INSERT INTO promotion_decisions VALUES (?,?)", (ref, raw))
            AuditChain(db).append(ref, self._event(ref))
            self._verified(db)
        return ref

    def get(self, ref: str) -> PromotionDecision:
        if type(ref) is not str or not _REF.fullmatch(ref):
            raise ValueError("Canonical promotion evidence reference required")
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            value = self._verified(db).get(ref)
        if value is None:
            raise ValueError("Unknown promotion evidence reference")
        return value

    def list_refs(self) -> tuple[str, ...]:
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            return tuple(sorted(self._verified(db)))
