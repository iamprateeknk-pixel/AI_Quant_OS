"""Atomic graph invocation reservations and audit-bound results, independent of graph replay."""

import json
import sqlite3
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.context_checkpoints import ContextCheckpoint

RunStatus = Literal[
    "CREATED",
    "RUNNING",
    "COMPLETED",
    "UNAVAILABLE",
    "EXPIRED",
    "DENIED",
    "WAIT_FOR_DATA",
    "INVALID",
    "FAILED",
    "TIMEOUT",
    "UNCERTAIN",
]


class GraphReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    graph_version: Literal["0.1.0"] = "0.1.0"
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    agent_id: str
    agent_version: str
    task_id: str
    context_ref: str
    manifest_ref: str
    input_refs: tuple[str, ...]
    created_at: AwareDatetime
    valid_until: AwareDatetime
    status: RunStatus
    output_type: str
    output_json: str | None = Field(default=None, max_length=65536)
    requires_escalation: Literal[True] = True

    @model_validator(mode="after")
    def output_binding(self) -> "GraphReceipt":
        if (self.status == "COMPLETED") != (self.output_json is not None):
            raise ValueError("Graph output/status mismatch")
        if self.output_json is not None:
            value = json.loads(self.output_json)
            if not isinstance(value, dict):
                raise ValueError("Graph output must be a JSON object")
            canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
            if canonical != self.output_json or len(canonical.encode()) > 65536:
                raise ValueError("Graph output is not canonical or exceeds limit")
        return self


class GraphRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    checkpoint: ContextCheckpoint
    receipt: GraphReceipt

    @model_validator(mode="after")
    def receipt_binding(self) -> "GraphRecord":
        context, manifest, receipt = self.checkpoint.context, self.checkpoint.manifest, self.receipt
        if (
            receipt.agent_id,
            receipt.agent_version,
            receipt.task_id,
            receipt.context_ref,
            receipt.manifest_ref,
            receipt.output_type,
            receipt.input_refs,
            receipt.created_at,
            receipt.valid_until,
        ) != (
            context.agent_id,
            context.agent_version,
            context.task_id,
            context.context_ref,
            context.manifest_ref,
            manifest.output_type,
            context.input_refs,
            context.created_at,
            context.valid_until,
        ):
            raise ValueError("Graph receipt provenance mismatch")
        return self


class GraphLedger:
    def __init__(self, path: Path) -> None:
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
        self._lock = Lock()
        self._audit = AuditChain(self._db)
        try:
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS audit_events ("
                    "event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
                )
                self._audit.initialize()
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS graph_runs ("
                    "run_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, "
                    "record_hash TEXT NOT NULL)"
                )
        except BaseException:
            self._db.close()
            raise

    def close(self) -> None:
        self._db.close()

    def _read(self, run_id: str) -> GraphRecord | None:
        row = self._db.execute(
            "SELECT record_json, record_hash FROM graph_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        raw, digest = row
        if len(raw.encode()) > 131072 or sha256(raw.encode()).hexdigest() != digest:
            raise ValueError("Graph record integrity failed")
        record = GraphRecord.model_validate_json(raw)
        if record.model_dump_json() != raw or record.receipt.run_id != run_id:
            raise ValueError("Graph record canonical identity failed")
        key = f"graph:{run_id}:{record.receipt.status}"
        event = self._db.execute(
            "SELECT event_json FROM audit_events WHERE event_key = ?", (key,)
        ).fetchone()
        if event is None or json.loads(event[0]) != self._event(record, digest):
            raise ValueError("Graph record audit binding failed")
        return record

    @staticmethod
    def _event(record: GraphRecord, digest: str) -> dict[str, str]:
        return {
            "action": "AGENT_GRAPH_TRANSITION",
            "run_id": record.receipt.run_id,
            "status": record.receipt.status,
            "record_hash": digest,
            "context_ref": record.receipt.context_ref,
        }

    def _write(self, record: GraphRecord) -> None:
        record = GraphRecord.model_validate_json(record.model_dump_json())
        raw = record.model_dump_json()
        if len(raw.encode()) > 131072:
            raise ValueError("Graph record exceeds limit")
        digest = sha256(raw.encode()).hexdigest()
        self._db.execute(
            "INSERT INTO graph_runs VALUES (?, ?, ?) ON CONFLICT(run_id) "
            "DO UPDATE SET record_json=excluded.record_json, "
            "record_hash=excluded.record_hash",
            (record.receipt.run_id, raw, digest),
        )
        self._audit.append(
            f"graph:{record.receipt.run_id}:{record.receipt.status}", self._event(record, digest)
        )

    def get(self, run_id: str) -> GraphRecord:
        with self._lock, self._db:
            self._db.execute("BEGIN")
            self._audit.require_valid()
            record = self._read(run_id)
            if record is None:
                raise KeyError("Unknown graph run")
            return record

    def health_receipts(
        self, after_run_id: str = "", *, limit: int = 64
    ) -> tuple[GraphReceipt, ...]:
        if (
            not isinstance(after_run_id, str)
            or len(after_run_id) > 128
            or type(limit) is not int
            or not 1 <= limit <= 64
        ):
            raise ValueError("Invalid bounded graph health scan")
        with self._lock, self._db:
            self._db.execute("BEGIN")
            self._audit.require_valid()
            rows = self._db.execute(
                "SELECT run_id FROM graph_runs WHERE run_id > ? ORDER BY run_id LIMIT ?",
                (after_run_id, limit),
            ).fetchall()
            return tuple(self._read(row[0]).receipt for row in rows)

    def create(self, run_id: str, checkpoint: ContextCheckpoint) -> GraphRecord:
        context, manifest = checkpoint.context, checkpoint.manifest
        record = GraphRecord(
            checkpoint=checkpoint,
            receipt=GraphReceipt(
                run_id=run_id,
                agent_id=context.agent_id,
                agent_version=context.agent_version,
                task_id=context.task_id,
                context_ref=context.context_ref,
                manifest_ref=context.manifest_ref,
                input_refs=context.input_refs,
                created_at=context.created_at,
                valid_until=context.valid_until,
                status="CREATED",
                output_type=manifest.output_type,
            ),
        )
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._audit.require_valid()
            prior = self._read(run_id)
            if prior:
                if prior.checkpoint != checkpoint:
                    raise ValueError("Graph run identity conflict")
                return prior
            if self._db.execute("SELECT COUNT(*) FROM graph_runs").fetchone()[0] >= 10000:
                raise ValueError("Graph run capacity exhausted")
            self._write(record)
        return record

    def transition(
        self, run_id: str, expected: RunStatus, status: RunStatus, output_json: str | None = None
    ) -> GraphRecord:
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._audit.require_valid()
            prior = self._read(run_id)
            if prior is None or prior.receipt.status != expected:
                raise ValueError("Graph transition conflict")
            if (
                expected not in {"CREATED", "RUNNING"}
                or status == "CREATED"
                or (expected == "RUNNING" and status == "RUNNING")
                or (expected == "CREATED" and status in {"COMPLETED", "UNCERTAIN"})
            ):
                raise ValueError("Invalid graph transition")
            receipt = GraphReceipt.model_validate(
                {**prior.receipt.model_dump(), "status": status, "output_json": output_json}
            )
            record = GraphRecord(checkpoint=prior.checkpoint, receipt=receipt)
            self._write(record)
            return record
