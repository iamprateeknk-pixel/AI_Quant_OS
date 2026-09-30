"""Agent result persistence owned by the paper state's existing audit transaction."""

import hashlib
import json
import sqlite3

from ai_quant_os.agent_runs import AgentRun

_MAX_BYTES = 1_000_000
_MAX_RUNS = 10000


class AgentRunRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        connection.execute("""CREATE TABLE IF NOT EXISTS agent_runs (
            run_id TEXT PRIMARY KEY,
            review_trace_id TEXT NOT NULL UNIQUE,
            run_json TEXT NOT NULL,
            content_hash TEXT NOT NULL
        )""")

    def put(self, run: AgentRun) -> bool:
        # Caller owns BEGIN IMMEDIATE, lock and audit append; never commit separately.
        raw = run.model_dump_json()
        if len(raw.encode()) > _MAX_BYTES:
            raise ValueError("Agent run payload limit exceeded")
        AgentRun.model_validate_json(raw)
        prior = self.get(run.run_id)
        if prior is not None:
            if prior.model_dump_json() != raw:
                raise ValueError("Agent run integrity conflict")
            return False
        if self._connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] >= _MAX_RUNS:
            raise ValueError("Agent run capacity exhausted")
        self._connection.execute(
            "INSERT INTO agent_runs VALUES (?, ?, ?, ?)",
            (run.run_id, run.deep_review.trace_id, raw, hashlib.sha256(raw.encode()).hexdigest()),
        )
        return True

    def get(self, run_id: str) -> AgentRun | None:
        row = self._connection.execute(
            "SELECT review_trace_id, run_json, content_hash FROM agent_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            review_id, raw, digest = row
            if len(raw.encode()) > _MAX_BYTES or hashlib.sha256(raw.encode()).hexdigest() != digest:
                raise ValueError("Agent run byte hash mismatch")
            run = AgentRun.model_validate_json(raw)
            if (
                run.run_id != run_id
                or run.deep_review.trace_id != review_id
                or run.model_dump_json() != raw
            ):
                raise ValueError("Agent run identity or canonical form mismatch")
            event = self._connection.execute(
                "SELECT event_json FROM audit_events WHERE event_key = ?",
                (f"classification:{run_id}",),
            ).fetchone()
            if event is None or json.loads(event[0]) != run.audit_event():
                raise ValueError("Agent run audit binding mismatch")
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Agent run integrity verification failed") from error
        return run
