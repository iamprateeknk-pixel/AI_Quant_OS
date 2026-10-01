"""Context persistence using the caller's existing SQLite/audit transaction."""

import hashlib
import json
import sqlite3

from ai_quant_os.context_checkpoints import ContextCheckpoint

_MAX_BYTES = 65536
_MAX_CHECKPOINTS = 10000


class ContextCheckpointRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        connection.execute("""CREATE TABLE IF NOT EXISTS context_checkpoints (
            context_ref TEXT PRIMARY KEY,
            checkpoint_json TEXT NOT NULL,
            content_hash TEXT NOT NULL
        )""")

    def put(self, checkpoint: ContextCheckpoint) -> bool:
        # The state store owns the transaction and atomic audit append.
        raw = checkpoint.model_dump_json()
        if len(raw.encode()) > _MAX_BYTES:
            raise ValueError("Context checkpoint payload limit exceeded")
        ContextCheckpoint.model_validate_json(raw)
        ref = checkpoint.context.context_ref
        prior = self.get(ref)
        if prior:
            if prior.model_dump_json() != raw:
                raise ValueError("Context checkpoint integrity conflict")
            return False
        if (
            self._connection.execute("SELECT COUNT(*) FROM context_checkpoints").fetchone()[0]
            >= _MAX_CHECKPOINTS
        ):
            raise ValueError("Context checkpoint capacity exhausted")
        self._connection.execute(
            "INSERT INTO context_checkpoints VALUES (?, ?, ?)",
            (ref, raw, hashlib.sha256(raw.encode()).hexdigest()),
        )
        return True

    def get(self, context_ref: str) -> ContextCheckpoint | None:
        row = self._connection.execute(
            "SELECT checkpoint_json, content_hash FROM context_checkpoints WHERE context_ref = ?",
            (context_ref,),
        ).fetchone()
        if row is None:
            return None
        try:
            raw, digest = row
            if len(raw.encode()) > _MAX_BYTES or hashlib.sha256(raw.encode()).hexdigest() != digest:
                raise ValueError("Context checkpoint byte hash mismatch")
            checkpoint = ContextCheckpoint.model_validate_json(raw)
            if checkpoint.context.context_ref != context_ref or checkpoint.model_dump_json() != raw:
                raise ValueError("Context checkpoint identity or canonical form mismatch")
            event = self._connection.execute(
                "SELECT event_json FROM audit_events WHERE event_key = ?",
                (f"agent-context:{context_ref}",),
            ).fetchone()
            if event is None or json.loads(event[0]) != checkpoint.audit_event():
                raise ValueError("Context checkpoint audit binding mismatch")
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Context checkpoint integrity verification failed") from error
        return checkpoint
