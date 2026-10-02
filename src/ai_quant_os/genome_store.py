"""Bounded transactional archive and audit-bound agent genome lineage."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from ai_quant_os.agent_genomes import (
    AgentGenome,
    EvaluationObjective,
    GENOME_REF,
    MAX_GENOME_BYTES,
    capture_baseline_genome,
    encode_genome,
    require_genome_child,
)
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.audit_chain import AuditChain


class GenomeStore:
    def __init__(self, path: Path, *, registry: AgentRegistry) -> None:
        self.path = Path(path).resolve()
        self.registry = registry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connection()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS agent_genomes (genome_ref TEXT PRIMARY KEY, agent_id TEXT NOT NULL, version TEXT NOT NULL, body BLOB NOT NULL, UNIQUE(agent_id,version))"
            )
            AuditChain(db).initialize()
            self._verified(db)

    def _connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _event(genome: AgentGenome) -> dict[str, str]:
        return {
            "action": "AGENT_GENOME_REGISTERED",
            "genome_ref": genome.genome_ref,
            "agent_id": genome.manifest.agent_id,
            "agent_version": genome.manifest.agent_version,
            "manifest_ref": genome.manifest.manifest_ref,
            "parent_ref": genome.parent_ref or "ROOT",
        }

    def _verified(self, db: sqlite3.Connection) -> dict[str, AgentGenome]:
        count, total, largest = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(body)),0),COALESCE(MAX(LENGTH(body)),0) FROM agent_genomes"
        ).fetchone()
        events, longest_event = db.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
        ).fetchone()
        if (
            count > 1000
            or total > 33554432
            or largest > MAX_GENOME_BYTES
            or events != count
            or longest_event > 2048
        ):
            raise ValueError("Genome registry capacity or audit count mismatch")
        integrity = AuditChain(db).require_valid()
        if integrity.legacy_event_count != 0:
            raise ValueError("Genome registry cannot baseline preexisting audit events")
        genomes = {}
        for ref, agent_id, version, raw in db.execute(
            "SELECT genome_ref,agent_id,version,body FROM agent_genomes ORDER BY genome_ref"
        ):
            if not isinstance(raw, bytes):
                raise ValueError("Genome archive must retain encoded bytes")
            genome = AgentGenome.model_validate_json(raw)
            if (
                encode_genome(genome) != raw
                or genome.genome_ref != ref
                or (agent_id, version) != (genome.manifest.agent_id, genome.manifest.agent_version)
            ):
                raise ValueError("Genome archive canonical identity mismatch")
            event = db.execute(
                "SELECT event_json FROM audit_events WHERE event_key=?", (ref,)
            ).fetchone()
            if event is None or json.loads(event[0]) != self._event(genome):
                raise ValueError("Genome archive audit binding mismatch")
            genomes[ref] = genome
        for genome in genomes.values():
            if genome.parent_ref is not None:
                parent = genomes.get(genome.parent_ref)
                if parent is None:
                    raise ValueError("Genome parent missing from retained lineage")
                require_genome_child(parent, genome)
        return genomes

    def register(self, genome: AgentGenome) -> AgentGenome:
        if type(genome) is not AgentGenome:
            raise ValueError("Typed agent genome required")
        raw = encode_genome(genome)
        genome = AgentGenome.model_validate_json(raw)
        with closing(self._connection()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            genomes = self._verified(db)
            prior = genomes.get(genome.genome_ref)
            if prior is not None:
                return prior
            if any(
                (row.manifest.agent_id, row.manifest.agent_version)
                == (genome.manifest.agent_id, genome.manifest.agent_version)
                for row in genomes.values()
            ):
                raise ValueError("Immutable genome version conflict")
            if genome.parent_ref is None:
                if genome != capture_baseline_genome(
                    self.registry, genome.manifest.agent_id, genome.objective
                ):
                    raise ValueError("Genome root differs from server-owned baseline")
            else:
                parent = genomes.get(genome.parent_ref)
                if parent is None:
                    raise ValueError("Genome parent must already be retained")
                require_genome_child(parent, genome)
            total = db.execute(
                "SELECT COALESCE(SUM(LENGTH(body)),0) FROM agent_genomes"
            ).fetchone()[0]
            if len(genomes) >= 1000 or total + len(raw) > 33554432:
                raise ValueError("Genome registry capacity exhausted")
            db.execute(
                "INSERT INTO agent_genomes VALUES (?,?,?,?)",
                (genome.genome_ref, genome.manifest.agent_id, genome.manifest.agent_version, raw),
            )
            AuditChain(db).append(genome.genome_ref, self._event(genome))
            self._verified(db)
        return genome

    def seed(self, agent_id: str, objective: EvaluationObjective) -> AgentGenome:
        return self.register(capture_baseline_genome(self.registry, agent_id, objective))

    def list(self) -> tuple[AgentGenome, ...]:
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            genomes = self._verified(db)
            return tuple(genomes[ref] for ref in sorted(genomes))

    def get(self, genome_ref: str) -> AgentGenome | None:
        if not isinstance(genome_ref, str) or not GENOME_REF.fullmatch(genome_ref):
            raise ValueError("Canonical genome reference required")
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            return self._verified(db).get(genome_ref)

    def lineage(self, genome_ref: str) -> tuple[AgentGenome, ...]:
        if not isinstance(genome_ref, str) or not GENOME_REF.fullmatch(genome_ref):
            raise ValueError("Canonical genome reference required")
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            genomes = self._verified(db)
            lineage, current = [], genomes.get(genome_ref)
            if current is None:
                raise ValueError("Unknown genome reference")
            while current is not None:
                lineage.append(current)
                current = genomes.get(current.parent_ref)
            return tuple(reversed(lineage))
