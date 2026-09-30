"""Local audit integrity. Transactions and locking belong to the owning store."""

import json
import sqlite3
from dataclasses import dataclass
from hashlib import sha256


GENESIS = "0" * 64


@dataclass(frozen=True)
class AuditIntegrity:
    valid: bool
    event_count: int
    head_hash: str
    legacy_event_count: int
    issue: str | None = None


def event_hash(sequence: int, key: str, raw: str, previous: str) -> str:
    payload = json.dumps(
        ["ai-quant-audit-v1", sequence, key, raw, previous],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


class AuditChain:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute("""CREATE TABLE IF NOT EXISTS audit_chain (
            sequence INTEGER PRIMARY KEY,
            event_key TEXT UNIQUE NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL
        )""")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS audit_head (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            event_count INTEGER NOT NULL,
            head_hash TEXT NOT NULL,
            legacy_event_count INTEGER NOT NULL
        )""")
        head = self.connection.execute("SELECT 1 FROM audit_head").fetchone()
        if head is None:
            if self.connection.execute("SELECT 1 FROM audit_chain LIMIT 1").fetchone():
                raise ValueError("Audit integrity metadata missing")
            rows = self.connection.execute(
                "SELECT event_key, event_json FROM audit_events ORDER BY rowid"
            ).fetchall()
            previous = GENESIS
            for sequence, (key, raw) in enumerate(rows, start=1):
                digest = event_hash(sequence, key, raw, previous)
                self.connection.execute(
                    "INSERT INTO audit_chain VALUES (?, ?, ?, ?)",
                    (sequence, key, previous, digest),
                )
                previous = digest
            self.connection.execute(
                "INSERT INTO audit_head VALUES (1, ?, ?, ?)",
                (len(rows), previous, len(rows)),
            )
        self.require_valid()

    def verify(self) -> AuditIntegrity:
        head = self.connection.execute(
            "SELECT event_count, head_hash, legacy_event_count FROM audit_head WHERE singleton = 1"
        ).fetchone()
        if head is None:
            return AuditIntegrity(False, 0, GENESIS, 0, "MISSING_HEAD")
        count, digest, legacy_count = head
        rows = self.connection.execute(
            "SELECT sequence, c.event_key, previous_hash, event_hash, e.event_json "
            "FROM audit_chain c LEFT JOIN audit_events e ON e.event_key = c.event_key "
            "ORDER BY sequence"
        ).fetchall()
        event_count = self.connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        issue = None
        previous = GENESIS
        if len(rows) != count or event_count != count or not 0 <= legacy_count <= count:
            issue = "COUNT_MISMATCH"
        else:
            for expected_sequence, (sequence, key, prior, hashed, raw) in enumerate(rows, start=1):
                if sequence != expected_sequence or prior != previous or raw is None:
                    issue = "CHAIN_MISMATCH"
                    break
                if hashed != event_hash(sequence, key, raw, previous):
                    issue = "EVENT_MISMATCH"
                    break
                previous = hashed
            if issue is None and previous != digest:
                issue = "HEAD_MISMATCH"
        return AuditIntegrity(issue is None, count, digest, legacy_count, issue)

    def require_valid(self) -> AuditIntegrity:
        report = self.verify()
        if not report.valid:
            raise ValueError(f"Audit integrity verification failed: {report.issue}")
        return report

    def append(self, key: str, event: dict[str, str], *, only_if_missing: bool = False) -> None:
        head = self.require_valid()
        raw = json.dumps(event, sort_keys=True)
        prior = self.connection.execute(
            "SELECT event_json FROM audit_events WHERE event_key = ?", (key,)
        ).fetchone()
        if prior is not None:
            if not only_if_missing and json.loads(prior[0]) != event:
                raise ValueError("Audit event key already has a different payload")
            return
        sequence = head.event_count + 1
        digest = event_hash(sequence, key, raw, head.head_hash)
        self.connection.execute("INSERT INTO audit_events VALUES (?, ?)", (key, raw))
        self.connection.execute(
            "INSERT INTO audit_chain VALUES (?, ?, ?, ?)",
            (sequence, key, head.head_hash, digest),
        )
        self.connection.execute(
            "UPDATE audit_head SET event_count = ?, head_hash = ? WHERE singleton = 1",
            (sequence, digest),
        )
