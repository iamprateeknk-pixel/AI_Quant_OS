"""Shadow/canary trial pipeline for independently verified repair candidates.

A repair candidate that carries a valid independent certificate
(:mod:`ai_quant_os.repair_certificates`, state
``INDEPENDENT_VERIFICATION_PASSED``) has *evidence*, not authority. This
module is the trial path that evidence must survive before activation can
even be authorized:

    SHADOW_PENDING -> SHADOW_PASSED -> CANARY_ACTIVE -> CANARY_PASSED
        -> ACTIVATION_AUTHORIZED
    SHADOW_PENDING -> SHADOW_FAILED            (terminal; never activates)
    CANARY_ACTIVE/CANARY_PASSED -> ROLLED_BACK  (incident during canary,
                                                 or an explicit rollback)

The semantics mirror the paper-recovery canary: a staged landing, a soak,
fresh gate evaluation at promotion time, and fail-closed records derived
from durable storage. Activation itself is only ever *authorized* here —
``applied`` stays False, because serving a new agent version is a catalog
release action owned outside this store. Nothing in this module runs
automatically: every transition is an explicit call, and shadow evaluation
refuses to run without an injected evaluator, so an unwired deployment
fails closed instead of inventing shadow evidence.
"""

import json
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import closing
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.health_contracts import HealthIncident
from ai_quant_os.repair_certificates import RepairCertificateVerification
from ai_quant_os.repair_snapshots import RepairSourceSnapshot, canonical_json

DEFAULT_CANARY_SOAK_SECONDS = 300

TrialState = Literal[
    "SHADOW_PENDING",
    "SHADOW_PASSED",
    "SHADOW_FAILED",
    "CANARY_ACTIVE",
    "CANARY_PASSED",
    "ACTIVATION_AUTHORIZED",
    "ROLLED_BACK",
]


class ShadowVerdict(BaseModel):
    """Shadow-stage evidence produced by an injected, independent evaluator.

    The verdict must bind the exact incident replay the certificate was
    verified against; a verdict about any other replay is refused.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluator_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    passed: StrictBool
    replay_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    summary: str = Field(min_length=1, max_length=1024)


ShadowEvaluator = Callable[["RepairTrialRecord"], ShadowVerdict]


class ShadowRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluator_id: str
    passed: StrictBool
    replay_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    summary: str = Field(min_length=1, max_length=1024)
    evaluated_at: datetime


class CanaryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: str = Field(min_length=1, max_length=128)
    entered_at: datetime
    min_soak_seconds: int = Field(strict=True, ge=0, le=86400)
    completed_at: datetime | None = None


class RollbackRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: str = Field(min_length=1, max_length=256)
    incident_id: str | None = None
    rollback_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    rolled_back_at: datetime


class ActivationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operator_ref: str = Field(min_length=1, max_length=128)
    authorized_at: datetime
    applied: Literal[False] = False
    application: Literal["REQUIRES_CATALOG_RELEASE"] = "REQUIRES_CATALOG_RELEASE"


class RepairTrialRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    trial_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    incident_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    agent_id: str = Field(pattern=r"^A(0[1-9]|1[0-8])$")
    old_version: str
    new_version: str
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rollback_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    certificate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verifier_id: str
    verifier_verdict: Literal["PASS"] = "PASS"
    replay_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: TrialState
    created_at: datetime
    updated_at: datetime
    shadow: ShadowRecord | None = None
    canary: CanaryRecord | None = None
    rollback: RollbackRecord | None = None
    activation: ActivationRecord | None = None
    promotion_allowed: Literal[False] = False

    @field_validator("promotion_allowed", mode="before")
    @classmethod
    def deny_authority(cls, value: object) -> bool:
        if value is not False:
            raise ValueError("Repair trial records never carry promotion authority")
        return False


class RepairIncidentClosure(BaseModel):
    """Closure record linking an incident to the repair that resolved it.

    incident -> candidate -> certificate (verifier verdict) -> shadow
    evidence -> canary evidence -> activation authorization. The health
    journal remains the incident system of record; ``journal_writeback``
    is False because writing the closure into the journal is the health
    store owner's step, not this store's.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    state: Literal["CLOSED_REPAIR_ACTIVATED"] = "CLOSED_REPAIR_ACTIVATED"
    incident_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    trial_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    certificate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verifier_id: str
    verifier_verdict: Literal["PASS"] = "PASS"
    shadow_evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    canary_entered_at: datetime
    canary_soak_seconds: int = Field(strict=True, ge=0, le=31536000)
    incidents_during_canary: Literal[0] = 0
    activation_operator_ref: str
    closed_at: datetime
    journal_writeback: Literal[False] = False

    @property
    def closure_ref(self) -> str:
        raw = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        return "repair-closure:" + sha256(raw).hexdigest()


