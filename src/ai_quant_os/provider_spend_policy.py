"""Immutable per-role token and declared-rate spending ceilings; no invoice claim."""

import json
import re
from dataclasses import asdict, dataclass
from hashlib import sha256

from ai_quant_os.agent_permissions import ROLE_CEILINGS


def encode(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def bounded_integer(value: object, maximum: int = 10**9) -> bool:
    return type(value) is int and 0 <= value <= maximum


@dataclass(frozen=True)
class SpendRule:
    agent_id: str
    daily_tokens: int
    monthly_tokens: int
    daily_token_reserve: int
    monthly_token_reserve: int
    daily_nano_usd: int
    monthly_nano_usd: int
    daily_money_reserve: int
    monthly_money_reserve: int

    def __post_init__(self) -> None:
        if type(self.agent_id) is not str or self.agent_id not in ROLE_CEILINGS:
            raise ValueError("Invalid spending role")
        for daily, monthly, daily_reserve, monthly_reserve, bound in (
            (
                self.daily_tokens,
                self.monthly_tokens,
                self.daily_token_reserve,
                self.monthly_token_reserve,
                10**9,
            ),
            (
                self.daily_nano_usd,
                self.monthly_nano_usd,
                self.daily_money_reserve,
                self.monthly_money_reserve,
                10**15,
            ),
        ):
            if (
                not all(
                    bounded_integer(value, bound)
                    for value in (daily, monthly, daily_reserve, monthly_reserve)
                )
                or not daily <= monthly
                or not daily_reserve <= daily
                or not monthly_reserve <= monthly
                or not daily_reserve <= monthly_reserve
            ):
                raise ValueError("Invalid spending ceiling or protected reserve")


@dataclass(frozen=True)
class ProviderSpendPolicy:
    model: str
    input_nano_usd: int
    output_nano_usd: int
    rate_reference: str
    rules: tuple[SpendRule, ...]

    def __post_init__(self) -> None:
        if (
            type(self.model) is not str
            or not re.fullmatch(r"gpt-[a-z0-9.-]+-\d{4}-\d{2}-\d{2}", self.model)
            or not bounded_integer(self.input_nano_usd)
            or not bounded_integer(self.output_nano_usd)
            or min(self.input_nano_usd, self.output_nano_usd) < 1
            or type(self.rate_reference) is not str
            or not 1 <= len(self.rate_reference) <= 256
            or any(ord(char) < 32 for char in self.rate_reference)
            or type(self.rules) is not tuple
            or any(type(row) is not SpendRule for row in self.rules)
            or len(self.rules) != len(ROLE_CEILINGS)
            or {row.agent_id for row in self.rules} != set(ROLE_CEILINGS)
        ):
            raise ValueError("Pinned model, declared rates and all role ceilings required")
        object.__setattr__(self, "rules", tuple(sorted(self.rules, key=lambda row: row.agent_id)))

    def encode(self) -> bytes:
        return encode({"schema_version": "1.0", **asdict(self)})

    @property
    def fingerprint(self) -> str:
        return sha256(self.encode()).hexdigest()

    @classmethod
    def decode(cls, raw: bytes) -> "ProviderSpendPolicy":
        if type(raw) is not bytes or len(raw) > 65536:
            raise ValueError("Spending policy exceeds capacity")

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate spending policy field")
                result[key] = value
            return result

        value = json.loads(raw, object_pairs_hook=unique)
        if (
            type(value) is not dict
            or value.pop("schema_version", None) != "1.0"
            or type(value.get("rules")) is not list
        ):
            raise ValueError("Invalid spending policy")
        try:
            value["rules"] = tuple(SpendRule(**row) for row in value["rules"])
            return cls(**value)
        except (TypeError, KeyError):
            raise ValueError("Invalid spending policy fields") from None
