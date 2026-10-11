"""Immutable trace links inside the original agent run owner's transaction."""

from hashlib import sha256
import json
import sqlite3

from ai_quant_os.agent_runs import AgentRun
from ai_quant_os.agent_trace_binding import AgentTraceBinding


class AgentTraceBindingRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        connection.execute("""CREATE TABLE IF NOT EXISTS agent_trace_bindings (
            run_id TEXT PRIMARY KEY,
            binding_json TEXT NOT NULL,
            content_hash TEXT NOT NULL
        )""")

    def get(self, run_id: str, run: AgentRun | None) -> AgentTraceBinding | None:
        row = self._connection.execute(
            "SELECT binding_json,content_hash FROM agent_trace_bindings WHERE run_id=?", (run_id,)
        ).fetchone()
        event = self._connection.execute(
            "SELECT event_json FROM audit_events WHERE event_key=?", (f"agent-trace:{run_id}",)
        ).fetchone()
        if row is None and event is None:
            return None
        try:
            if row is None or event is None or run is None:
                raise ValueError("Original trace binding custody differs")
            raw, digest = row
            if len(raw.encode()) > 2048 or sha256(raw.encode()).hexdigest() != digest:
                raise ValueError("Trace binding bytes differ")
            binding = AgentTraceBinding.model_validate_json(raw)
            binding.require_run(run)
            if (
                binding.run_id != run_id
                or binding.model_dump_json() != raw
                or json.loads(event[0]) != binding.audit_event()
            ):
                raise ValueError("Trace binding audit commitment differs")
            return binding
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Agent trace binding integrity verification failed") from error

    def put(self, binding: AgentTraceBinding, run: AgentRun, *, inserted_run: bool):
        binding.require_run(run)
        prior = self.get(run.run_id, run)
        if prior is not None:
            if prior != binding:
                raise ValueError("Original trace binding cannot be replaced")
            return
        if not inserted_run:
            raise ValueError("An old agent run cannot infer a new trace context")
        raw = binding.model_dump_json()
        if len(raw.encode()) > 2048:
            raise ValueError("Trace binding capacity exceeded")
        # One row per original bounded run. Caller owns lock, BEGIN and both audit appends.
        self._connection.execute(
            "INSERT INTO agent_trace_bindings VALUES (?,?,?)",
            (run.run_id, raw, sha256(raw.encode()).hexdigest()),
        )
