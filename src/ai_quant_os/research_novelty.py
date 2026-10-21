"""Durable, short-lived reservations for competing A08 research contexts."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3
from threading import Lock


_CONTEXT_REF = re.compile(r"^agent-context:[a-f0-9]{64}$")
_SIGNATURE = re.compile(r"^[a-f0-9]{64}$")
_MAX_LEASE = timedelta(seconds=5)
_MAX_RESERVATIONS = 512


def _instant(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Research reservation time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _encoded(value: datetime) -> str:
    return _instant(value).isoformat(timespec="microseconds")


class ResearchNoveltyReservations:
    """Atomically lease at most four research signatures per context."""

    def __init__(self, path: Path | None = None) -> None:
        target = Path(path) if path is not None else None
        existed = target is not None and target.exists()
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            str(target) if target is not None else ":memory:",
            check_same_thread=False,
            isolation_level=None,
            timeout=5,
        )
        self._lock = Lock()
        try:
            self._db.execute("PRAGMA synchronous=FULL")
            if existed:
                tables = {
                    row[0]
                    for row in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if "research_novelty_reservations" not in tables:
                    raise ValueError("Research novelty reservation metadata missing")
                check = self._db.execute("PRAGMA integrity_check").fetchone()
                if check is None or check[0] != "ok":
                    raise ValueError("Research novelty reservation storage is corrupt")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS research_novelty_reservations ("
                "signature TEXT PRIMARY KEY, context_ref TEXT NOT NULL, valid_until TEXT NOT NULL)"
            )
            self._verify_rows()
        except BaseException:
            self._db.close()
            raise

    def _verify_rows(self) -> None:
        rows = self._db.execute(
            "SELECT signature,context_ref,valid_until FROM research_novelty_reservations "
            "ORDER BY signature"
        ).fetchall()
        if len(rows) > _MAX_RESERVATIONS:
            raise ValueError("Research novelty reservation capacity exceeded")
        for signature, context_ref, valid_until in rows:
            if (
                not isinstance(signature, str)
                or _SIGNATURE.fullmatch(signature) is None
                or not isinstance(context_ref, str)
                or _CONTEXT_REF.fullmatch(context_ref) is None
                or not isinstance(valid_until, str)
            ):
                raise ValueError("Research novelty reservation identity mismatch")
            try:
                moment = datetime.fromisoformat(valid_until)
            except ValueError as error:
                raise ValueError("Research novelty reservation expiry mismatch") from error
            if moment.utcoffset() is None or _encoded(moment) != valid_until:
                raise ValueError("Research novelty reservation expiry mismatch")

    def _prune(self, now: datetime) -> None:
        self._db.execute(
            "DELETE FROM research_novelty_reservations WHERE valid_until<=?", (_encoded(now),)
        )

    def active_signatures(self, now: datetime) -> tuple[str, ...]:
        moment = _instant(now)
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._verify_rows()
            self._prune(moment)
            return tuple(
                row[0]
                for row in self._db.execute(
                    "SELECT signature FROM research_novelty_reservations "
                    "WHERE valid_until>? ORDER BY signature",
                    (_encoded(moment),),
                )
            )

    def claim(
        self,
        context_ref: str,
        signatures: tuple[str, ...],
        *,
        now: datetime,
        valid_until: datetime,
    ) -> tuple[str, ...]:
        moment, expiry = _instant(now), _instant(valid_until)
        if (
            not isinstance(context_ref, str)
            or _CONTEXT_REF.fullmatch(context_ref) is None
            or not isinstance(signatures, tuple)
            or len(signatures) > 4
            or any(
                not isinstance(value, str) or _SIGNATURE.fullmatch(value) is None
                for value in signatures
            )
            or signatures != tuple(sorted(set(signatures)))
            or expiry <= moment
            or expiry > moment + _MAX_LEASE
        ):
            raise ValueError("Invalid research novelty reservation request")
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._verify_rows()
            self._prune(moment)
            existing = {
                signature: owner
                for signature, owner in self._db.execute(
                    "SELECT signature,context_ref FROM research_novelty_reservations"
                )
            }
            conflicts = tuple(
                signature
                for signature in signatures
                if signature in existing and existing[signature] != context_ref
            )
            if conflicts:
                return conflicts
            if (
                len(existing) + sum(signature not in existing for signature in signatures)
                > _MAX_RESERVATIONS
            ):
                raise ValueError("Research novelty reservation capacity exhausted")
            encoded_expiry = _encoded(expiry)
            for signature in signatures:
                prior = self._db.execute(
                    "SELECT valid_until FROM research_novelty_reservations "
                    "WHERE signature=? AND context_ref=?",
                    (signature, context_ref),
                ).fetchone()
                if prior is None:
                    self._db.execute(
                        "INSERT INTO research_novelty_reservations VALUES (?,?,?)",
                        (signature, context_ref, encoded_expiry),
                    )
                elif prior[0] != encoded_expiry:
                    raise ValueError("Research novelty reservation context changed")
            return ()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "ResearchNoveltyReservations":
        return self

    def __exit__(self, *_args) -> None:
        self.close()