def _trial_id(candidate_id: str, snapshot_sha256: str, certificate_sha256: str) -> str:
    identity = {
        "candidate_id": candidate_id,
        "snapshot_sha256": snapshot_sha256,
        "certificate_sha256": certificate_sha256,
    }
    return sha256(canonical_json(identity).encode()).hexdigest()


class RepairTrialStore:
    """Durable, hash-checked trial records with an audited transition log."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connection()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS repair_trials "
                "(trial_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS repair_closures "
                "(incident_id TEXT PRIMARY KEY, closure_json TEXT NOT NULL, "
                "closure_sha256 TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS audit_events "
                "(event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
            )
            AuditChain(db).initialize()
            self._verified(db)

    def _connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _verified(self, db: sqlite3.Connection) -> None:
        if AuditChain(db).require_valid().legacy_event_count:
            raise ValueError("Repair trial store cannot baseline prior audit data")
        for record_json, record_sha256 in db.execute(
            "SELECT record_json, record_sha256 FROM repair_trials"
        ):
            if sha256(record_json.encode()).hexdigest() != record_sha256:
                raise ValueError("Repair trial record integrity mismatch")
            RepairTrialRecord.model_validate_json(record_json)
        for closure_json, closure_sha256 in db.execute(
            "SELECT closure_json, closure_sha256 FROM repair_closures"
        ):
            if sha256(closure_json.encode()).hexdigest() != closure_sha256:
                raise ValueError("Repair closure record integrity mismatch")
            RepairIncidentClosure.model_validate_json(closure_json)

    def _append_event(self, db: sqlite3.Connection, record: RepairTrialRecord) -> None:
        AuditChain(db).append(
            f"repair-trial:{record.trial_id}:{record.state}:{record.updated_at.isoformat()}",
            {
                "action": "REPAIR_TRIAL_" + record.state,
                "trial_id": record.trial_id,
                "candidate_id": record.candidate_id,
                "incident_id": record.incident_id,
            },
        )

    def _save(self, db: sqlite3.Connection, record: RepairTrialRecord) -> RepairTrialRecord:
        raw = record.model_dump_json()
        digest = sha256(raw.encode()).hexdigest()
        db.execute(
            "INSERT INTO repair_trials (trial_id, record_json, record_sha256) VALUES (?, ?, ?) "
            "ON CONFLICT(trial_id) DO UPDATE SET record_json = excluded.record_json, "
            "record_sha256 = excluded.record_sha256",
            (record.trial_id, raw, digest),
        )
        self._append_event(db, record)
        return record

    def _transition(
        self, trial_id: str, expected: set[str], **changes: object
    ) -> RepairTrialRecord:
        with closing(self._connection()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._verified(db)
            record = self._read(db, trial_id)
            if record.state not in expected:
                raise ValueError(
                    f"Repair trial in state {record.state} cannot accept this transition"
                )
            updated = record.model_copy(update=changes)
            return self._save(db, updated)

    @staticmethod
    def _read(db: sqlite3.Connection, trial_id: str) -> RepairTrialRecord:
        row = db.execute(
            "SELECT record_json, record_sha256 FROM repair_trials WHERE trial_id = ?", (trial_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown repair trial")
        if sha256(row[0].encode()).hexdigest() != row[1]:
            raise ValueError("Repair trial record integrity mismatch")
        return RepairTrialRecord.model_validate_json(row[0])

    def get(self, trial_id: str) -> RepairTrialRecord:
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            return self._read(db, trial_id)

    def create_trial(
        self,
        snapshot: RepairSourceSnapshot,
        verification: RepairCertificateVerification,
        *,
        replay_sha256: str,
        created_at: datetime,
    ) -> RepairTrialRecord:
        """Open a trial for one candidate, gated on independent verification.

        Only a certificate verification in state
        ``INDEPENDENT_VERIFICATION_PASSED`` for this exact snapshot opens a
        trial; a failed or mismatched verification is refused, so an
        unverified candidate can never enter shadow.
        """
        if type(snapshot) is not RepairSourceSnapshot:
            raise ValueError("A typed repair source snapshot is required")
        if type(verification) is not RepairCertificateVerification:
            raise ValueError("A typed repair certificate verification is required")
        if verification.state != "INDEPENDENT_VERIFICATION_PASSED":
            raise ValueError("Repair trial requires independent verification to have passed")
        if verification.snapshot_sha256 != snapshot.snapshot_sha256:
            raise ValueError("Certificate verification does not bind this candidate snapshot")
        manifest = snapshot.proposed_manifest
        incident = snapshot.incident
        record = RepairTrialRecord(
            trial_id=_trial_id(
                snapshot.candidate_id, snapshot.snapshot_sha256, verification.certificate_sha256
            ),
            candidate_id=snapshot.candidate_id,
            incident_id=str(incident["incident_id"]),
            agent_id=str(manifest["agent_id"]),
            old_version=str(incident["agent_version"]),
            new_version=str(manifest["agent_version"]),
            snapshot_sha256=snapshot.snapshot_sha256,
            rollback_commit=snapshot.rollback_commit,
            certificate_sha256=verification.certificate_sha256,
            verifier_id=verification.verifier_id,
            replay_sha256=replay_sha256,
            state="SHADOW_PENDING",
            created_at=created_at,
            updated_at=created_at,
        )
        with closing(self._connection()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._verified(db)
            existing = db.execute(
                "SELECT record_json FROM repair_trials WHERE trial_id = ?", (record.trial_id,)
            ).fetchone()
            if existing is not None:
                return RepairTrialRecord.model_validate_json(existing[0])
            return self._save(db, record)

    def evaluate_shadow(
        self,
        trial_id: str,
        evaluator: ShadowEvaluator | None,
        *,
        evaluated_at: datetime,
    ) -> RepairTrialRecord:
        """Run the shadow stage through an injected independent evaluator.

        No evaluator wired means no shadow evidence can exist, so the call
        refuses and the trial stays in SHADOW_PENDING (fail closed).
        """
        if evaluator is None:
            raise ValueError("Shadow evaluation requires an injected independent evaluator")
        record = self.get(trial_id)
        if record.state != "SHADOW_PENDING":
            raise ValueError(f"Repair trial in state {record.state} cannot enter shadow evaluation")
        verdict = evaluator(record)
        if type(verdict) is not ShadowVerdict:
            raise ValueError("Shadow evaluator must return a typed ShadowVerdict")
        if verdict.replay_sha256 != record.replay_sha256:
            raise ValueError("Shadow verdict does not bind the trial's incident replay")
        shadow = ShadowRecord(
            evaluator_id=verdict.evaluator_id,
            passed=verdict.passed,
            replay_sha256=verdict.replay_sha256,
            evidence_refs=verdict.evidence_refs,
            summary=verdict.summary,
            evaluated_at=evaluated_at,
        )
        return self._transition(
            trial_id,
            {"SHADOW_PENDING"},
            state="SHADOW_PASSED" if verdict.passed else "SHADOW_FAILED",
            shadow=shadow,
            updated_at=evaluated_at,
        )

    def start_canary(
        self,
        trial_id: str,
        *,
        scope: str,
        entered_at: datetime,
        min_soak_seconds: int = DEFAULT_CANARY_SOAK_SECONDS,
    ) -> RepairTrialRecord:
        canary = CanaryRecord(
            scope=scope, entered_at=entered_at, min_soak_seconds=min_soak_seconds
        )
        return self._transition(
            trial_id, {"SHADOW_PASSED"}, state="CANARY_ACTIVE", canary=canary, updated_at=entered_at
        )

    def evaluate_canary(
        self,
        trial_id: str,
        *,
        now: datetime,
        incidents: Iterable[HealthIncident],
    ) -> RepairTrialRecord:
        """Fresh canary gate: an incident for the agent ends the trial.

        Any health incident for the trialled agent detected at or after
        canary entry (other than the incident under repair itself) rolls
        the trial back to the pre-repair commit. Otherwise the trial passes
        once the soak has elapsed; before that it stays CANARY_ACTIVE.
        """
        record = self.get(trial_id)
        if record.state != "CANARY_ACTIVE" or record.canary is None:
            raise ValueError(f"Repair trial in state {record.state} has no active canary to evaluate")
        for incident in incidents:
            if (
                incident.agent_id == record.agent_id
                and incident.detected_at >= record.canary.entered_at
                and getattr(incident, "incident_id", None) != record.incident_id
            ):
                rollback = RollbackRecord(
                    reason="INCIDENT_DURING_CANARY",
                    incident_id=getattr(incident, "incident_id", None),
                    rollback_commit=record.rollback_commit,
                    rolled_back_at=now,
                )
                return self._transition(
                    trial_id,
                    {"CANARY_ACTIVE"},
                    state="ROLLED_BACK",
                    rollback=rollback,
                    updated_at=now,
                )
        elapsed = (now - record.canary.entered_at).total_seconds()
        if elapsed < record.canary.min_soak_seconds:
            return record
        completed = record.canary.model_copy(update={"completed_at": now})
        return self._transition(
            trial_id, {"CANARY_ACTIVE"}, state="CANARY_PASSED", canary=completed, updated_at=now
        )

    def rollback(self, trial_id: str, *, reason: str, at: datetime) -> RepairTrialRecord:
        record = self.get(trial_id)
        rollback = RollbackRecord(
            reason=reason, rollback_commit=record.rollback_commit, rolled_back_at=at
        )
        return self._transition(
            trial_id,
            {"CANARY_ACTIVE", "CANARY_PASSED"},
            state="ROLLED_BACK",
            rollback=rollback,
            updated_at=at,
        )

    def authorize_activation(
        self, trial_id: str, *, operator_ref: str, authorized_at: datetime
    ) -> RepairTrialRecord:
        """The enforced-promotion step: explicit, and authorization only.

        Mirrors the recovery canary promotion: every prior gate must be
        green in the durable record, an operator must name themselves, and
        the outcome is an authorization artifact — the served-version flip
        itself remains a catalog release action outside this store.
        """
        activation = ActivationRecord(operator_ref=operator_ref, authorized_at=authorized_at)
        return self._transition(
            trial_id,
            {"CANARY_PASSED"},
            state="ACTIVATION_AUTHORIZED",
            activation=activation,
            updated_at=authorized_at,
        )

    def close_incident(self, trial_id: str, *, closed_at: datetime) -> RepairIncidentClosure:
        record = self.get(trial_id)
        if (
            record.state != "ACTIVATION_AUTHORIZED"
            or record.shadow is None
            or record.canary is None
            or record.canary.completed_at is None
            or record.activation is None
        ):
            raise ValueError("Incident closure requires an activation-authorized trial")
        closure = RepairIncidentClosure(
            incident_id=record.incident_id,
            candidate_id=record.candidate_id,
            trial_id=record.trial_id,
            certificate_sha256=record.certificate_sha256,
            verifier_id=record.verifier_id,
            shadow_evidence_refs=record.shadow.evidence_refs,
            canary_entered_at=record.canary.entered_at,
            canary_soak_seconds=int(
                (record.canary.completed_at - record.canary.entered_at).total_seconds()
            ),
            activation_operator_ref=record.activation.operator_ref,
            closed_at=closed_at,
        )
        raw = closure.model_dump_json()
        with closing(self._connection()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._verified(db)
            existing = db.execute(
                "SELECT closure_json FROM repair_closures WHERE incident_id = ?",
                (record.incident_id,),
            ).fetchone()
            if existing is not None:
                return RepairIncidentClosure.model_validate_json(existing[0])
            db.execute(
                "INSERT INTO repair_closures (incident_id, closure_json, closure_sha256) "
                "VALUES (?, ?, ?)",
                (record.incident_id, raw, sha256(raw.encode()).hexdigest()),
            )
            AuditChain(db).append(
                f"repair-closure:{record.incident_id}",
                {
                    "action": "REPAIR_INCIDENT_CLOSED",
                    "trial_id": record.trial_id,
                    "candidate_id": record.candidate_id,
                    "incident_id": record.incident_id,
                },
            )
        return closure

    def closure_for(self, incident_id: str) -> RepairIncidentClosure | None:
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT closure_json, closure_sha256 FROM repair_closures WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if row is None:
                return None
            if sha256(row[0].encode()).hexdigest() != row[1]:
                raise ValueError("Repair closure record integrity mismatch")
            return RepairIncidentClosure.model_validate_json(row[0])
