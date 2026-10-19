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
from ai_quant_os.evolution_evaluation import (
    EvolutionTournament,
    EvolutionScorecard,
    build_monthly_tournament,
    encode_scorecard,
    encode_tournament,
    scorecard_sha256,
    tournament_sha256,
)
from ai_quant_os.evolution_quality import (
    TrainingQualityProfile,
    build_training_quality_profile,
    encode_training_quality_profile,
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
        if type(value) is EvolutionScorecard:
            return (
                "evolution-scorecard:" + scorecard_sha256(value),
                "SCORECARD",
                f"{value.protocol_sha256}:{value.challenger_genome_ref}",
                value.evaluated_at.isoformat(),
            )
        if type(value) is EvolutionTournament:
            return (
                "evolution-tournament:" + tournament_sha256(value),
                "TOURNAMENT",
                value.protocol_sha256,
                value.period,
            )
        if type(value) is TrainingQualityProfile:
            return (
                value.quality_profile_ref,
                "QUALITY_PROFILE",
                f"{value.protocol_sha256}:{value.candidate_genome_ref}",
                value.latest_evaluated_at.isoformat(),
            )
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
            model = {
                "DATASET": EvolutionDataset,
                "PROTOCOL": EvaluationProtocol,
                "SCORECARD": EvolutionScorecard,
                "TOURNAMENT": EvolutionTournament,
                "QUALITY_PROFILE": TrainingQualityProfile,
            }.get(kind)
            if model is None or not isinstance(raw, bytes):
                raise ValueError("Evaluation artifact type mismatch")
            value = model.model_validate_json(raw)
            encoded = (
                encode_scorecard(value)
                if type(value) is EvolutionScorecard
                else encode_tournament(value)
                if type(value) is EvolutionTournament
                else encode_training_quality_profile(value)
                if type(value) is TrainingQualityProfile
                else encode_protocol_artifact(value)
            )
            if encoded != raw or self._identity(value) != (
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
            elif type(value) is EvaluationProtocol:
                dataset = values.get(value.dataset_ref)
                if type(dataset) is not EvolutionDataset or dataset.agent_id != value.agent_id:
                    raise ValueError("Protocol requires retained same-agent dataset")
            elif type(value) is EvolutionScorecard:
                plan = values.get(value.protocol_sha256)
                dataset = values.get(value.dataset_ref)
                if (
                    type(plan) is not EvaluationProtocol
                    or type(dataset) is not EvolutionDataset
                    or plan.dataset_ref != value.dataset_ref
                ):
                    raise ValueError("Scorecard requires matching retained protocol and dataset")
            elif type(value) is EvolutionTournament:
                plan = values.get(value.protocol_sha256)
                reports = tuple(values.get(row.scorecard_ref) for row in value.rankings)
                if (
                    type(plan) is not EvaluationProtocol
                    or not reports
                    or any(type(row) is not EvolutionScorecard for row in reports)
                ):
                    raise ValueError("Tournament requires retained scorecard evidence")
                expected = build_monthly_tournament(plan, reports, period=value.period)
                if encode_tournament(expected) != encode_tournament(value):
                    raise ValueError("Tournament differs from retained scorecard inventory")
            else:
                plan = values.get(value.protocol_sha256)
                dataset = values.get(value.dataset_ref)
                reports = tuple(values.get(ref) for ref in value.scorecard_refs)
                if (
                    type(plan) is not EvaluationProtocol
                    or type(dataset) is not EvolutionDataset
                    or any(type(row) is not EvolutionScorecard for row in reports)
                ):
                    raise ValueError("Quality profile requires retained protocol and scorecards")
                expected = build_training_quality_profile(plan, dataset, reports)
                if encode_training_quality_profile(expected) != encode_training_quality_profile(
                    value
                ):
                    raise ValueError("Quality profile differs from retained scorecards")
        return values

    def register(
        self,
        value: (
            EvolutionDataset
            | EvaluationProtocol
            | EvolutionScorecard
            | EvolutionTournament
            | TrainingQualityProfile
        ),
    ) -> (
        EvolutionDataset
        | EvaluationProtocol
        | EvolutionScorecard
        | EvolutionTournament
        | TrainingQualityProfile
    ):
        ref, kind, label, version = self._identity(value)
        raw = (
            encode_scorecard(value)
            if type(value) is EvolutionScorecard
            else encode_tournament(value)
            if type(value) is EvolutionTournament
            else encode_training_quality_profile(value)
            if type(value) is TrainingQualityProfile
            else encode_protocol_artifact(value)
        )
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

    def scorecard(self, ref: str) -> EvolutionScorecard | None:
        if not isinstance(ref, str) or not re.fullmatch(r"evolution-scorecard:[a-f0-9]{64}", ref):
            raise ValueError("Canonical scorecard reference required")
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            value = self._verified(db).get(ref)
            return value if type(value) is EvolutionScorecard else None

    def tournament(self, ref: str) -> EvolutionTournament | None:
        if not isinstance(ref, str) or not re.fullmatch(r"evolution-tournament:[a-f0-9]{64}", ref):
            raise ValueError("Canonical tournament reference required")
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            value = self._verified(db).get(ref)
            return value if type(value) is EvolutionTournament else None

    def quality_profile(self, ref: str) -> TrainingQualityProfile | None:
        if not isinstance(ref, str) or not re.fullmatch(
            r"evolution-quality-profile:[a-f0-9]{64}", ref
        ):
            raise ValueError("Canonical quality profile reference required")
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            value = self._verified(db).get(ref)
            return value if type(value) is TrainingQualityProfile else None

    def training_quality_profile(
        self, protocol_sha256: str, candidate_genome_ref: str
    ) -> TrainingQualityProfile:
        if not isinstance(protocol_sha256, str) or not re.fullmatch(
            r"[a-f0-9]{64}", protocol_sha256
        ):
            raise ValueError("Canonical protocol SHA required")
        if not isinstance(candidate_genome_ref, str) or not re.fullmatch(
            r"agent-genome:[a-f0-9]{64}", candidate_genome_ref
        ):
            raise ValueError("Canonical candidate genome reference required")
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            values = self._verified(db)
        plan = values.get(protocol_sha256)
        if type(plan) is not EvaluationProtocol:
            raise ValueError("Unknown retained protocol")
        dataset = values.get(plan.dataset_ref)
        if type(dataset) is not EvolutionDataset:
            raise ValueError("Unknown retained training dataset")
        existing = tuple(
            row
            for row in values.values()
            if type(row) is TrainingQualityProfile
            and row.protocol_sha256 == protocol_sha256
            and row.candidate_genome_ref == candidate_genome_ref
        )
        scorecards = tuple(
            row
            for row in values.values()
            if type(row) is EvolutionScorecard
            and row.protocol_sha256 == protocol_sha256
            and row.challenger_genome_ref == candidate_genome_ref
        )
        if len(scorecards) < 2:
            return build_training_quality_profile(plan, dataset, scorecards)
        latest_scorecard_at = max(row.evaluated_at for row in scorecards)
        prior = next(
            (row for row in existing if row.latest_evaluated_at == latest_scorecard_at), None
        )
        if prior is not None:
            return prior
        window = tuple(
            sorted(scorecards, key=lambda row: (row.evaluated_at, row.scorecard_ref))[-32:]
        )
        return build_training_quality_profile(plan, dataset, window)

    def pending_training_quality_profiles(self, *, limit: int = 32) -> tuple[tuple[str, str], ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Quality profile publication limit must be bounded")
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            values = self._verified(db)
        scorecards: dict[tuple[str, str], list[EvolutionScorecard]] = {}
        for value in values.values():
            if type(value) is EvolutionScorecard:
                key = (value.protocol_sha256, value.challenger_genome_ref)
                scorecards.setdefault(key, []).append(value)
        published = {
            (value.protocol_sha256, value.candidate_genome_ref, value.latest_evaluated_at)
            for value in values.values()
            if type(value) is TrainingQualityProfile
        }
        pending = []
        for (protocol_sha256, candidate_genome_ref), reports in scorecards.items():
            if len(reports) < 2:
                continue
            latest = max(row.evaluated_at for row in reports)
            if (protocol_sha256, candidate_genome_ref, latest) not in published:
                pending.append((protocol_sha256, candidate_genome_ref))
        return tuple(sorted(pending)[:limit])

    def monthly_tournament(self, protocol_sha256: str, period: str) -> EvolutionTournament:
        if not isinstance(protocol_sha256, str) or not re.fullmatch(
            r"[a-f0-9]{64}", protocol_sha256
        ):
            raise ValueError("Canonical protocol SHA required")
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            values = self._verified(db)
        existing = next(
            (
                value
                for value in values.values()
                if type(value) is EvolutionTournament
                and value.protocol_sha256 == protocol_sha256
                and value.period == period
            ),
            None,
        )
        if existing is not None:
            return existing
        plan = values.get(protocol_sha256)
        if type(plan) is not EvaluationProtocol:
            raise ValueError("Unknown retained protocol")
        scorecards = tuple(
            row
            for row in values.values()
            if type(row) is EvolutionScorecard
            and row.protocol_sha256 == protocol_sha256
            and row.evaluated_at.strftime("%Y-%m") == period
        )
        if not scorecards:
            raise ValueError("No retained scorecards for the requested tournament month")
        return build_monthly_tournament(plan, scorecards, period=period)

    def pending_monthly_tournaments(
        self, before_period: str, *, limit: int = 32
    ) -> tuple[tuple[str, str], ...]:
        if (
            type(before_period) is not str
            or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", before_period)
            or type(limit) is not int
            or not 1 <= limit <= 1000
        ):
            raise ValueError("Canonical closing month and bounded tournament limit required")
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            values = self._verified(db)
        completed = {
            (value.protocol_sha256, value.period)
            for value in values.values()
            if type(value) is EvolutionTournament
        }
        eligible = {
            (value.protocol_sha256, value.evaluated_at.strftime("%Y-%m"))
            for value in values.values()
            if type(value) is EvolutionScorecard
            and value.evaluated_at.strftime("%Y-%m") < before_period
        }
        return tuple(sorted(eligible - completed)[:limit])

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
