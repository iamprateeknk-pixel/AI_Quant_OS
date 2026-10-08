"""Durable expiring grants; only nonce digests enter the checked journal."""

from contextlib import contextmanager
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
import secrets
import sqlite3
from threading import Lock
from uuid import uuid4

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.operator_session_contracts import OperatorSession, SESSION_TTL


class OperatorSessionStore:
    def __init__(self, path: Path | None = None, *, capacity: int = 128):
        if type(capacity) is not int or not 1 <= capacity <= 128:
            raise ValueError("Invalid session capacity")
        existed = path is not None and path.exists()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._capacity, self._lock = capacity, Lock()
        self._db = sqlite3.connect(
            str(path) if path else ":memory:",
            check_same_thread=False,
            isolation_level=None,
            timeout=5,
        )
        self._db.execute("PRAGMA synchronous=FULL")
        self._audit = AuditChain(self._db)
        try:
            with self._transaction():
                tables = {
                    r[0]
                    for r in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                required = {"operator_sessions", "audit_events", "audit_chain", "audit_head"}
                if existed and (
                    not required <= tables
                    or self._db.execute("SELECT COUNT(*) FROM audit_head").fetchone()[0] != 1
                ):
                    raise ValueError("Session journal metadata missing")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS operator_sessions (session_id TEXT PRIMARY KEY, token_sha256 TEXT UNIQUE NOT NULL, session_json TEXT NOT NULL)"
                )
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
                )
                self._audit.initialize()
                self._verified()
        except BaseException:
            self._db.close()
            raise

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def _verified(self) -> dict[str, OperatorSession]:
        count, size = self._db.execute(
            "SELECT COUNT(*), COALESCE(MAX(LENGTH(CAST(event_json AS BLOB))),0) FROM audit_events"
        ).fetchone()
        rows, row_size = self._db.execute(
            "SELECT COUNT(*), COALESCE(MAX(LENGTH(CAST(session_json AS BLOB))),0) FROM operator_sessions"
        ).fetchone()
        if count > 8192 or size > 2048 or rows > self._capacity or row_size > 1024:
            raise ValueError("Session journal capacity exceeded")
        integrity = self._audit.require_valid()
        if integrity.legacy_event_count:
            raise ValueError("Session journal contains unaudited grants")
        expected = {}
        seen = set()
        for key, raw in self._db.execute(
            "SELECT event_key,event_json FROM audit_events ORDER BY rowid"
        ):
            event = json.loads(raw)
            if set(event) == {"action", "session_json"} and event["action"] == "SESSION_ISSUED":
                grant = OperatorSession.model_validate_json(event["session_json"])
                if key != "issue:" + str(grant.session_id) or grant.token_sha256 in seen:
                    raise ValueError("Invalid session issue binding")
                seen.add(grant.token_sha256)
                expected[grant.token_sha256] = grant
            elif (
                set(event) == {"action", "token_sha256", "revoked_at"}
                and event["action"] == "SESSION_REVOKED"
            ):
                grant = expected.pop(event["token_sha256"], None)
                at = datetime.fromisoformat(event["revoked_at"])
                if (
                    grant is None
                    or key != "revoke:" + str(grant.session_id)
                    or at.utcoffset() != timedelta(0)
                    or at < grant.created_at
                ):
                    raise ValueError("Invalid session revocation binding")
            else:
                raise ValueError("Invalid session journal event")
        actual = {}
        for identity, digest, raw in self._db.execute(
            "SELECT session_id,token_sha256,session_json FROM operator_sessions"
        ):
            grant = OperatorSession.model_validate_json(raw)
            if identity != str(grant.session_id) or digest != grant.token_sha256:
                raise ValueError("Invalid session projection binding")
            actual[digest] = grant
        if actual != expected:
            raise ValueError("Session projection does not match journal")
        return actual

    @staticmethod
    def _context(now: datetime, epoch: str):
        if now.utcoffset() != timedelta(0) or not re.fullmatch(r"[0-9a-f]{64}", epoch):
            raise ValueError("UTC time and credential fingerprint required")

    def _remove(self, grant: OperatorSession, now: datetime):
        self._audit.append(
            "revoke:" + str(grant.session_id),
            {
                "action": "SESSION_REVOKED",
                "token_sha256": grant.token_sha256,
                "revoked_at": now.isoformat(),
            },
        )
        self._db.execute(
            "DELETE FROM operator_sessions WHERE token_sha256=?", (grant.token_sha256,)
        )

    def issue(self, now: datetime, epoch: str) -> tuple[str, OperatorSession]:
        self._context(now, epoch)
        with self._lock, self._transaction():
            grants = self._verified()
            expired = [g for g in grants.values() if g.expires_at <= now]
            if len(grants) - len(expired) >= self._capacity:
                raise ValueError("Session capacity exceeded")
            if self._audit.require_valid().event_count + len(expired) + 1 > 8192:
                raise ValueError("Session journal capacity exceeded")
            for grant in expired:
                self._remove(grant, now)
            token = secrets.token_urlsafe(32)
            grant = OperatorSession(
                session_id=uuid4(),
                token_sha256=sha256(token.encode()).hexdigest(),
                credential_epoch=epoch,
                created_at=now,
                expires_at=now + SESSION_TTL,
            )
            raw = grant.model_dump_json()
            self._db.execute(
                "INSERT INTO operator_sessions VALUES (?,?,?)",
                (str(grant.session_id), grant.token_sha256, raw),
            )
            self._audit.append(
                "issue:" + str(grant.session_id), {"action": "SESSION_ISSUED", "session_json": raw}
            )
            return token, grant

    def authenticate(self, token: str, now: datetime, epoch: str) -> OperatorSession | None:
        self._context(now, epoch)
        with self._lock, self._transaction():
            grants = self._verified()
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
                return None
            grant = grants.get(sha256(token.encode()).hexdigest())
            if (
                grant
                and grant.credential_epoch == epoch
                and grant.created_at <= now < grant.expires_at
            ):
                return grant
            return None

    def revoke(self, token: str, now: datetime, epoch: str):
        self._context(now, epoch)
        with self._lock, self._transaction():
            grants = self._verified()
            grant = grants.get(sha256(token.encode()).hexdigest())
            if grant and grant.credential_epoch == epoch and grant.created_at <= now:
                if self._audit.require_valid().event_count >= 8192:
                    raise ValueError("Session journal capacity exceeded")
                self._remove(grant, now)

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
