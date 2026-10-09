"""Audited routine-call reservations committed before tool effects."""

import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from ai_quant_os.agent_call_policy import AgentCallPolicy
from ai_quant_os.agent_permissions import KNOWN_TOOLS
from ai_quant_os.agent_registry import ToolInvocation
from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.contracts import _require_aware


def _encode(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class AgentCallStore:
    def __init__(self, path: Path, policy: AgentCallPolicy | None) -> None:
        self.path = Path(path).resolve()
        self.anchor_path = self.path.with_suffix(self.path.suffix + ".pin.json")
        created = False
        if not self.path.exists() and not self.anchor_path.exists():
            if policy is None:
                raise ValueError("Call budget archive is absent")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            anchor = {
                "schema_version": "1.0",
                "budget_id": uuid4().hex,
                "policy_sha256": policy.fingerprint,
            }
            with self.anchor_path.open("xb") as output:
                output.write(_encode(anchor))
                output.flush()
                os.fsync(output.fileno())
            created = True
        raw = self._read_anchor()
        self.anchor = json.loads(raw)
        if (
            type(self.anchor) is not dict
            or set(self.anchor) != {"schema_version", "budget_id", "policy_sha256"}
            or self.anchor["schema_version"] != "1.0"
            or not isinstance(self.anchor["budget_id"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", self.anchor["budget_id"])
            or not isinstance(self.anchor["policy_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", self.anchor["policy_sha256"])
        ):
            raise ValueError("Invalid call budget anchor")
        if not created and not self.path.is_file():
            raise ValueError("Original call budget database is missing")
        with closing(self._connection()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            if created:
                db.execute(
                    "CREATE TABLE audit_events (event_key TEXT PRIMARY KEY,event_json TEXT NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE agent_call_policy (singleton INTEGER PRIMARY KEY CHECK(singleton=1),body BLOB NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE agent_call_reservations (reservation_id TEXT PRIMARY KEY,body BLOB NOT NULL)"
                )
                AuditChain(db).initialize()
                db.execute("INSERT INTO agent_call_policy VALUES (1,?)", (policy.encode(),))
                AuditChain(db).append("agent-call-policy", self._policy_event())
            retained, _, _ = self._verified(db)
            if policy is not None and retained != policy:
                raise ValueError("Call policy is immutable; migration required")

    def _read_anchor(self):
        with self.anchor_path.open("rb") as source:
            raw = source.read(257)
        if len(raw) > 256:
            raise ValueError("Invalid call budget anchor")
        return raw

    def _connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _policy_event(self):
        return {"action": "AGENT_CALL_POLICY_PINNED", **self.anchor}

    @staticmethod
    def _event(record):
        return {
            "action": "AGENT_ROUTINE_CALL_RESERVED",
            "reservation_id": record["reservation_id"],
            "sha256": sha256(_encode(record)).hexdigest(),
        }

    def _verified(self, db):
        if self._read_anchor() != _encode(self.anchor):
            raise ValueError("Call budget anchor changed")
        count, total, largest = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(body)),0),COALESCE(MAX(LENGTH(body)),0) FROM agent_call_reservations"
        ).fetchone()
        event_count, event_size = db.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
        ).fetchone()
        policies, policy_size = db.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(body)),0) FROM agent_call_policy"
        ).fetchone()
        if (
            count > 20000
            or total > 33554432
            or largest > 1024
            or event_count != count + 1
            or event_size > 512
            or policies != 1
            or policy_size > 65536
        ):
            raise ValueError("Call budget archive capacity or audit count mismatch")
        integrity = AuditChain(db).require_valid()
        if integrity.legacy_event_count:
            raise ValueError("Call budget cannot baseline unaudited events")
        row = db.execute("SELECT body FROM agent_call_policy WHERE singleton=1").fetchone()
        if row is None:
            raise ValueError("Call budget policy missing")
        policy = AgentCallPolicy.decode(row[0])
        if row[0] != policy.encode() or policy.fingerprint != self.anchor["policy_sha256"]:
            raise ValueError("Call budget policy pin mismatch")
        events = {
            key: json.loads(raw)
            for key, raw in db.execute("SELECT event_key,event_json FROM audit_events")
        }
        if events.get("agent-call-policy") != self._policy_event():
            raise ValueError("Call budget policy audit mismatch")
        rules = {row.agent_id: row for row in policy.rules}
        rows = []
        counts = {}
        last = None
        fields = {
            "reservation_id",
            "agent_id",
            "agent_version",
            "tool",
            "scope",
            "request_sha256",
            "reserved_at",
        }
        for key, raw in db.execute(
            "SELECT reservation_id,body FROM agent_call_reservations ORDER BY rowid"
        ):
            record = json.loads(raw)
            if type(record) is not dict or set(record) != fields or _encode(record) != raw:
                raise ValueError("Call reservation encoding mismatch")
            if (
                key != record["reservation_id"]
                or not isinstance(key, str)
                or not re.fullmatch(r"[a-f0-9]{32}", key)
                or not isinstance(record["agent_id"], str)
                or record["agent_id"] not in rules
                or not isinstance(record["tool"], str)
                or record["tool"] not in KNOWN_TOOLS
                or record["scope"] not in ("PAPER", "SANDBOX")
                or not isinstance(record["agent_version"], str)
                or not re.fullmatch(
                    r"(?:0|[1-9][0-9]{0,5})(?:\.(?:0|[1-9][0-9]{0,5})){2}", record["agent_version"]
                )
                or not isinstance(record["request_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", record["request_sha256"])
                or not isinstance(record["reserved_at"], str)
                or events.get(key) != self._event(record)
            ):
                raise ValueError("Call reservation identity or audit mismatch")
            stamp = datetime.fromisoformat(record["reserved_at"])
            _require_aware(stamp)
            if stamp.astimezone(timezone.utc).isoformat() != record["reserved_at"] or (
                last is not None and stamp < last
            ):
                raise ValueError("Call reservation clock mismatch")
            last = stamp
            agent = record["agent_id"]
            rule = rules[agent]
            for period, ceiling in (
                (stamp.date().isoformat(), rule.daily_calls - rule.daily_recovery_reserve),
                (stamp.date().isoformat()[:7], rule.monthly_calls - rule.monthly_recovery_reserve),
            ):
                counter = (agent, period)
                counts[counter] = counts.get(counter, 0) + 1
                if counts[counter] > ceiling:
                    raise ValueError("Retained call reservations exceed policy")
            rows.append(record)
        return policy, tuple(rows), integrity.head_hash

    def reserve(self, invocation: ToolInvocation, now: datetime) -> None:
        _require_aware(now)
        now = now.astimezone(timezone.utc)
        request = {
            "agent_id": invocation.agent_id,
            "agent_version": invocation.agent_version,
            "tool": invocation.tool,
            "scope": invocation.scope,
            "payload": invocation.payload,
            "context_ref": invocation.context_ref,
            "task_id": invocation.task_id,
        }
        record = {
            "reservation_id": uuid4().hex,
            "agent_id": invocation.agent_id,
            "agent_version": invocation.agent_version,
            "tool": invocation.tool,
            "scope": invocation.scope,
            "request_sha256": sha256(_encode(request)).hexdigest(),
            "reserved_at": now.isoformat(),
        }
        with closing(self._connection()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            policy, rows, _ = self._verified(db)
            if rows and now < datetime.fromisoformat(rows[-1]["reserved_at"]):
                raise ValueError("Call budget clock moved backwards")
            rule = next(row for row in policy.rules if row.agent_id == invocation.agent_id)
            agent_rows = [row for row in rows if row["agent_id"] == invocation.agent_id]
            daily = sum(row["reserved_at"][:10] == now.date().isoformat() for row in agent_rows)
            monthly = sum(
                row["reserved_at"][:7] == now.date().isoformat()[:7] for row in agent_rows
            )
            if (
                daily >= rule.daily_calls - rule.daily_recovery_reserve
                or monthly >= rule.monthly_calls - rule.monthly_recovery_reserve
            ):
                raise PermissionError("Agent call budget exhausted")
            if len(rows) >= 20000:
                raise ValueError("Call budget archive capacity exhausted")
            db.execute(
                "INSERT INTO agent_call_reservations VALUES (?,?)",
                (record["reservation_id"], _encode(record)),
            )
            AuditChain(db).append(record["reservation_id"], self._event(record))
            self._verified(db)

    def status(self, now: datetime) -> dict[str, object]:
        _require_aware(now)
        now = now.astimezone(timezone.utc)
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            policy, rows, head = self._verified(db)
            if rows and now < datetime.fromisoformat(rows[-1]["reserved_at"]):
                raise ValueError("Call budget clock moved backwards")
            agents = []
            for rule in policy.rules:
                selected = [row for row in rows if row["agent_id"] == rule.agent_id]
                daily = sum(row["reserved_at"][:10] == now.date().isoformat() for row in selected)
                monthly = sum(
                    row["reserved_at"][:7] == now.date().isoformat()[:7] for row in selected
                )
                agents.append(
                    {
                        "agent_id": rule.agent_id,
                        "daily_calls": daily,
                        "monthly_calls": monthly,
                        "daily_remaining": rule.daily_calls - rule.daily_recovery_reserve - daily,
                        "monthly_remaining": rule.monthly_calls
                        - rule.monthly_recovery_reserve
                        - monthly,
                        "daily_recovery_reserve": rule.daily_recovery_reserve,
                        "monthly_recovery_reserve": rule.monthly_recovery_reserve,
                    }
                )
            return {
                "state": "ENFORCED",
                "day": now.date().isoformat(),
                "month": now.date().isoformat()[:7],
                "head_hash": head,
                "policy_sha256": policy.fingerprint,
                "agents": agents,
            }
