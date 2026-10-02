"""Audited retained evaluation artifacts with current agent read admission."""

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.evolution_protocols import (
    EvolutionDataset,
    EvaluationProtocol,
    encode_protocol_artifact,
)


class EvolutionCatalog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connection()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS audit_events (event_key TEXT PRIMARY KEY,event_json TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS evolution_artifacts (ref TEXT PRIMARY KEY,kind TEXT NOT NULL,label TEXT NOT NULL,version TEXT NOT NULL,body BLOB NOT NULL,UNIQUE(kind,label,version))"
            )
            AuditChain(db).initialize()
            self._verified(db)

    def _connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _identity(value):
        if type(value) is EvolutionDataset:
            return value.dataset_ref, "DATASET", value.dataset_id, value.dataset_version
        if type(value) is EvaluationProtocol:
            return value.protocol_sha256, "PROTOCOL", value.protocol_id, value.protocol_version
        raise ValueError("Typed evaluation artifact required")

    @staticmethod
    def _event(value):
        ref, kind, label, version = EvolutionCatalog._identity(value)
        return {
            "action": "EVOLUTION_ARTIFACT_REGISTERED",
            "ref": ref,
            "kind": kind,
            "label": label,
            "version": version,
        }

    def _verified(self, db):
        count, total, largest = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(body)),0),COALESCE(MAX(LENGTH(body)),0) FROM evolution_artifacts"
        ).fetchone()
        events, maximum = db.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
        ).fetchone()
        if (
            count > 1000
            or total > 33554432
            or largest > 1048576
            or events != count
            or maximum > 2048
        ):
            raise ValueError("Evaluation catalog capacity/audit count mismatch")
        if AuditChain(db).require_valid().legacy_event_count:
            raise ValueError("Evaluation catalog cannot baseline prior audit data")
        values = {}
        for ref, kind, label, version, raw in db.execute(
            "SELECT ref,kind,label,version,body FROM evolution_artifacts"
        ):
            model = {"DATASET": EvolutionDataset, "PROTOCOL": EvaluationProtocol}.get(kind)
            if model is None or not isinstance(raw, bytes):
                raise ValueError("Evaluation artifact type mismatch")
            value = model.model_validate_json(raw)
            if encode_protocol_artifact(value) != raw or self._identity(value) != (
                ref,
                kind,
                label,
                version,
            ):
                raise ValueError("Evaluation artifact canonical identity mismatch")
            event = db.execute(
                "SELECT event_json FROM audit_events WHERE event_key=?", (ref,)
            ).fetchone()
            if event is None or json.loads(event[0]) != self._event(value):
                raise ValueError("Evaluation artifact audit binding mismatch")
            values[ref] = value
        inputs = {}
        for value in values.values():
            if type(value) is EvolutionDataset:
                for case in value.cases:
                    prior = inputs.setdefault(case.input_sha256, value.resource_class)
                    if prior != value.resource_class:
                        raise ValueError("Training/holdout input overlap")
            else:
                dataset = values.get(value.dataset_ref)
                if type(dataset) is not EvolutionDataset or dataset.agent_id != value.agent_id:
                    raise ValueError("Protocol requires retained same-agent dataset")
        return values

    def register(
        self, value: EvolutionDataset | EvaluationProtocol
    ) -> EvolutionDataset | EvaluationProtocol:
        ref, kind, label, version = self._identity(value)
        raw = encode_protocol_artifact(value)
        value = type(value).model_validate_json(raw)
        with closing(self._connection()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            values = self._verified(db)
            if ref in values:
                return values[ref]
            if any(
                self._identity(prior)[1:] == (kind, label, version) for prior in values.values()
            ):
                raise ValueError("Immutable evaluation artifact version conflict")
            total = db.execute(
                "SELECT COALESCE(SUM(LENGTH(body)),0) FROM evolution_artifacts"
            ).fetchone()[0]
            if len(values) >= 1000 or total + len(raw) > 33554432:
                raise ValueError("Evaluation catalog capacity exhausted")
            db.execute(
                "INSERT INTO evolution_artifacts VALUES (?,?,?,?,?)",
                (ref, kind, label, version, raw),
            )
            AuditChain(db).append(ref, self._event(value))
            self._verified(db)
        return value

    def protocol(self, digest: str) -> EvaluationProtocol | None:
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Canonical protocol SHA required")
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            value = self._verified(db).get(digest)
            return value if type(value) is EvaluationProtocol else None

    def dataset(
        self, ref: str, *, registry: AgentRegistry, agent_id: str, agent_version: str
    ) -> EvolutionDataset:
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            value = self._verified(db).get(ref)
            if type(value) is not EvolutionDataset:
                raise ValueError("Unknown retained evaluation dataset")
            tool = "HOLDOUT_READ" if value.resource_class == "HOLDOUT" else "TRAINING_READ"
            registry.require_tool(agent_id, agent_version, tool, scope="SANDBOX")
            return value

    def inventory(self) -> tuple[dict, ...]:
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            values = self._verified(db)
            return tuple(self._event(values[ref]) for ref in sorted(values))
