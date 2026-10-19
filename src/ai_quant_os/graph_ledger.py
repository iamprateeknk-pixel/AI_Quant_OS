"""Atomic graph invocation reservations and audit-bound results, independent of graph replay."""

import json
import re
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Callable, Literal, get_args

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


class GraphQualityCapacityExceeded(ValueError):
    """The bounded operational quality window cannot cover all retained runs."""


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
    @classmethod
    @contextmanager
    def archive(cls, path: Path):
        """Read an existing archive with SQLite-enforced read-only access; no migration."""
        path = Path(path).resolve()
        if not path.is_file():
            raise ValueError("An existing graph archive is required")
        ledger = cls.__new__(cls)
        ledger._db = sqlite3.connect(
            path.as_uri() + "?mode=ro", uri=True, check_same_thread=False, timeout=5
        )
        ledger._lock = Lock()
        ledger._audit = AuditChain(ledger._db)
        try:
            yield ledger
        finally:
            ledger.close()

    def __init__(self, path: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
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
        if event is None or not self._matches_event(json.loads(event[0]), record, digest):
            raise ValueError("Graph record audit binding failed")
        return record

    @staticmethod
    def _matches_event(event, record: GraphRecord, digest: str) -> bool:
        from ai_quant_os.graph_trace import observation

        if not isinstance(event, dict):
            return False
        legacy = GraphLedger._event(record, digest)
        if event == legacy:
            return True
        if set(event) != {*legacy, "observed_at"} or event["observed_at"] is None:
            return False
        observation(event["observed_at"])
        return {key: value for key, value in event.items() if key != "observed_at"} == legacy

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
        moment = self._clock()
        if not isinstance(moment, datetime) or moment.utcoffset() is None:
            raise ValueError("Graph transition clock must be aware")
        event = {
            **self._event(record, digest),
            "observed_at": moment.astimezone(timezone.utc).isoformat(),
        }
        self._audit.append(f"graph:{record.receipt.run_id}:{record.receipt.status}", event)

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

    def quality_receipts(self, *, limit: int = 512) -> tuple[GraphReceipt, ...]:
        """Return checked current receipts used to rebuild operational metrics."""
        if type(limit) is not int or not 1 <= limit <= 512:
            raise ValueError("Invalid agent quality receipt bound")
        with self._lock, self._db:
            self._db.execute("BEGIN")
            rows = self._db.execute(
                "SELECT run_id FROM graph_runs ORDER BY run_id LIMIT ?", (limit + 1,)
            ).fetchall()
            count, largest = self._db.execute(
                "SELECT COUNT(*),COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
            ).fetchone()
            if len(rows) > limit or count > 30000 or largest > 4096:
                raise GraphQualityCapacityExceeded(
                    "Agent quality receipt inventory exceeds the covered window"
                )
            self._audit.require_valid()
            run_ids = {row[0] for row in rows}
            audited_ids = set()
            for key, raw in self._db.execute("SELECT event_key,event_json FROM audit_events"):
                try:
                    event = json.loads(raw)
                    run_id, status = event["run_id"], event["status"]
                except (TypeError, ValueError, KeyError) as error:
                    raise ValueError("Agent quality journal is malformed") from error
                if (
                    not isinstance(event, dict)
                    or set(event)
                    not in (
                        {"action", "run_id", "status", "record_hash", "context_ref"},
                        {
                            "action",
                            "run_id",
                            "status",
                            "record_hash",
                            "context_ref",
                            "observed_at",
                        },
                    )
                    or event["action"] != "AGENT_GRAPH_TRANSITION"
                    or not isinstance(run_id, str)
                    or status not in get_args(RunStatus)
                    or key != f"graph:{run_id}:{status}"
                ):
                    raise ValueError("Agent quality journal identity mismatch")
                audited_ids.add(run_id)
            if audited_ids != run_ids:
                raise ValueError("Agent quality receipt/audit inventory mismatch")
            return tuple(self._read(run_id).receipt for (run_id,) in rows)

    def records_for_agent(
        self, agent_id: str, agent_version: str, *, limit: int = 128
    ) -> tuple[GraphRecord, ...]:
        if (
            not isinstance(agent_id, str)
            or re.fullmatch(r"A(?:0[1-9]|1[0-8])", agent_id) is None
            or not isinstance(agent_version, str)
            or len(agent_version) > 32
            or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", agent_version) is None
            or type(limit) is not int
            or not 1 <= limit <= 128
        ):
            raise ValueError("Invalid agent graph collection bound")
        with self._lock, self._db:
            self._db.execute("BEGIN")
            rows = self._db.execute(
                "SELECT run_id FROM graph_runs ORDER BY run_id LIMIT 10001"
            ).fetchall()
            count, largest = self._db.execute(
                "SELECT COUNT(*),COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
            ).fetchone()
            if len(rows) > 10000 or count > 30000 or largest > 4096:
                raise ValueError("Agent graph collection exceeds archive bound")
            self._audit.require_valid()
            audited_ids = set()
            for key, raw in self._db.execute("SELECT event_key,event_json FROM audit_events"):
                event = json.loads(raw)
                if (
                    not isinstance(event, dict)
                    or set(event)
                    not in (
                        {"action", "run_id", "status", "record_hash", "context_ref"},
                        {"action", "run_id", "status", "record_hash", "context_ref", "observed_at"},
                    )
                    or event["action"] != "AGENT_GRAPH_TRANSITION"
                    or not isinstance(event["run_id"], str)
                    or key != f"graph:{event['run_id']}:{event['status']}"
                ):
                    raise ValueError("Agent graph collection journal mismatch")
                audited_ids.add(event["run_id"])
            if audited_ids != {row[0] for row in rows}:
                raise ValueError("Agent graph collection identity mismatch")
            selected = []
            for (run_id,) in rows:
                record = self._read(run_id)
                if (record.receipt.agent_id, record.receipt.agent_version) == (
                    agent_id,
                    agent_version,
                ):
                    selected.append(record)
                    if len(selected) > limit:
                        raise ValueError("Agent graph collection exceeds requested bound")
            return tuple(selected)

    def page(self, *, after_run_id: str = "", head: str | None = None, limit: int = 25):
        from ai_quant_os.graph_archive import GraphArchiveChanged, GraphRunPage, summary
        from ai_quant_os.graph_trace import observation

        if (
            not isinstance(after_run_id, str)
            or (after_run_id and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", after_run_id) is None)
            or type(limit) is not int
            or not 1 <= limit <= 25
            or (
                head is not None
                and (not isinstance(head, str) or re.fullmatch(r"[a-f0-9]{64}", head) is None)
            )
            or (after_run_id and head is None)
        ):
            raise ValueError("Invalid graph archive page")
        with self._lock, self._db:
            self._db.execute("BEGIN")
            rows = self._db.execute(
                "SELECT run_id FROM graph_runs ORDER BY run_id LIMIT 10001"
            ).fetchall()
            count, largest = self._db.execute(
                "SELECT COUNT(*),COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
            ).fetchone()
            if len(rows) > 10000 or count > 30000 or largest > 4096:
                raise ValueError("Graph archive exceeds bound")
            integrity = self._audit.require_valid()
            if head is not None and head != integrity.head_hash:
                raise GraphArchiveChanged("Graph archive changed")
            audited = set()
            for key, raw in self._db.execute("SELECT event_key,event_json FROM audit_events"):
                event = json.loads(raw)
                if (
                    not isinstance(event, dict)
                    or set(event)
                    not in (
                        {"action", "run_id", "status", "record_hash", "context_ref"},
                        {"action", "run_id", "status", "record_hash", "context_ref", "observed_at"},
                    )
                    or event["action"] != "AGENT_GRAPH_TRANSITION"
                    or event["status"] not in get_args(RunStatus)
                    or not isinstance(event["run_id"], str)
                    or key != f"graph:{event['run_id']}:{event['status']}"
                ):
                    raise ValueError("Graph archive journal identity mismatch")
                if "observed_at" in event:
                    if event["observed_at"] is None:
                        raise ValueError("Graph archive observation missing")
                    observation(event["observed_at"])
                audited.add(event["run_id"])
            identities = [row[0] for row in rows]
            if audited != set(identities):
                raise ValueError("Graph archive projection identity mismatch")
            if after_run_id and after_run_id not in identities:
                raise KeyError("Graph archive cursor missing")
            selected = [value for value in identities if value > after_run_id]
            page = selected[:limit]
            remaining = len(selected) - len(page)
            return GraphRunPage(
                head_hash=integrity.head_hash,
                total=len(rows),
                after_run_id=after_run_id,
                remaining=remaining,
                next_after=page[-1] if remaining else None,
                records=tuple(summary(self._read(value)) for value in page),
            )

    def trace(self, run_id: str):
        """Read a bounded, audit-checked timeline without execution or source access."""
        from ai_quant_os.graph_trace import (
            GraphTrace,
            GraphTraceEvent,
            observation,
            timing,
            trace_identity,
        )

        if not isinstance(run_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id) is None:
            raise ValueError("Invalid graph trace run identity")
        with self._lock, self._db:
            self._db.execute("BEGIN")
            count, largest = self._db.execute(
                "SELECT COUNT(*),COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
            ).fetchone()
            if count > 30000 or largest > 4096:
                raise ValueError("Graph trace journal exceeds archive bound")
            self._audit.require_valid()
            record = self._read(run_id)
            if record is None:
                raise KeyError("Unknown graph run")
            keys = [f"graph:{run_id}:{status}" for status in get_args(RunStatus)]
            marks = ",".join("?" for _ in keys)
            rows = self._db.execute(
                "SELECT c.sequence,c.event_hash,e.event_json FROM audit_chain c "
                "JOIN audit_events e ON e.event_key=c.event_key "
                f"WHERE e.event_key IN ({marks}) ORDER BY c.sequence",
                keys,
            ).fetchall()
            if not 1 <= len(rows) <= 3:
                raise ValueError("Graph trace transition count mismatch")
            events = []
            receipt = record.receipt
            for sequence, event_hash, raw in rows:
                event = json.loads(raw)
                # Reconstruct every historical record from the original checkpoint.
                prior = GraphRecord(
                    checkpoint=record.checkpoint,
                    receipt=GraphReceipt.model_validate(
                        {
                            **receipt.model_dump(),
                            "status": event["status"],
                            "output_json": receipt.output_json
                            if event["status"] == "COMPLETED"
                            else None,
                        }
                    ),
                )
                digest = sha256(prior.model_dump_json().encode()).hexdigest()
                if not self._matches_event(event, prior, digest):
                    raise ValueError("Graph trace historical record binding mismatch")
                events.append(
                    GraphTraceEvent(
                        sequence=sequence,
                        status=event["status"],
                        record_hash=digest,
                        event_hash=event_hash,
                        observed_at=observation(event.get("observed_at")),
                    )
                )
            events = tuple(events)
            state, elapsed = timing(events)
            output = json.loads(receipt.output_json) if receipt.output_json is not None else {}
            return GraphTrace(
                scope=record.checkpoint.context.scope,
                trace_id=trace_identity(run_id, receipt.context_ref, receipt.manifest_ref),
                run_id=run_id,
                task_id=receipt.task_id,
                agent_id=receipt.agent_id,
                agent_version=receipt.agent_version,
                context_ref=receipt.context_ref,
                manifest_ref=receipt.manifest_ref,
                input_refs=receipt.input_refs,
                context_observed_at=receipt.created_at,
                context_valid_until=receipt.valid_until,
                status=receipt.status,
                output_trace_id=output.get("trace_id"),
                output_sha256=sha256(receipt.output_json.encode()).hexdigest()
                if receipt.output_json is not None
                else None,
                events=events,
                timing_state=state,
                elapsed_seconds=elapsed,
            )

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
