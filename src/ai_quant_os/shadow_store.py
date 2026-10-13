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
    ShadowRecovery,
    ShadowRequest,
    ShadowRetirement,
    _identifier,
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
                CREATE TABLE IF NOT EXISTS shadow_retirements (
                    shadow_id TEXT PRIMARY KEY, blob BLOB NOT NULL, sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shadow_recoveries (
                    shadow_id TEXT NOT NULL, sequence INTEGER NOT NULL, recovery_id TEXT NOT NULL,
                    blob BLOB NOT NULL, sha256 TEXT NOT NULL,
                    PRIMARY KEY(shadow_id,sequence), UNIQUE(shadow_id,recovery_id)
                );
                CREATE TABLE IF NOT EXISTS shadow_recovery_heads (
                    shadow_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL, sha256 TEXT NOT NULL
                );
            """)
            db.execute(
                "INSERT OR IGNORE INTO shadow_reality_gap_policies "
                "SELECT shadow_id,?,? FROM shadow_assignments",
                (DISABLED_REALITY_GAP_POLICY, sha256(DISABLED_REALITY_GAP_POLICY).hexdigest()),
            )
            db.execute(
                "INSERT OR IGNORE INTO shadow_recovery_heads "
                "SELECT shadow_id,0,? FROM shadow_assignments",
                (ZERO,),
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
        retirements = db.execute(
            "SELECT COALESCE(SUM(length(blob)),0) FROM shadow_retirements"
        ).fetchone()[0]
        recoveries = db.execute(
            "SELECT COALESCE(SUM(length(blob)),0) FROM shadow_recoveries"
        ).fetchone()[0]
        if (
            size + batches + policies + retirements + recoveries + additional > 64 * 1024 * 1024
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
        retirement_row = db.execute(
            "SELECT blob,sha256 FROM shadow_retirements WHERE shadow_id=?", (shadow_id,)
        ).fetchone()
        if retirement_row is not None:
            retirement = _decode(*retirement_row, ShadowRetirement)
            if (
                retirement.shadow_id != shadow_id
                or (retirement.final_sequence, retirement.final_sha256) != expected
                or retirement.retired_at < assignment.assigned_at
                or (previous is not None and retirement.retired_at < previous.observed_at)
                or any(entry[2] is None for entry in entries.values())
            ):
                raise ValueError("Shadow retirement lineage mismatch")
        recovery_rows = db.execute(
            "SELECT sequence,recovery_id,blob,sha256 FROM shadow_recoveries "
            "WHERE shadow_id=? ORDER BY sequence",
            (shadow_id,),
        ).fetchall()
        if len(recovery_rows) > 1000:
            raise ValueError("Shadow recovery history capacity exceeded")
        recovery_previous = ZERO
        recovery_commands = {}
        recovery_ids = set()
        for index, (sequence, recovery_id, raw, digest) in enumerate(recovery_rows, 1):
            recovery = _decode(raw, digest, ShadowRecovery)
            entry = entries.get(recovery.command_id)
            request_digest = (
                sha256(encode_research(entry[1])).hexdigest() if entry is not None else None
            )
            prior_for_command = recovery_commands.get(recovery.command_id)
            if (
                recovery.shadow_id != shadow_id
                or recovery_id != recovery.recovery_id
                or recovery_id in recovery_ids
                or sequence != index
                or recovery.sequence != sequence
                or recovery.previous_sha256 != recovery_previous
                or entry is None
                or recovery.request_sha256 != request_digest
                or recovery.recovered_at < assignment.assigned_at
                or (entry[2] is not None and recovery.recovered_at > entry[2].observed_at)
                or (
                    prior_for_command is not None
                    and recovery.previous_owner_sha256 != prior_for_command.owner_sha256
                )
            ):
                raise ValueError("Shadow recovery lineage mismatch")
            recovery_ids.add(recovery_id)
            recovery_commands[recovery.command_id] = recovery
            recovery_previous = digest
        recovery_head = db.execute(
            "SELECT sequence,sha256 FROM shadow_recovery_heads WHERE shadow_id=?", (shadow_id,)
        ).fetchone()
        if recovery_head != (len(recovery_rows), recovery_previous):
            raise ValueError("Shadow recovery committed head mismatch")
        for command_id, recovery in recovery_commands.items():
            if sha256(entries[command_id][0].encode()).hexdigest() != recovery.owner_sha256:
                raise ValueError("Shadow recovered owner token mismatch")
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
                "INSERT INTO shadow_recovery_heads VALUES (?,0,?)", (assignment.shadow_id, ZERO)
            )
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

    def retirement(self, shadow_id):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            self._verified(db, shadow_id)
            row = db.execute(
                "SELECT blob,sha256 FROM shadow_retirements WHERE shadow_id=?", (shadow_id,)
            ).fetchone()
            return None if row is None else _decode(*row, ShadowRetirement)

    def retire(self, shadow_id, command_id, reason, *, retired_at):
        retirement = ShadowRetirement(shadow_id, command_id, reason, retired_at, 0, ZERO)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            assignment, previous, entries = self._verified(db, shadow_id)
            prior = db.execute(
                "SELECT blob,sha256 FROM shadow_retirements WHERE shadow_id=?", (shadow_id,)
            ).fetchone()
            if prior is not None:
                existing = _decode(*prior, ShadowRetirement)
                if existing.command_id != command_id or existing.reason != retirement.reason:
                    raise ValueError("Shadow retirement command identity conflict")
                return existing
            if any(entry[2] is None for entry in entries.values()):
                raise ValueError("Uncertain shadow reservation blocks retirement")
            final_sequence = previous.sequence if previous is not None else 0
            final_sha256 = previous.fingerprint if previous is not None else ZERO
            if retired_at < assignment.assigned_at or (
                previous is not None and retired_at < previous.observed_at
            ):
                raise ValueError("Shadow retirement time cannot precede retained evidence")
            retirement = ShadowRetirement(
                shadow_id,
                command_id,
                reason,
                retired_at,
                final_sequence,
                final_sha256,
            )
            raw = encode_research(retirement)
            self._capacity(db, len(raw))
            db.execute(
                "INSERT INTO shadow_retirements VALUES (?,?,?)",
                (shadow_id, raw, sha256(raw).hexdigest()),
            )
            db.commit()
            return retirement

    def recoveries(self, shadow_id):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN")
            self._verified(db, shadow_id)
            rows = db.execute(
                "SELECT blob,sha256 FROM shadow_recoveries WHERE shadow_id=? ORDER BY sequence",
                (shadow_id,),
            ).fetchall()
            return tuple(_decode(raw, digest, ShadowRecovery) for raw, digest in rows)

    def recover_reservation(self, request, recovery_id, reason, *, recovered_at):
        if type(request) is not ShadowRequest:
            raise ValueError("Typed shadow reservation is required for recovery")
        _identifier(recovery_id)
        if not isinstance(reason, str):
            raise ValueError("A bounded recovery reason is required")
        reason = reason.strip()
        if not reason or len(reason) > 212:
            raise ValueError("A bounded recovery reason is required")
        raw_request = encode_research(request)
        request = _decode(raw_request, sha256(raw_request).hexdigest(), ShadowRequest)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            assignment, previous, entries = self._verified(db, request.shadow_id)
            entry = entries.get(request.command_id)
            if entry is None or entry[1] != request:
                raise ValueError("Exact retained shadow reservation is required for recovery")
            prior_recovery_row = db.execute(
                "SELECT blob,sha256 FROM shadow_recoveries WHERE shadow_id=? AND recovery_id=?",
                (request.shadow_id, recovery_id),
            ).fetchone()
            if prior_recovery_row is not None:
                prior_recovery = _decode(*prior_recovery_row, ShadowRecovery)
                if (
                    prior_recovery.command_id != request.command_id
                    or prior_recovery.request_sha256 != sha256(raw_request).hexdigest()
                    or prior_recovery.reason != reason
                    or prior_recovery.owner_sha256 != sha256(entry[0].encode()).hexdigest()
                ):
                    raise ValueError("Shadow recovery command identity conflict")
                return (
                    None if entry[2] is not None else entry[0],
                    assignment,
                    entry[2].source_bars
                    if entry[2] is not None
                    else self._recovery_source(previous, request, assignment),
                    previous,
                    entry[2],
                )
            if entry[2] is not None:
                raise ValueError("Completed shadow reservation does not require recovery")
            if any(other[2] is None for key, other in entries.items() if key != request.command_id):
                raise ValueError("Another uncertain shadow reservation blocks recovery")
            if db.execute(
                "SELECT 1 FROM shadow_retirements WHERE shadow_id=?", (request.shadow_id,)
            ).fetchone():
                raise ValueError("Shadow assignment is retired")

            source = self._recovery_source(previous, request, assignment)
            current_token = entry[0]
            replacement_token = uuid4().hex
            recovery_rows = db.execute(
                "SELECT sequence,sha256 FROM shadow_recoveries WHERE shadow_id=? ORDER BY sequence",
                (request.shadow_id,),
            ).fetchall()
            if len(recovery_rows) >= 1000:
                raise ValueError("Shadow recovery capacity exceeded")
            recovery = ShadowRecovery(
                request.shadow_id,
                recovery_id,
                request.command_id,
                sha256(raw_request).hexdigest(),
                reason,
                sha256(current_token.encode()).hexdigest(),
                sha256(replacement_token.encode()).hexdigest(),
                len(recovery_rows) + 1,
                recovery_rows[-1][1] if recovery_rows else ZERO,
                recovered_at,
            )
            raw = encode_research(recovery)
            digest = sha256(raw).hexdigest()
            self._capacity(db, len(raw))
            db.execute(
                "UPDATE shadow_batches SET owner_token=? WHERE shadow_id=? AND command_id=?",
                (replacement_token, request.shadow_id, request.command_id),
            )
            db.execute(
                "INSERT INTO shadow_recoveries VALUES (?,?,?,?,?)",
                (request.shadow_id, recovery.sequence, recovery_id, raw, digest),
            )
            db.execute(
                "UPDATE shadow_recovery_heads SET sequence=?,sha256=? WHERE shadow_id=?",
                (recovery.sequence, digest, request.shadow_id),
            )
            self._verified(db, request.shadow_id)
            db.commit()
            return replacement_token, assignment, source, previous, None

    @staticmethod
    def _recovery_source(previous, request, assignment):
        source = (previous.source_bars if previous is not None else ()) + request.bars
        verify_forward_source(assignment, source)
        return source

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
            if db.execute(
                "SELECT 1 FROM shadow_retirements WHERE shadow_id=?", (request.shadow_id,)
            ).fetchone():
                raise ValueError("Shadow assignment is retired")
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
