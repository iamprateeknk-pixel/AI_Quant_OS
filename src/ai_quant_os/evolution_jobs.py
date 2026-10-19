"""Durable, training-only admission for controlled genome evaluation jobs."""

from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_genomes import AgentGenome, require_genome_child
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.evolution_catalog import EvolutionCatalog
from ai_quant_os.evolution_protocols import (
    EvaluationProtocol,
    EvolutionDataset,
    require_protocol_genome,
)
from ai_quant_os.evolution_evaluation import CaseOutput, evaluate_protocol_outputs
from ai_quant_os.genome_store import GenomeStore
from ai_quant_os.evolution_runner import EvolutionRunner

ZERO = "0" * 64
MAX_JOBS = 1000
MAX_JOB_BYTES = 8 * 1024 * 1024


class EvolutionTrainingJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    job_ref: str = Field(pattern=r"^evolution-job:[a-f0-9]{64}$")
    protocol_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_ref: str = Field(pattern=r"^evolution-dataset:[a-f0-9]{64}$")
    baseline_genome_ref: str = Field(pattern=r"^agent-genome:[a-f0-9]{64}$")
    challenger_genome_ref: str = Field(pattern=r"^agent-genome:[a-f0-9]{64}$")
    created_at: AwareDatetime
    updated_at: AwareDatetime
    state: Literal["PENDING", "RUNNING", "COMPLETED", "FAILED"] = "PENDING"
    scorecard_ref: str | None = Field(default=None, pattern=r"^evolution-scorecard:[a-f0-9]{64}$")
    failure_code: Literal["TRAINING_EXECUTION_FAILED"] | None = None
    recovery_count: int = Field(default=0, ge=0, le=3)
    recovery_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    recovery_reason: str | None = Field(default=None, min_length=1, max_length=212)
    recovery_expected_updated_at: AwareDatetime | None = None
    evaluation_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @model_validator(mode="after")
    def consistent_state(self):
        valid = self.updated_at >= self.created_at
        if self.state in {"PENDING", "RUNNING"}:
            valid = valid and self.scorecard_ref is None and self.failure_code is None
        elif self.state == "COMPLETED":
            valid = valid and self.scorecard_ref is not None and self.failure_code is None
        else:
            valid = valid and self.scorecard_ref is None and self.failure_code is not None
        recovery_values = (
            self.recovery_id,
            self.recovery_reason,
            self.recovery_expected_updated_at,
        )
        valid = valid and (
            all(value is None for value in recovery_values)
            or (
                all(value is not None for value in recovery_values)
                and self.recovery_count > 0
                and self.recovery_reason == self.recovery_reason.strip()
            )
        )
        if not valid:
            raise ValueError("Evolution training job state is inconsistent")
        return self


def evolution_training_job_schema() -> dict:
    schema = EvolutionTrainingJob.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://ai-quant-os.invalid/schemas/evolution-training-job.schema.json"
    schema["title"] = "Evolution training job"
    schema["description"] = (
        "Audited local TRAINING evaluation job; verification and promotion are never granted."
    )
    return schema


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _job_identity(protocol_sha256: str, baseline_ref: str, challenger_ref: str) -> str:
    raw = _canonical([protocol_sha256, baseline_ref, challenger_ref])
    return "evolution-job:" + sha256(raw).hexdigest()


def _event(
    sequence: int,
    job: EvolutionTrainingJob,
    action: str,
    previous_sha256: str,
    *,
    operator_assertion: dict | None = None,
) -> dict:
    event = {
        "sequence": sequence,
        "job_ref": job.job_ref,
        "job": job.model_dump(mode="json"),
        "action": action,
        "previous_sha256": previous_sha256,
    }
    if operator_assertion is not None:
        event["operator_assertion"] = operator_assertion
    return event


