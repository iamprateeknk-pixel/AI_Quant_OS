"""Pinned audited SQLite admission before generation; unknown usage keeps its hold."""

import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.contracts import _require_aware
from ai_quant_os.jev_spend_policy import JevInputSpendPolicy
from ai_quant_os.provider_spend_policy import ProviderSpendPolicy, bounded_integer, encode
from ai_quant_os.shared_spend_policy import SharedSpendPolicy, decode_spending_policy


def _utc(now: datetime) -> datetime:
    _require_aware(now)
    return now.astimezone(timezone.utc)


class ProviderSpendStore:
    def __init__(self, path: Path, policy: ProviderSpendPolicy | SharedSpendPolicy | None) -> None:
        self.path = Path(path).resolve()
        self.pin = self.path.with_suffix(self.path.suffix + ".pin.json")
        created = not self.path.exists() and not self.pin.exists()
        if created:
            if type(policy) not in (ProviderSpendPolicy, JevInputSpendPolicy, SharedSpendPolicy):
                raise ValueError("Original spending policy required")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            anchor = {
                "schema_version": "1.0",
                "budget_id": uuid4().hex,
                "policy_sha256": policy.fingerprint,
            }
            with self.pin.open("xb") as output:
                output.write(encode(anchor))
                output.flush()
                os.fsync(output.fileno())
            # An incomplete creation remains unavailable, never silently reinitialized.
            with self.path.open("xb"):
                pass
        with self.pin.open("rb") as source:
            raw = source.read(257)
        if len(raw) > 256:
            raise ValueError("Invalid spending pin")
        self.anchor = json.loads(raw)
        if (
            type(self.anchor) is not dict
            or set(self.anchor) != {"schema_version", "budget_id", "policy_sha256"}
            or self.anchor["schema_version"] != "1.0"
            or not isinstance(self.anchor["budget_id"], str)
            or not re.fullmatch("[a-f0-9]{32}", self.anchor["budget_id"])
            or not isinstance(self.anchor["policy_sha256"], str)
            or not re.fullmatch("[a-f0-9]{64}", self.anchor["policy_sha256"])
            or raw != encode(self.anchor)
            or not self.path.is_file()
        ):
            raise ValueError("Original spending archive unavailable")
        with closing(self._connection()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            if created:
                db.execute(
                    "CREATE TABLE audit_events(event_key TEXT PRIMARY KEY,event_json TEXT NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE spend_policy(singleton INTEGER PRIMARY KEY CHECK(singleton=1),body BLOB NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE spend_events(event_key TEXT PRIMARY KEY,body BLOB NOT NULL)"
                )
                AuditChain(db).initialize()
                db.execute("INSERT INTO spend_policy VALUES(1,?)", (policy.encode(),))
                AuditChain(db).append(
                    "spend-policy", {"action": "PROVIDER_SPEND_POLICY_PINNED", **self.anchor}
                )
            retained, _, _, _, _ = self._verified(db)
            if policy is not None and policy != retained:
                raise ValueError("Spending policy is immutable")

    def _connection(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _append(self, db, key, record):
        raw = encode(record)
        db.execute("INSERT INTO spend_events VALUES(?,?)", (key, raw))
        AuditChain(db).append(
            key, {"action": "PROVIDER_SPEND_EVENT", "sha256": sha256(raw).hexdigest()}
        )

    def _verified(self, db):
        with self.pin.open("rb") as source:
            raw_pin = source.read(257)
        if raw_pin != encode(self.anchor):
            raise ValueError("Spending pin changed")
        count, size, largest = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(body)),0),COALESCE(MAX(LENGTH(body)),0) FROM spend_events"
        ).fetchone()
        audit_count, audit_size = db.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
        ).fetchone()
        policy_count, policy_size = db.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(body)),0) FROM spend_policy"
        ).fetchone()
        if (
            count > 20000
            or size > 16000000
            or largest > 1024
            or audit_count != count + 1
            or audit_size > 512
            or policy_count != 1
            or policy_size > 65536
        ):
            raise ValueError("Spending archive capacity or inventory mismatch")
        integrity = AuditChain(db).require_valid()
        if integrity.legacy_event_count:
            raise ValueError("Unaudited spending archive")
        raw = db.execute("SELECT body FROM spend_policy WHERE singleton=1").fetchone()[0]
        policy = decode_spending_policy(raw)
        if raw != policy.encode() or policy.fingerprint != self.anchor["policy_sha256"]:
            raise ValueError("Spending policy pin mismatch")
        audits = {
            key: json.loads(raw)
            for key, raw in db.execute("SELECT event_key,event_json FROM audit_events")
        }
        if audits.get("spend-policy") != {"action": "PROVIDER_SPEND_POLICY_PINNED", **self.anchor}:
            raise ValueError("Spending policy audit mismatch")
        reservations, usage, last = {}, {}, None
        roles = {rule.agent_id for rule in policy.rules}
        for key, raw in db.execute("SELECT event_key,body FROM spend_events ORDER BY rowid"):
            record = json.loads(raw)
            if (
                type(record) is not dict
                or raw != encode(record)
                or audits.get(key)
                != {"action": "PROVIDER_SPEND_EVENT", "sha256": sha256(raw).hexdigest()}
            ):
                raise ValueError("Spending record audit mismatch")
            if not isinstance(record.get("at"), str):
                raise ValueError("Spending timestamp missing")
            stamp = _utc(datetime.fromisoformat(record["at"]))
            if stamp.isoformat() != record["at"] or (last is not None and stamp < last):
                raise ValueError("Spending clock regression")
            last = stamp
            identity = record.get("reservation_id")
            if type(identity) is not str or not re.fullmatch("[a-f0-9]{32}", identity):
                raise ValueError("Invalid spending reservation")
            if key == "reserve:" + identity:
                contract = policy.contract(record.get("model"))
                if (
                    set(record)
                    != {
                        "reservation_id",
                        "at",
                        "agent_id",
                        "agent_version",
                        "model",
                        "request_sha256",
                        "input_tokens",
                        "max_output_tokens",
                    }
                    or record["agent_id"] not in roles
                    or type(record["agent_version"]) is not str
                    or not re.fullmatch(
                        r"(?:0|[1-9]\d{0,5})(?:\.(?:0|[1-9]\d{0,5})){2}", record["agent_version"]
                    )
                    or type(record["request_sha256"]) is not str
                    or not re.fullmatch("[a-f0-9]{64}", record["request_sha256"])
                    or not contract.valid_reservation(
                        record["input_tokens"], record["max_output_tokens"]
                    )
                ):
                    raise ValueError("Invalid spending reservation fields")
                reservations[identity] = record
            elif key == "usage:" + identity:
                if (
                    set(record) != {"reservation_id", "at", "input_tokens", "output_tokens"}
                    or identity not in reservations
                    or not bounded_integer(record["input_tokens"])
                    or not bounded_integer(record["output_tokens"])
                ):
                    raise ValueError("Invalid spending usage fields")
                usage[identity] = record
            else:
                raise ValueError("Unknown spending record")
        return policy, reservations, usage, last, integrity.head_hash

    @staticmethod
    def _breached(policy, reservations, usage):
        return any(
            policy.contract(reservations[key]["model"]).exceeded(reservations[key], row)
            for key, row in usage.items()
        )

    @staticmethod
    def _totals(policy, reservations, usage, agent_id, now):
        daily_tokens = monthly_tokens = daily_money = monthly_money = pending = 0
        for key, row in reservations.items():
            if row["agent_id"] != agent_id:
                continue
            report = usage.get(key)
            contract = policy.contract(row["model"])
            inputs = report["input_tokens"] if report else row["input_tokens"]
            outputs = report["output_tokens"] if report else row["max_output_tokens"]
            tokens, money = (
                contract.tokens(inputs, outputs),
                contract.money(inputs, outputs),
            )
            pending += int(report is None)
            # Unknown older charges remain reserved in every later period until settled.
            if report is None or row["at"][:10] == now.date().isoformat():
                daily_tokens += tokens
                daily_money += money
            if report is None or row["at"][:7] == now.date().isoformat()[:7]:
                monthly_tokens += tokens
                monthly_money += money
        return daily_tokens, monthly_tokens, daily_money, monthly_money, pending

    def reserve(
        self,
        *,
        agent_id: str,
        agent_version: str,
        model: str,
        request_sha256: str,
        input_tokens: int,
        max_output_tokens: int,
        now: datetime,
    ) -> str:
        now = _utc(now)
        with closing(self._connection()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            policy, reservations, usage, last, _ = self._verified(db)
            if last is not None and now < last:
                raise ValueError("Spending clock moved backwards")
            if self._breached(policy, reservations, usage):
                raise PermissionError("Provider spending breach unresolved")
            contract = policy.contract(model)
            if agent_id not in {
                rule.agent_id for rule in policy.rules
            } or not contract.valid_reservation(input_tokens, max_output_tokens):
                raise ValueError("Invalid spending admission")
            tokens = contract.tokens(input_tokens, max_output_tokens)
            money = contract.money(input_tokens, max_output_tokens)
            self._require_capacity(policy, reservations, usage, agent_id, now, tokens, money)
            if type(policy) is SharedSpendPolicy:
                selected = {key: row for key, row in reservations.items() if row["model"] == model}
                self._require_capacity(contract, selected, usage, agent_id, now, tokens, money)
            identity = uuid4().hex
            record = dict(
                reservation_id=identity,
                at=now.isoformat(),
                agent_id=agent_id,
                agent_version=agent_version,
                model=model,
                request_sha256=request_sha256,
                input_tokens=input_tokens,
                max_output_tokens=max_output_tokens,
            )
            self._append(db, "reserve:" + identity, record)
            self._verified(db)
            return identity

    def settle(
        self, reservation_id: str, *, input_tokens: int, output_tokens: int, now: datetime
    ) -> None:
        now = _utc(now)
        with closing(self._connection()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            policy, reservations, usage, last, _ = self._verified(db)
            if (
                reservation_id not in reservations
                or not bounded_integer(input_tokens)
                or not bounded_integer(output_tokens)
            ):
                raise ValueError("Invalid spending settlement")
            existing = usage.get(reservation_id)
            if existing:
                if (existing["input_tokens"], existing["output_tokens"]) != (
                    input_tokens,
                    output_tokens,
                ):
                    raise ValueError("Conflicting spending settlement")
            else:
                if last is not None and now < last:
                    raise ValueError("Spending clock moved backwards")
                self._append(
                    db,
                    "usage:" + reservation_id,
                    dict(
                        reservation_id=reservation_id,
                        at=now.isoformat(),
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    ),
                )
            _, reservations, usage, _, _ = self._verified(db)
            breach = self._breached(policy, reservations, usage)
        if breach:
            raise PermissionError("Provider spending breach retained")

    @classmethod
    def _require_capacity(cls, policy, reservations, usage, agent_id, now, tokens, money):
        rule = next(rule for rule in policy.rules if rule.agent_id == agent_id)
        daily, monthly, daily_money, monthly_money, _ = cls._totals(
            policy, reservations, usage, agent_id, now
        )
        if (
            daily + tokens > rule.daily_tokens - rule.daily_token_reserve
            or monthly + tokens > rule.monthly_tokens - rule.monthly_token_reserve
            or daily_money + money > rule.daily_nano_usd - rule.daily_money_reserve
            or monthly_money + money > rule.monthly_nano_usd - rule.monthly_money_reserve
        ):
            raise PermissionError("Provider spending budget exhausted")

    @classmethod
    def _agent_rows(cls, policy, reservations, usage, now):
        agents = []
        for rule in policy.rules:
            daily, monthly, daily_money, monthly_money, pending = cls._totals(
                policy, reservations, usage, rule.agent_id, now
            )
            agents.append(
                dict(
                    agent_id=rule.agent_id,
                    daily_tokens=daily,
                    monthly_tokens=monthly,
                    daily_nano_usd=daily_money,
                    monthly_nano_usd=monthly_money,
                    pending_count=pending,
                    unsettled_tokens=sum(
                        policy.contract(row["model"]).tokens(
                            row["input_tokens"], row["max_output_tokens"]
                        )
                        for key, row in reservations.items()
                        if row["agent_id"] == rule.agent_id and key not in usage
                    ),
                    unsettled_nano_usd=sum(
                        policy.contract(row["model"]).money(
                            row["input_tokens"], row["max_output_tokens"]
                        )
                        for key, row in reservations.items()
                        if row["agent_id"] == rule.agent_id and key not in usage
                    ),
                    daily_token_limit=rule.daily_tokens,
                    monthly_token_limit=rule.monthly_tokens,
                    daily_money_limit=rule.daily_nano_usd,
                    monthly_money_limit=rule.monthly_nano_usd,
                    daily_token_remaining=max(
                        0, rule.daily_tokens - rule.daily_token_reserve - daily
                    ),
                    monthly_token_remaining=max(
                        0, rule.monthly_tokens - rule.monthly_token_reserve - monthly
                    ),
                    daily_money_remaining=max(
                        0, rule.daily_nano_usd - rule.daily_money_reserve - daily_money
                    ),
                    monthly_money_remaining=max(
                        0, rule.monthly_nano_usd - rule.monthly_money_reserve - monthly_money
                    ),
                    daily_token_reserve=rule.daily_token_reserve,
                    monthly_token_reserve=rule.monthly_token_reserve,
                    daily_money_reserve=rule.daily_money_reserve,
                    monthly_money_reserve=rule.monthly_money_reserve,
                )
            )
        return agents

    def status(self, now: datetime, *, model: str | None = None) -> dict[str, object]:
        now = _utc(now)
        with closing(self._connection()) as db:
            db.execute("BEGIN")
            policy, reservations, usage, last, head = self._verified(db)
            if last is not None and now < last:
                raise ValueError("Spending clock moved backwards")
            state = dict(
                state="BREACHED" if self._breached(policy, reservations, usage) else "ENFORCED",
                day=now.date().isoformat(),
                month=now.date().isoformat()[:7],
                model=policy.model,
                accounting=policy.accounting,
                policy_sha256=policy.fingerprint,
                head_hash=head,
                agents=self._agent_rows(policy, reservations, usage, now),
            )
            if type(policy) is SharedSpendPolicy:
                state["models"] = [
                    {
                        "model": row.model,
                        "accounting": row.accounting,
                        "policy_sha256": row.fingerprint,
                    }
                    for row in policy.models
                ]
            if model is not None:
                contract = policy.contract(model)
                selected = {key: row for key, row in reservations.items() if row["model"] == model}
                state.update(
                    admitted_model=contract.model,
                    model_accounting=contract.accounting,
                    model_policy_sha256=contract.fingerprint,
                    model_agents=self._agent_rows(contract, selected, usage, now),
                )
            return state
