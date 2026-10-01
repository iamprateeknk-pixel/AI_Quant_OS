"""Transactional candidate registration and withdrawal with a verified event journal."""

from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Literal

from pydantic import TypeAdapter

from ai_quant_os.research_candidates import CandidateBundle, CandidateManifest
from ai_quant_os.research_contracts import _identifier
from ai_quant_os.research_store import encode_research

ZERO = "0" * 64


@dataclass(frozen=True)
class CandidateView:
    manifest: CandidateManifest
    revision: int
    state: Literal["CHALLENGER", "FAILED", "REJECTED", "WITHDRAWN"]
    last_event_sha256: str


def _decode(raw, digest, model):
    if (
        not isinstance(raw, bytes)
        or len(raw) > 16 * 1024 * 1024
        or sha256(raw).hexdigest() != digest
    ):
        raise ValueError("Candidate archive fingerprint mismatch")
    value = TypeAdapter(model).validate_json(raw)
    if encode_research(value) != raw:
        raise ValueError("Candidate archive is not canonical")
    return value


def _event_bytes(event):
    return json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class CandidateStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS research_candidates (
                    candidate_id TEXT PRIMARY KEY, experiment_id TEXT UNIQUE NOT NULL,
                    manifest BLOB NOT NULL, manifest_sha256 TEXT NOT NULL,
                    bundle BLOB NOT NULL, bundle_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidate_events (
                    sequence INTEGER PRIMARY KEY, blob BLOB NOT NULL, sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidate_head (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), sequence INTEGER NOT NULL,
                    sha256 TEXT NOT NULL
                );
                INSERT OR IGNORE INTO candidate_head VALUES (1,0,
                    '0000000000000000000000000000000000000000000000000000000000000000');
            """)
            db.execute("BEGIN")
            self._verified(db)
            db.commit()

    def _verified(self, db):
        rows = db.execute("SELECT * FROM research_candidates ORDER BY candidate_id").fetchall()
        if any(not isinstance(row[4], bytes) for row in rows):
            raise ValueError("Candidate bundle must be an encoded artifact")
        if len(rows) > 1000 or sum(len(row[4]) for row in rows) > 64 * 1024 * 1024:
            raise ValueError("Candidate registry capacity exceeded")
        manifests = {}
        for identifier, experiment, raw, digest, bundle, bundle_digest in rows:
            manifest = _decode(raw, digest, CandidateManifest)
            record = json.loads(bundle)
            if (
                identifier != manifest.candidate_id
                or experiment != manifest.experiment_id
                or sha256(bundle).hexdigest() != bundle_digest
                or not isinstance(record, dict)
                or record.get("manifest") != json.loads(raw)
            ):
                raise ValueError("Candidate archive identity or bundle mismatch")
            manifests[identifier] = manifest
        events = db.execute(
            "SELECT sequence,blob,sha256 FROM candidate_events ORDER BY sequence"
        ).fetchall()
        if len(events) > 5000:
            raise ValueError("Candidate journal capacity exceeded")
        views, commands, previous = {}, {}, ZERO
        for sequence, raw, digest in events:
            if not isinstance(raw, bytes) or len(raw) > 4096 or sha256(raw).hexdigest() != digest:
                raise ValueError("Candidate journal fingerprint mismatch")
            event = json.loads(raw)
            if (
                not isinstance(event, dict)
                or set(event)
                != {"sequence", "candidate_id", "action", "command_id", "reason", "previous_sha256"}
                or _event_bytes(event) != raw
                or sequence != len(commands) + 1
                or event["sequence"] != sequence
                or event["previous_sha256"] != previous
                or event["candidate_id"] not in manifests
                or event["command_id"] in commands
            ):
                raise ValueError("Candidate journal lineage mismatch")
            identifier = event["candidate_id"]
            manifest = manifests[identifier]
            if event["action"] == "REGISTERED":
                if (
                    identifier in views
                    or event["command_id"] != "register:" + identifier
                    or event["reason"] != "ARCHIVED_RESEARCH_REPORT"
                ):
                    raise ValueError("Candidate registration journal mismatch")
                state = (
                    "CHALLENGER"
                    if manifest.research_outcome == "VALIDATION_INCOMPLETE"
                    else manifest.research_outcome
                )
                views[identifier] = CandidateView(manifest, 1, state, digest)
            elif event["action"] == "WITHDRAWN":
                _identifier(event["command_id"])
                if (
                    identifier not in views
                    or views[identifier].state != "CHALLENGER"
                    or not isinstance(event["reason"], str)
                    or not 1 <= len(event["reason"].strip()) <= 512
                ):
                    raise ValueError("Candidate withdrawal journal mismatch")
                views[identifier] = CandidateView(
                    manifest, views[identifier].revision + 1, "WITHDRAWN", digest
                )
            else:
                raise ValueError("Unknown candidate journal action")
            commands[event["command_id"]] = event
            previous = digest
        head = db.execute("SELECT sequence,sha256 FROM candidate_head WHERE singleton=1").fetchone()
        if head != (len(events), previous) or set(views) != set(manifests):
            raise ValueError("Candidate journal committed head mismatch")
        return views, commands

    def _append(self, db, identifier, action, command_id, reason):
        sequence, previous = db.execute(
            "SELECT sequence,sha256 FROM candidate_head WHERE singleton=1"
        ).fetchone()
        if sequence >= 5000:
            raise ValueError("Candidate journal capacity exceeded")
        event = dict(
            sequence=sequence + 1,
            candidate_id=identifier,
            action=action,
            command_id=command_id,
            reason=reason,
            previous_sha256=previous,
        )
        raw = _event_bytes(event)
        digest = sha256(raw).hexdigest()
        db.execute("INSERT INTO candidate_events VALUES (?,?,?)", (sequence + 1, raw, digest))
        db.execute(
            "UPDATE candidate_head SET sequence=?,sha256=? WHERE singleton=1",
            (sequence + 1, digest),
        )

    def register(self, bundle: CandidateBundle) -> CandidateView:
        raw = encode_research(bundle)
        digest = sha256(raw).hexdigest()
        bundle = _decode(raw, digest, CandidateBundle)
        manifest = bundle.manifest
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            views, _ = self._verified(db)
            prior = db.execute(
                "SELECT candidate_id,bundle FROM research_candidates WHERE experiment_id=?",
                (manifest.experiment_id,),
            ).fetchone()
            if prior is not None:
                if prior != (manifest.candidate_id, raw):
                    raise ValueError("Immutable candidate experiment conflict")
                return views[manifest.candidate_id]
            size = db.execute(
                "SELECT COALESCE(SUM(LENGTH(bundle)),0),COUNT(*) FROM research_candidates"
            ).fetchone()
            if size[1] >= 1000 or size[0] + len(raw) > 64 * 1024 * 1024:
                raise ValueError("Candidate registry capacity exceeded")
            manifest_raw = encode_research(manifest)
            db.execute(
                "INSERT INTO research_candidates VALUES (?,?,?,?,?,?)",
                (
                    manifest.candidate_id,
                    manifest.experiment_id,
                    manifest_raw,
                    sha256(manifest_raw).hexdigest(),
                    raw,
                    digest,
                ),
            )
            self._append(
                db,
                manifest.candidate_id,
                "REGISTERED",
                "register:" + manifest.candidate_id,
                "ARCHIVED_RESEARCH_REPORT",
            )
            views, _ = self._verified(db)
            db.commit()
            return views[manifest.candidate_id]

    def list(self) -> tuple[CandidateView, ...]:
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            views, _ = self._verified(db)
            return tuple(views[key] for key in sorted(views))

    def get(self, identifier: str) -> CandidateView | None:
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            return self._verified(db)[0].get(identifier)

    def bundle(self, identifier: str) -> CandidateBundle | None:
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            self._verified(db)
            row = db.execute(
                "SELECT bundle,bundle_sha256 FROM research_candidates WHERE candidate_id=?",
                (identifier,),
            ).fetchone()
            return None if row is None else _decode(row[0], row[1], CandidateBundle)

    def snapshot(self, identifier: str) -> tuple[CandidateView, CandidateBundle] | None:
        """Read eligibility and retained evidence from the same registry snapshot."""
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            views, _ = self._verified(db)
            row = db.execute(
                "SELECT bundle,bundle_sha256 FROM research_candidates WHERE candidate_id=?",
                (identifier,),
            ).fetchone()
            if row is None:
                return None
            return views[identifier], _decode(row[0], row[1], CandidateBundle)

    def withdraw(
        self, identifier: str, *, expected_revision: int, command_id: str, reason: str
    ) -> CandidateView:
        _identifier(command_id)
        if (
            type(expected_revision) is not int
            or expected_revision < 1
            or not isinstance(reason, str)
            or not 1 <= len(reason.strip()) <= 512
        ):
            raise ValueError("Bounded reason and expected revision required")
        reason = reason.strip()
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            views, commands = self._verified(db)
            view = views.get(identifier)
            if view is None:
                raise ValueError("Unknown candidate")
            prior = commands.get(command_id)
            if prior is not None:
                if (
                    prior["candidate_id"] != identifier
                    or prior["action"] != "WITHDRAWN"
                    or prior["reason"] != reason
                    or expected_revision != 1
                ):
                    raise ValueError("Candidate command identity conflict")
                return view
            if view.revision != expected_revision or view.state != "CHALLENGER":
                raise ValueError("Candidate revision or lifecycle conflict")
            self._append(db, identifier, "WITHDRAWN", command_id, reason)
            views, _ = self._verified(db)
            db.commit()
            return views[identifier]
