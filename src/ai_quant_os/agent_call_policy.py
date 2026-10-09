"""Immutable UTC call ceilings shared across agent versions."""

import json
from dataclasses import asdict, dataclass
from hashlib import sha256

from ai_quant_os.agent_permissions import ROLE_CEILINGS


@dataclass(frozen=True)
class AgentCallRule:
    agent_id: str
    daily_calls: int
    monthly_calls: int
    daily_recovery_reserve: int
    monthly_recovery_reserve: int

    def __post_init__(self):
        values = (
            self.daily_calls,
            self.monthly_calls,
            self.daily_recovery_reserve,
            self.monthly_recovery_reserve,
        )
        if (
            not isinstance(self.agent_id, str)
            or self.agent_id not in ROLE_CEILINGS
            or any(type(value) is not int or not 0 <= value <= 100000 for value in values)
            or self.daily_calls > self.monthly_calls
            or self.daily_recovery_reserve > self.daily_calls
            or self.monthly_recovery_reserve > self.monthly_calls
            or self.daily_recovery_reserve > self.monthly_recovery_reserve
        ):
            raise ValueError("Invalid agent call ceiling or recovery reserve")


@dataclass(frozen=True)
class AgentCallPolicy:
    rules: tuple[AgentCallRule, ...]

    def __post_init__(self):
        if type(self.rules) is not tuple or any(
            type(row) is not AgentCallRule for row in self.rules
        ):
            raise ValueError("Immutable typed call rules required")
        if len(self.rules) != len(ROLE_CEILINGS) or {row.agent_id for row in self.rules} != set(
            ROLE_CEILINGS
        ):
            raise ValueError("Exactly one call policy per registered agent role required")
        object.__setattr__(self, "rules", tuple(sorted(self.rules, key=lambda row: row.agent_id)))

    def encode(self) -> bytes:
        return json.dumps(
            {"schema_version": "1.0", "rules": [asdict(row) for row in self.rules]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    @property
    def fingerprint(self) -> str:
        return sha256(self.encode()).hexdigest()

    @classmethod
    def decode(cls, raw: bytes) -> "AgentCallPolicy":
        if type(raw) is not bytes or len(raw) > 65536:
            raise ValueError("Call policy exceeds byte bound")

        def unique(pairs):
            values = {}
            for key, value in pairs:
                if key in values:
                    raise ValueError("Duplicate call policy field")
                values[key] = value
            return values

        value = json.loads(raw, object_pairs_hook=unique)
        if (
            type(value) is not dict
            or set(value) != {"schema_version", "rules"}
            or value["schema_version"] != "1.0"
            or type(value["rules"]) is not list
        ):
            raise ValueError("Invalid call policy envelope")
        fields = {
            "agent_id",
            "daily_calls",
            "monthly_calls",
            "daily_recovery_reserve",
            "monthly_recovery_reserve",
        }
        if any(type(row) is not dict or set(row) != fields for row in value["rules"]):
            raise ValueError("Invalid call policy rule fields")
        return cls(tuple(AgentCallRule(**row) for row in value["rules"]))
