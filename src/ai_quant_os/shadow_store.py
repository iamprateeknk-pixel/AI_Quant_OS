"""Transactional shadow assignments, sequential reservations and verified evidence chains."""

from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
from uuid import uuid4

from ai_quant_os.research_store import _decode, encode_research
from ai_quant_os.shadow_portfolio import (
    ShadowAssignment,
    ShadowReport,
    ShadowRequest,
    verify_forward_source,
)

ZERO = "0" * 64
DISABLED_REALITY_GAP_POLICY = b"DISABLED"


class ShadowStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS shadow_assignments (
                    shadow_id TEXT PRIMARY KEY, blob BLOB NOT NULL, sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shadow_batches (
                    shadow_id TEXT NOT NULL, command_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    owner_token TEXT NOT NULL, request BLOB NOT NULL, request_sha256 TEXT NOT NULL,
                    report BLOB, report_sha256 TEXT,
                    PRIMARY KEY(shadow_id,command_id), UNIQUE(shadow_id,sequence)
                );
                CREATE TABLE IF NOT EXISTS shadow_heads (
                    shadow_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL, sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shadow_reality_gap_policies (
                    shadow_id TEXT PRIMARY KEY, blob BLOB NOT NULL, sha256 TEXT NOT NULL
                );
            """)
            db.execute(
                "INSERT OR IGNORE INTO shadow_reality_gap_policies "
                "SELECT shadow_id,?,? FROM shadow_assignments",
                (DISABLED_REALITY_GAP_POLICY, sha256(DISABLED_REALITY_GAP_POLICY).hexdigest()),
            )
            db.commit()

    @staticmethod
    def _capacity(db, additional):
        size, count = db.execute(
            "SELECT COALESCE(SUM(length(blob)),0), COUNT(*) FROM shadow_assignments"
        ).fetchone()
        batches, batch_count = db.execute(
            "SELECT COALESCE(SUM(length(request)+COALESCE(length(report),0)),0), COUNT(*) FROM shadow_batches"
        ).fetchone()
        policies = db.execute(
            "SELECT COALESCE(SUM(length(blob)),0) FROM shadow_reality_gap_policies"
        ).fetchone()[0]
        if (
            size + batches + policies + additional > 64 * 1024 * 1024
            or count > 1000
            or batch_count > 1000
        ):
            raise ValueError("Shadow archive capacity exceeded")

    @staticmethod
    def _decode_reality_gap_policy(db, shadow_id):
        from ai_quant_os.shadow_reality_gap import RealityGapPolicy

        row = db.execute(
            "SELECT blob,sha256 FROM shadow_reality_gap_policies WHERE shadow_id=?",
            (shadow_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Shadow reality-gap policy record is missing")
        raw, digest = row
        if not isinstance(raw, bytes) or sha256(raw).hexdigest() != digest:
            raise ValueError("Shadow reality-gap policy fingerprint mismatch")
        if raw == DISABLED_REALITY_GAP_POLICY:
            return None
        return _decode(raw, digest, RealityGapPolicy)

    def _verified(self, db, shadow_id):
        row = db.execute(
            "SELECT blob,sha256 FROM shadow_assignments WHERE shadow_id=?", (shadow_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown shadow assignment")
        assignment = _decode(*row, ShadowAssignment)
        if assignment.shadow_id != shadow_id:
            raise ValueError("Shadow assignment identity mismatch")
        self._decode_reality_gap_policy(db, shadow_id)
        rows = db.execute(
            "SELECT command_id,sequence,owner_token,request,request_sha256,report,report_sha256 FROM shadow_batches WHERE shadow_id=? ORDER BY sequence",
            (shadow_id,),
        ).fetchall()
        if len(rows) > 1000:
            raise ValueError("Shadow history capacity exceeded")
        previous, entries = None, {}
        for index, row in enumerate(rows, 1):
            command, sequence, token, raw, digest, report_raw, report_digest = row
            request = _decode(raw, digest, ShadowRequest)
            if (
                request.shadow_id != shadow_id
                or request.command_id != command
                or sequence != index
                or not isinstance(token, str)
                or len(token) != 32
            ):
                raise ValueError("Shadow reservation identity mismatch")
            if previous is not None and previous.outcome != "OBSERVED":
                raise ValueError("Shadow history continues after failed evidence")
            combined = (previous.source_bars if previous is not None else ()) + request.bars
            verify_forward_source(assignment, combined)
            if report_raw is None:
                if index != len(rows) or report_digest is not None:
                    raise ValueError("Uncertain shadow history cannot continue")
                report = None
            else:
                report = _decode(report_raw, report_digest, ShadowReport)
                if (
                    report.assignment != assignment
                    or report.request != request
                    or report.source_bars != combined
                    or report.sequence != sequence
                    or report.previous_sha256
                    != (previous.fingerprint if previous is not None else ZERO)
                ):
                    raise ValueError("Shadow report lineage mismatch")
                self._verify_prefix(previous, report)
                previous = report
            entries[command] = (token, request, report, sequence)
        head = db.execute(
            "SELECT sequence,sha256 FROM shadow_heads WHERE shadow_id=?", (shadow_id,)
        ).fetchone()
        expected = (previous.sequence, previous.fingerprint) if previous is not None else (0, ZERO)
        if head != expected:
            raise ValueError("Shadow committed head mismatch")
        return assignment, previous, entries

    @staticmethod
    def _verify_prefix(previous, report):
        if previous is not None and report.observed_at < previous.observed_at:
            raise ValueError("Shadow observation clock cannot regress")
        if previous is not None and report.run is not None:
            count = len(previous.run.result.fills)
            if report.run.result.fills[:count] != previous.run.result.fills or any(
                fill.observed_at <= previous.source_bars[-1].observed_at
                for fill in report.run.result.fills[count:]
            ):
                raise ValueError("Shadow prior fill prefix cannot change")

    def assign(self, assignment, *, reality_gap_policy=None):
        from ai_quant_os.shadow_reality_gap import RealityGapPolicy

        if reality_gap_policy is not None and type(reality_gap_policy) is not RealityGapPolicy:
            raise ValueError("Typed shadow reality-gap policy required")
        policy_raw = (
            DISABLED_REALITY_GAP_POLICY
            if reality_gap_policy is None
            else encode_research(reality_gap_policy)
        )
        policy_digest = sha256(policy_raw).hexdigest()
        raw = encode_research(assignment)
        assignment = _decode(raw, sha256(raw).hexdigest(), ShadowAssignment)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT blob FROM shadow_assignments WHERE shadow_id=?", (assignment.shadow_id,)
            ).fetchone()
            if prior is not None:
                existing, _, _ = self._verified(db, assignment.shadow_id)
                bound_policy = db.execute(
                    "SELECT blob,sha256 FROM shadow_reality_gap_policies WHERE shadow_id=?",
                    (assignment.shadow_id,),
                ).fetchone()
                if (
                    existing != assignment
                    or prior[0] != raw
                    or bound_policy != (policy_raw, policy_digest)
                ):
                    raise ValueError(
                        "Immutable shadow assignment or reality-gap policy cannot change"
                    )
                return existing
            self._capacity(db, len(raw) + len(policy_raw))
            if db.execute("SELECT COUNT(*) FROM shadow_assignments").fetchone()[0] >= 1000:
                raise ValueError("Shadow assignment capacity exceeded")
            db.execute(
                "INSERT INTO shadow_assignments VALUES (?,?,?)",
                (assignment.shadow_id, raw, sha256(raw).hexdigest()),
            )
            db.execute("INSERT INTO shadow_heads VALUES (?,0,?)", (assignment.shadow_id, ZERO))
            db.execute(
                "INSERT INTO shadow_reality_gap_policies VALUES (?,?,?)",
                (assignment.shadow_id, policy_raw, policy_digest),
            )
            db.commit()
            return assignment

    def assignment(self, shadow_id):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            return self._verified(db, shadow_id)[0]

    def reality_gap_policy(self, shadow_id):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            self._verified(db, shadow_id)
            return self._decode_reality_gap_policy(db, shadow_id)

    def latest(self, shadow_id):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            return self._verified(db, shadow_id)[1]

    def lookup(self, shadow_id):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT shadow_id FROM shadow_assignments WHERE shadow_id=?", (shadow_id,)
            ).fetchone()
            return None if row is None else self._verified(db, shadow_id)[0]

    def cached(self, request):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            _, _, entries = self._verified(db, request.shadow_id)
            prior = entries.get(request.command_id)
            if prior is None:
                return None
            if prior[1] != request:
                raise ValueError("Immutable shadow command cannot change")
            return prior[2]

    def reserve(self, request):
        raw = encode_research(request)
        request = _decode(raw, sha256(raw).hexdigest(), ShadowRequest)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            assignment, previous, entries = self._verified(db, request.shadow_id)
            prior = entries.get(request.command_id)
            if prior is not None:
                if prior[1] != request:
                    raise ValueError("Immutable shadow command cannot change")
                if prior[2] is None:
                    raise ValueError("Uncertain shadow reservation requires investigation")
                return None, assignment, prior[2].source_bars, previous, prior[2]
            if any(entry[2] is None for entry in entries.values()):
                raise ValueError("Uncertain shadow reservation blocks new observations")
            if previous is not None and previous.outcome == "FAILED":
                raise ValueError("A failed shadow observation blocks new execution")
            bars = (previous.source_bars if previous is not None else ()) + request.bars
            verify_forward_source(assignment, bars)
            self._capacity(db, len(raw))
            if db.execute("SELECT COUNT(*) FROM shadow_batches").fetchone()[0] >= 1000:
                raise ValueError("Shadow observation capacity exceeded")
            token = uuid4().hex
            db.execute(
                "INSERT INTO shadow_batches VALUES (?,?,?,?,?,?,NULL,NULL)",
                (
                    request.shadow_id,
                    request.command_id,
                    len(entries) + 1,
                    token,
                    raw,
                    sha256(raw).hexdigest(),
                ),
            )
            db.commit()
            return token, assignment, bars, previous, None

    def finish(self, token, report):
        raw = encode_research(report)
        report = _decode(raw, sha256(raw).hexdigest(), ShadowReport)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            assignment, previous, entries = self._verified(db, report.assignment.shadow_id)
            entry = entries.get(report.request.command_id)
            if (
                entry is None
                or entry[0] != token
                or entry[2] is not None
                or entry[1] != report.request
                or entry[3] != report.sequence
                or assignment != report.assignment
                or report.previous_sha256
                != (previous.fingerprint if previous is not None else ZERO)
                or report.source_bars
                != (previous.source_bars if previous is not None else ()) + report.request.bars
            ):
                raise ValueError("Shadow reservation ownership/lineage mismatch")
            self._verify_prefix(previous, report)
            self._capacity(db, len(raw))
            db.execute(
                "UPDATE shadow_batches SET report=?,report_sha256=? WHERE shadow_id=? AND command_id=?",
                (
                    raw,
                    sha256(raw).hexdigest(),
                    report.assignment.shadow_id,
                    report.request.command_id,
                ),
            )
            db.execute(
                "UPDATE shadow_heads SET sequence=?,sha256=? WHERE shadow_id=?",
                (report.sequence, report.fingerprint, report.assignment.shadow_id),
            )
            self._verified(db, report.assignment.shadow_id)
            db.commit()