class EvolutionJobQueue:
    """Audited training-only queue; execution uses the pinned offline runner."""

    def __init__(
        self,
        catalog: EvolutionCatalog,
        genomes: GenomeStore,
        *,
        path: Path | None = None,
        clock,
    ) -> None:
        if type(catalog) is not EvolutionCatalog or type(genomes) is not GenomeStore:
            raise ValueError("Evolution job queue requires retained catalogs")
        if not callable(clock) or (path is not None and not isinstance(path, Path)):
            raise ValueError("Evolution job queue requires a clock and local path")
        self.catalog = catalog
        self.genomes = genomes
        self.path = (path or catalog.path.with_name("training-jobs.sqlite3")).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        with closing(self._connection()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS training_jobs (job_ref TEXT PRIMARY KEY, body BLOB NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS job_events (sequence INTEGER PRIMARY KEY, event_json TEXT NOT NULL, event_sha256 TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS job_head (singleton INTEGER PRIMARY KEY CHECK(singleton=1), sequence INTEGER NOT NULL, event_sha256 TEXT NOT NULL)"
            )
            db.execute("INSERT OR IGNORE INTO job_head VALUES (1,0,?)", (ZERO,))
            self._verified(db)

    def _connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _verified(self, db: sqlite3.Connection) -> dict[str, EvolutionTrainingJob]:
        rows = db.execute("SELECT job_ref,body FROM training_jobs ORDER BY job_ref").fetchall()
        events = db.execute(
            "SELECT sequence,event_json,event_sha256 FROM job_events ORDER BY sequence"
        ).fetchall()
        total = sum(len(body) for _, body in rows if isinstance(body, bytes))
        if (
            len(rows) > MAX_JOBS
            or total > MAX_JOB_BYTES
            or any(not isinstance(body, bytes) for _, body in rows)
        ):
            raise ValueError("Evolution job queue capacity or audit count mismatch")
        jobs = {}
        for ref, raw in rows:
            job = EvolutionTrainingJob.model_validate_json(raw)
            if (
                _canonical(job.model_dump(mode="json")) != raw
                or ref != job.job_ref
                or ref
                != _job_identity(
                    job.protocol_sha256,
                    job.baseline_genome_ref,
                    job.challenger_genome_ref,
                )
            ):
                raise ValueError("Evolution job identity or canonical body mismatch")
            jobs[ref] = job
        replayed = {}
        recovery_ids = set()
        previous = ZERO
        for index, (sequence, raw, digest) in enumerate(events, start=1):
            try:
                value = json.loads(raw)
                job = EvolutionTrainingJob.model_validate(value["job"])
                prior = replayed.get(job.job_ref)
                action = value["action"]
                valid_transition = (
                    (
                        action == "TRAINING_EVALUATION_QUEUED"
                        and prior is None
                        and job.state == "PENDING"
                        and job.created_at == job.updated_at
                    )
                    or (
                        action == "TRAINING_EVALUATION_STARTED"
                        and prior is not None
                        and prior.state == "PENDING"
                        and job.state == "RUNNING"
                    )
                    or (
                        action == "TRAINING_EVALUATION_COMPLETED"
                        and prior is not None
                        and prior.state == "RUNNING"
                        and job.state == "COMPLETED"
                        and job.scorecard_ref is not None
                    )
                    or (
                        action == "TRAINING_EVALUATION_FAILED"
                        and prior is not None
                        and prior.state == "RUNNING"
                        and job.state == "FAILED"
                        and job.failure_code is not None
                    )
                    or (
                        action == "TRAINING_EVALUATION_RECOVERED"
                        and prior is not None
                        and prior.state == "RUNNING"
                        and job.state == "PENDING"
                        and job.recovery_count == prior.recovery_count + 1
                        and job.recovery_id is not None
                        and job.recovery_reason is not None
                        and job.recovery_expected_updated_at == prior.updated_at
                    )
                )
                if prior is not None and (
                    (
                        job.protocol_sha256,
                        job.dataset_ref,
                        job.baseline_genome_ref,
                        job.challenger_genome_ref,
                        job.created_at,
                    )
                    != (
                        prior.protocol_sha256,
                        prior.dataset_ref,
                        prior.baseline_genome_ref,
                        prior.challenger_genome_ref,
                        prior.created_at,
                    )
                    or job.updated_at < prior.updated_at
                ):
                    valid_transition = False
                if prior is not None and action != "TRAINING_EVALUATION_RECOVERED":
                    if (
                        job.recovery_count,
                        job.recovery_id,
                        job.recovery_reason,
                        job.recovery_expected_updated_at,
                    ) != (
                        prior.recovery_count,
                        prior.recovery_id,
                        prior.recovery_reason,
                        prior.recovery_expected_updated_at,
                    ):
                        valid_transition = False
                assertion = (
                    {"worker_stopped": True} if action == "TRAINING_EVALUATION_RECOVERED" else None
                )
                expected = _event(index, job, action, previous, operator_assertion=assertion)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                raise ValueError("Evolution job audit chain is invalid") from None
            if (
                sequence != index
                or _canonical(value).decode() != raw
                or value != expected
                or not valid_transition
                or job.job_ref not in jobs
                or job.job_ref
                != _job_identity(
                    job.protocol_sha256,
                    job.baseline_genome_ref,
                    job.challenger_genome_ref,
                )
                or digest != sha256(raw.encode()).hexdigest()
            ):
                raise ValueError("Evolution job audit chain is invalid")
            if action == "TRAINING_EVALUATION_RECOVERED":
                if job.recovery_id in recovery_ids:
                    raise ValueError("Evolution job recovery command was reused")
                recovery_ids.add(job.recovery_id)
            replayed[job.job_ref] = job
            previous = digest
        head = db.execute("SELECT sequence,event_sha256 FROM job_head WHERE singleton=1").fetchone()
        if head != (len(events), previous) or replayed != jobs:
            raise ValueError("Evolution job audit head mismatch")
        return jobs

    def enqueue(
        self, protocol_sha256: str, baseline_genome_ref: str, challenger_genome_ref: str
    ) -> EvolutionTrainingJob:
        plan = self.catalog.protocol(protocol_sha256)
        if type(plan) is not EvaluationProtocol:
            raise ValueError("Unknown retained protocol")
        registry = load_agent_catalog()
        dataset = self.catalog.dataset(
            plan.dataset_ref,
            registry=registry,
            agent_id="A09",
            agent_version="0.2.0",
        )
        if type(dataset) is not EvolutionDataset or dataset.resource_class != "TRAINING":
            raise ValueError("Evolution jobs accept retained TRAINING datasets only")
        baseline = self.genomes.get(baseline_genome_ref)
        challenger = self.genomes.get(challenger_genome_ref)
        if type(baseline) is not AgentGenome or type(challenger) is not AgentGenome:
            raise ValueError("Unknown baseline or challenger genome")
        if baseline.parent_ref is not None:
            raise ValueError("Training evaluation baseline must be the archived root genome")
        require_protocol_genome(plan, baseline)
        require_protocol_genome(plan, challenger)
        require_genome_child(baseline, challenger)
        if dataset.agent_id != plan.agent_id:
            raise ValueError("Protocol and training dataset agent identities differ")

        job_ref = _job_identity(protocol_sha256, baseline_genome_ref, challenger_genome_ref)
        now = self.clock()
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Evolution job clock must include a timezone")
        now = now.astimezone(timezone.utc)
        with closing(self._connection()) as db:
            db.execute("BEGIN IMMEDIATE")
            jobs = self._verified(db)
            prior = jobs.get(job_ref)
            if prior is not None:
                return prior
            if len(jobs) >= MAX_JOBS:
                raise ValueError("Evolution job queue capacity exhausted")
            job = EvolutionTrainingJob(
                job_ref=job_ref,
                protocol_sha256=protocol_sha256,
                dataset_ref=dataset.dataset_ref,
                baseline_genome_ref=baseline_genome_ref,
                challenger_genome_ref=challenger_genome_ref,
                created_at=now,
                updated_at=now,
            )
            raw = _canonical(job.model_dump(mode="json"))
            stored_bytes = db.execute(
                "SELECT COALESCE(SUM(LENGTH(body)),0) FROM training_jobs"
            ).fetchone()[0]
            if stored_bytes + len(raw) > MAX_JOB_BYTES:
                raise ValueError("Evolution job queue capacity exhausted")
            db.execute("INSERT INTO training_jobs VALUES (?,?)", (job.job_ref, raw))
            sequence, previous = db.execute(
                "SELECT sequence,event_sha256 FROM job_head WHERE singleton=1"
            ).fetchone()
            event_json = _canonical(
                _event(sequence + 1, job, "TRAINING_EVALUATION_QUEUED", previous)
            ).decode()
            digest = sha256(event_json.encode()).hexdigest()
            db.execute("INSERT INTO job_events VALUES (?,?,?)", (sequence + 1, event_json, digest))
            db.execute(
                "UPDATE job_head SET sequence=?,event_sha256=? WHERE singleton=1",
                (sequence + 1, digest),
            )
            self._verified(db)
            db.commit()
            return job

    def _append_transition(
        self,
        current: EvolutionTrainingJob,
        updated: EvolutionTrainingJob,
        action: str,
        *,
        operator_assertion: dict | None = None,
    ) -> EvolutionTrainingJob:
        with closing(self._connection()) as db:
            db.execute("BEGIN IMMEDIATE")
            jobs = self._verified(db)
            if jobs.get(current.job_ref) != current:
                raise ValueError("Evolution training job state changed")
            raw = _canonical(updated.model_dump(mode="json"))
            db.execute("UPDATE training_jobs SET body=? WHERE job_ref=?", (raw, updated.job_ref))
            sequence, previous = db.execute(
                "SELECT sequence,event_sha256 FROM job_head WHERE singleton=1"
            ).fetchone()
            event_json = _canonical(
                _event(
                    sequence + 1,
                    updated,
                    action,
                    previous,
                    operator_assertion=operator_assertion,
                )
            ).decode()
            digest = sha256(event_json.encode()).hexdigest()
            db.execute("INSERT INTO job_events VALUES (?,?,?)", (sequence + 1, event_json, digest))
            db.execute(
                "UPDATE job_head SET sequence=?,event_sha256=? WHERE singleton=1",
                (sequence + 1, digest),
            )
            self._verified(db)
            db.commit()
            return updated

    def recover_running(
        self,
        job_ref: str,
        *,
        expected_updated_at: datetime,
        recovery_id: str,
        reason: str,
        worker_stopped: bool,
    ) -> EvolutionTrainingJob:
        """Return a stale RUNNING job to PENDING after an explicit stop assertion."""
        if worker_stopped is not True:
            raise ValueError("Operator must confirm the prior worker stopped")
        if (
            not isinstance(job_ref, str)
            or not re.fullmatch(r"evolution-job:[a-f0-9]{64}", job_ref)
            or type(expected_updated_at) is not datetime
            or expected_updated_at.tzinfo is None
            or expected_updated_at.utcoffset() is None
            or not isinstance(recovery_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", recovery_id)
            or not isinstance(reason, str)
            or not 1 <= len(reason.strip()) <= 212
        ):
            raise ValueError("Bounded recovery identity, revision and reason are required")
        expected_updated_at = expected_updated_at.astimezone(timezone.utc)
        reason = reason.strip()
        now = self._now()
        with closing(self._connection()) as db:
            db.execute("BEGIN IMMEDIATE")
            jobs = self._verified(db)
            current = jobs.get(job_ref)
            event_rows = db.execute(
                "SELECT event_json FROM job_events ORDER BY sequence"
            ).fetchall()
            for (raw_event,) in event_rows:
                event = json.loads(raw_event)
                if event.get("action") != "TRAINING_EVALUATION_RECOVERED":
                    continue
                prior_event_job = EvolutionTrainingJob.model_validate(event["job"])
                if prior_event_job.recovery_id != recovery_id:
                    continue
                if (
                    prior_event_job.job_ref != job_ref
                    or prior_event_job.recovery_reason != reason
                    or prior_event_job.recovery_expected_updated_at != expected_updated_at
                ):
                    raise ValueError("Evolution recovery command identity conflict")
                if current is None:
                    raise ValueError("Evolution recovery job disappeared")
                return current

            if current is None:
                raise ValueError("Unknown evolution training job")
            if current.state != "RUNNING" or current.updated_at != expected_updated_at:
                raise ValueError("Evolution training job revision or state conflict")
            if current.recovery_count >= 3:
                raise ValueError("Evolution training job recovery limit reached")
            if now < current.updated_at:
                raise ValueError("Evolution job recovery clock regressed")
            updated = current.model_copy(
                update={
                    "state": "PENDING",
                    "updated_at": now,
                    "recovery_count": current.recovery_count + 1,
                    "recovery_id": recovery_id,
                    "recovery_reason": reason,
                    "recovery_expected_updated_at": expected_updated_at,
                }
            )
            raw = _canonical(updated.model_dump(mode="json"))
            db.execute("UPDATE training_jobs SET body=? WHERE job_ref=?", (raw, job_ref))
            sequence, previous = db.execute(
                "SELECT sequence,event_sha256 FROM job_head WHERE singleton=1"
            ).fetchone()
            event_json = _canonical(
                _event(
                    sequence + 1,
                    updated,
                    "TRAINING_EVALUATION_RECOVERED",
                    previous,
                    operator_assertion={"worker_stopped": True},
                )
            ).decode()
            digest = sha256(event_json.encode()).hexdigest()
            db.execute("INSERT INTO job_events VALUES (?,?,?)", (sequence + 1, event_json, digest))
            db.execute(
                "UPDATE job_head SET sequence=?,event_sha256=? WHERE singleton=1",
                (sequence + 1, digest),
            )
            self._verified(db)
            db.commit()
            return updated

    def claim_next(self) -> EvolutionTrainingJob | None:
        now = self._now()
        with closing(self._connection()) as db:
            db.execute("BEGIN IMMEDIATE")
            jobs = self._verified(db)
            pending = sorted(
                (job for job in jobs.values() if job.state == "PENDING"),
                key=lambda item: (item.created_at, item.job_ref),
            )
            if not pending:
                return None
            current = pending[0]
            updated = current.model_copy(update={"state": "RUNNING", "updated_at": now})
            raw = _canonical(updated.model_dump(mode="json"))
            db.execute("UPDATE training_jobs SET body=? WHERE job_ref=?", (raw, updated.job_ref))
            sequence, previous = db.execute(
                "SELECT sequence,event_sha256 FROM job_head WHERE singleton=1"
            ).fetchone()
            event_json = _canonical(
                _event(sequence + 1, updated, "TRAINING_EVALUATION_STARTED", previous)
            ).decode()
            digest = sha256(event_json.encode()).hexdigest()
            db.execute("INSERT INTO job_events VALUES (?,?,?)", (sequence + 1, event_json, digest))
            db.execute(
                "UPDATE job_head SET sequence=?,event_sha256=? WHERE singleton=1",
                (sequence + 1, digest),
            )
            self._verified(db)
            db.commit()
            return updated

    def _now(self) -> datetime:
        now = self.clock()
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Evolution job clock must include a timezone")
        return now.astimezone(timezone.utc)

    def process_next(self) -> EvolutionTrainingJob | None:
        """Run one claimed pair; interrupted RUNNING jobs are never replayed."""
        job = self.claim_next()
        if job is None:
            return None
        try:
            plan = self.catalog.protocol(job.protocol_sha256)
            if type(plan) is not EvaluationProtocol:
                raise ValueError("Retained protocol disappeared")
            registry = load_agent_catalog()
            dataset = self.catalog.dataset(
                job.dataset_ref,
                registry=registry,
                agent_id="A09",
                agent_version="0.2.0",
            )
            if type(dataset) is not EvolutionDataset or dataset.resource_class != "TRAINING":
                raise ValueError("Training job dataset is no longer training-only")
            baseline = self.genomes.get(job.baseline_genome_ref)
            challenger = self.genomes.get(job.challenger_genome_ref)
            if type(baseline) is not AgentGenome or type(challenger) is not AgentGenome:
                raise ValueError("Training job genome disappeared")
            if baseline.parent_ref is not None:
                raise ValueError("Training job baseline is no longer a root genome")
            require_protocol_genome(plan, baseline)
            require_protocol_genome(plan, challenger)
            require_genome_child(baseline, challenger)
            runner = EvolutionRunner()
            baseline_result = runner.run(baseline, dataset, plan)
            challenger_result = runner.run(challenger, dataset, plan, baseline=baseline)
            scorecard = evaluate_protocol_outputs(
                plan,
                dataset,
                baseline,
                challenger,
                self._case_outputs(baseline_result.outputs),
                self._case_outputs(challenger_result.outputs),
                evaluated_at=self._now(),
            )
            retained = self.catalog.register(scorecard)
            return self._finish(job, state="COMPLETED", scorecard_ref=retained.scorecard_ref)
        except Exception:
            return self._finish(job, state="FAILED", failure_code="TRAINING_EXECUTION_FAILED")

    def _finish(
        self,
        job: EvolutionTrainingJob,
        *,
        state: Literal["COMPLETED", "FAILED"],
        scorecard_ref: str | None = None,
        failure_code: Literal["TRAINING_EXECUTION_FAILED"] | None = None,
    ) -> EvolutionTrainingJob:
        updated = job.model_copy(
            update={
                "state": state,
                "updated_at": self._now(),
                "scorecard_ref": scorecard_ref,
                "failure_code": failure_code,
            }
        )
        action = (
            "TRAINING_EVALUATION_COMPLETED"
            if state == "COMPLETED"
            else "TRAINING_EVALUATION_FAILED"
        )
        return self._append_transition(job, updated, action)

    @staticmethod
    def _case_outputs(rows) -> tuple[CaseOutput, ...]:
        return tuple(
            CaseOutput(
                case_id=row["case_id"],
                output_json=json.dumps(
                    row["output"], sort_keys=True, separators=(",", ":"), allow_nan=False
                ),
            )
            for row in rows
        )

    def list_jobs(self) -> tuple[EvolutionTrainingJob, ...]:
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            jobs = self._verified(db)
            return tuple(sorted(jobs.values(), key=lambda item: (item.created_at, item.job_ref)))
